"""Uma conexao por rele, compartilhada pelos diagramas que a pedem.

Um rele SEL aceita poucas sessoes simultaneas, entao a conexao nao pode ser do
diagrama: e' do PROCESSO, chaveada por `ip:porta` e contada por referencia. O
segundo diagrama que pedir o mesmo rele entra na conexao existente; ela so cai
quando o ultimo solta.

O `LiveState` mora aqui, e nao no diagrama, porque quem escreve nele sao as
threads de polling -- uma por conexao. Dois diagramas do mesmo rele leem o
mesmo estado, que e' o certo: a Relay Word e' do rele.

O PROTOCOLO nao mora aqui. `RelayLink` e' a casca: identidade, refcount,
`LiveState`, watchdog e a thread de polling. Quem sabe falar com o rele e' o
transporte (`transport/telnet.py`, e amanha o de MMS), atras do Protocol de
`transport/__init__.py`. A casca nunca pergunta qual transporte e': quando
precisa abortar um setup travado, ela chama `transport.abort()`, porque como
se acorda uma leitura pendurada e' coisa do protocolo -- no telnet, fechar o
socket e' a unica coisa que faz o selprotopy levantar.

Quem trava o que:

    LinkPool._lock            o mapa `key -> RelayLink` e TODA transicao de
                              `owners`. Nunca segurado durante
                              connect()/close()/descoberta.
    RelayLink._lock           o transporte, a thread de polling, o stop event
                              e o flag `_closed`. E' o lock de CICLO DE VIDA:
                              so' se segura por operacoes curtas, nunca por
                              uma conversa com o rele.
    RelayLink._discovery_lock uma descoberta por vez. E' este, e nao o de
                              cima, que fica preso durante a varredura de
                              bits -- que num 3xx frio custa ~90s de `TAR`.
    LiveState.lock            os valores.

Foi assim que `Desconectar` deixou de parecer travado: `prepare_bits` segurava
o `_lock` durante a `transport.prepare_bits(...)` inteira, entao um
`POST /disconnect` -> `pool.release` -> `link.close()` ficava BLOQUEADO ali
ate' a descoberta acabar. Separar os dois locks reabre a corrida que o
`pause_polling` tinha (o restart no `finally` acontece fora do `_lock`), e por
isso o `_closed` entrou no mesmo movimento: `close()` o marca sob o `_lock` e
`_start_polling` o confere, recusando subir leitor num link fechado -- a mesma
recusa que ele ja faz por thread zumbi.

E o refcount, que e' onde mora o risco: `owners` e' um CONJUNTO de ids de
diagrama, e nao um inteiro. Adicionar ou remover duas vezes o mesmo dono e'
idempotente, entao um duplo clique em Conectar/Desconectar nao consegue fechar
o telnet debaixo de um diagrama vivo nem deixar conexao pendurada.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from pacct.web.glv.poll import FirstTimeLog
from pacct.web.glv.state import LiveState
from pacct.web.glv.transport import (  # noqa: F401  (re-exportados)
    MODE_FAST_METER,
    MODE_TAR,
    MODE_TARGET,
    SCAN_TELNET,
    pick_transport,
)

# Teto do setup (telnet + login + autoconfig). Nao cobre a descoberta de bits,
# que leva minutos de propria vontade num FID sem cache.
SETUP_TIMEOUT = 60.0

# Quanto esperar a thread de polling morrer. Tem que ser MAIOR que a volta mais
# longa de um loop de poll: uma leitura fica ate `RESPONSE_DEADLINE_S` (3.0s) +
# `DRAIN_DEADLINE_S` (0.3s) de `poll.py` dentro de uma espera que nao olha o
# stop event. Com os 2.0s de antes, o join voltava com a thread AINDA VIVA e o
# `_start_polling` seguinte punha um segundo leitor no mesmo telnet -- que e'
# exatamente o que parar o polling existe pra evitar.
POLL_JOIN_TIMEOUT = 4.0
# Quanto `_poll_gave_up` espera pelo `_lock` antes de desistir dele. Tem que
# ser MUITO menor que o join acima: quem faz o join segura o `_lock` durante
# ele, entao esperar mais que isso e' esperar o proprio join estourar.
GIVE_UP_LOCK_TIMEOUT = 0.25


class TooManyLinks(RuntimeError):
    """Teto de conexoes simultaneas atingido."""


class PollingWedged(RuntimeError):
    """A volta de polling anterior nao morreu, e por isso ninguem mais pode
    falar com o rele agora: duas conversas no mesmo telnet se embaralham."""


def _default_transport(*, ip, port, acc_password, relay_model, logger):
    """O transporte de sempre: SEL Fast Message por telnet.

    Fabrica, e nao instancia, porque so em `connect()` se conhece a senha e o
    modelo do rele -- o `LinkPool` cria o link antes disso.
    """
    return pick_transport(SCAN_TELNET, ip=ip, port=port,
                          acc_password=acc_password, relay_model=relay_model,
                          logger=logger)


class RelayLink:
    """Uma conexao com um rele, compartilhada por N diagramas."""

    def __init__(self, ip: str, port: int, logger, pool=None, transport=None,
                 make_transport=None):
        self.ip = ip
        self.port = port
        self.pool = pool
        self.key = f"{ip}:{port}"
        self.logger = logger
        self.state = LiveState()
        # O transporte pode chegar pronto (testes, um modo escolhido na tela)
        # ou ser construido em connect(), quando a senha e o modelo aparecem.
        self.transport = transport
        self._make_transport = make_transport or _default_transport
        self.fid = ""
        self.devid = ""
        self.mode = MODE_TARGET
        self.error = ""
        self._connected = False
        # Setado quando connect() termina, com ou sem erro. Quem entra numa
        # conexao ja existente espera aqui em vez de abrir um segundo telnet.
        self.ready = threading.Event()
        # ids de diagrama; refs == len(owners). So o LinkPool mexe.
        self.owners: set = set()
        self._lock = threading.RLock()
        # Uma descoberta por vez, e SO' isso -- separado do `_lock` de ciclo
        # de vida porque uma varredura de bits fala com o rele por minutos.
        self._discovery_lock = threading.RLock()
        # Marcado por `close()`. Um link fechado nao sobe leitor nenhum, nem
        # pelo `finally` de um `pause_polling` que estava em voo.
        self._closed = False
        # Diagnosticos que valem uma vez por CONEXAO. Mora aqui, e nao nas
        # funcoes de poll, porque a GLV abre N diagramas sobre N reles: um
        # flag no modulo dava os logs ao primeiro rele conectado depois do
        # restart e silencio a todos os outros.
        self._once = FirstTimeLog(logger)
        self._poll_thread = None
        self._poll_stop = None
        # Thread de polling que nao morreu dentro do join. Enquanto viva,
        # nenhuma outra sobe.
        self._poll_dying = None
        self._poll_interval = 0.5
        # owner -> bits da pagina aberta naquele diagrama. O modo TAR (3xx) le
        # so o que esta na tela; com dois diagramas no mesmo rele, um apagaria
        # a lista do outro se nao fosse a uniao.
        self._wanted: dict = {}

    # -- ciclo de vida (so o LinkPool cria e destroi) ------------------------

    def start_connect(self, **kwargs) -> None:
        """Conecta na thread DO LINK, e nao na de quem pediu.

        Um `selprotopy` travado numa leitura nao acorda nem fechando o socket
        (ele engole a excecao e tenta de novo), entao a thread de quem pediu
        nunca voltaria pra soltar a referencia. Aqui quem trava e' esta thread,
        que nao e' dona de nada: os diagramas esperam em `ready`, que o
        watchdog seta mesmo quando o setup fica pendurado.
        """
        threading.Thread(target=self.connect, kwargs=kwargs, daemon=True,
                         name=f"glv-link-{self.key}").start()

    def connect(self, *, relay_model=None, poll_interval: float,
                acc_password: str = "", job=None,
                setup_timeout: float = SETUP_TIMEOUT) -> None:
        """Conecta, descobre os bits do rele e sobe o polling.

        Nao levanta: falha vira `self.error`, e quem pediu decide o que fazer.
        O diagrama continua aberto e desconectado, com o motivo no badge -- o
        mesmo que o setup fazia quando caia pra "modo desenho".

        `setup_timeout` cobre so o setup (telnet + login + autoconfig), e nao
        a descoberta de bits, que num FID sem cache leva minutos de propria
        vontade. Sem ele, um peer que aceita a conexao e nunca responde deixa
        o diagrama em "conectando" pra sempre -- e segurando uma das vagas do
        teto de conexoes.
        """
        logger = self.logger
        try:
            self._poll_interval = poll_interval
            if self.transport is None:
                self.transport = self._make_transport(
                    ip=self.ip, port=self.port, acc_password=acc_password,
                    relay_model=relay_model, logger=logger)
            self.mode = self.transport.mode
            if job:
                job.stage("Conectando ao rele...", 8)
            self._connect_with_watchdog(setup_timeout, job)
            with self._lock:
                self._connected = True
            self.fid = self.transport.fid or ""
            self.devid = self.transport.devid or ""
            logger.info("[glv] %s conectado. FID=%s", self.key, self.fid)
            if job:
                # Sem percentual DE PROPOSITO: num 4xx/3xx o setup ja passou
                # pelo `_setup_ascii_reader`, que reporta 30, entao um 20 aqui
                # fazia a barra ANDAR PRA TRAS (8 -> 30 -> 20 -> 70). `None`
                # troca o texto e deixa a barra onde ela esta'.
                job.stage(f"Conectado ({self.fid or 'rele'})", None)
            self._start_polling()
            logger.info("[glv] %s: polling no modo %s", self.key, self.mode)
        except Exception as e:
            # Failsafe: IP que nao responde, timeout, recusa de conexao, falha
            # de autoconfig. O diagrama segue aberto, so que desconectado.
            if not self.error:      # o watchdog pode ter chegado primeiro
                self.error = f"sem conexão com {self.key}: {e}"
            logger.warning("[glv] falha ao conectar em %s: %s", self.key, e)
            self._close_transport()
        finally:
            self.ready.set()

    def _connect_with_watchdog(self, timeout: float, job=None) -> None:
        """`transport.connect` com um cronometro que o aborta se ele travar.

        O cronometro e' generico; ABORTAR e' do transporte. No telnet nao da
        pra interromper uma leitura bloqueada de fora, e fechar o socket faz
        ela levantar -- que e' o que queremos.

        O prazo cobre o SETUP, e nao a descoberta de bits: quem diz onde uma
        acaba e a outra comeca e' o proprio transporte, pelo `setup_done`. Sem
        esse evento o prazo cobre o `connect()` inteiro.
        """
        timed_out = threading.Event()
        setup_done = getattr(self.transport, "setup_done", None)

        def abort():
            if setup_done is not None and setup_done.is_set():
                return          # o setup passou; a descoberta nao tem prazo
            timed_out.set()
            self.logger.warning(
                "[glv] %s nao respondeu em %.0fs no setup -- abortando.",
                self.key, timeout)
            self.transport.abort()
            # Fechar o socket nem sempre acorda a leitura: o selprotopy tenta
            # de novo e pode ficar pendurado. Entao liberamos quem espera e
            # devolvemos a vaga do teto aqui mesmo, sem depender daquela
            # thread voltar.
            self.error = (f"o rele em {self.key} aceitou a conexao mas nao "
                          f"respondeu em {timeout:.0f}s")
            self.ready.set()
            pool = self.pool
            if pool is not None:
                pool.abandon(self)

        watchdog = threading.Timer(timeout, abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            return self.transport.connect(job)
        except Exception:
            if timed_out.is_set():
                raise TimeoutError(self.error) from None
            raise
        finally:
            watchdog.cancel()

    def close(self) -> None:
        """Para o polling e fecha a conexao. Chamado pelo pool, fora do lock dele.

        Nao esperamos a thread zumbi (a que sobreviveu ao join) morrer: ela e'
        daemon, e dar outro prazo a quem ja ignorou um stop so atrasaria o
        Desconectar. Fechar a conexao e' o que a mata de verdade -- a leitura
        dela levanta -- e o link sai do pool logo depois, entao ninguem mais
        chega a este objeto.
        """
        with self._lock:
            self._closed = True
            transport = self.transport
        # Quebra uma descoberta EM VOO antes de tentar parar qualquer coisa.
        # Sem isto, desconectar no meio de um `TAR` frio (~90s medidos num
        # 3xx) ou de uma busca de layouts MMS so' voltava quando a conversa
        # com o rele acabasse por conta propria -- na tela, o botao parecia
        # morto. `abort()` e' do transporte porque ACORDAR uma leitura pendurada
        # e' coisa do protocolo: no telnet, fechar o socket e' a unica coisa
        # que faz o selprotopy levantar.
        if transport is not None:
            try:
                transport.abort()
            except Exception:
                pass
        with self._lock:
            self._stop_polling()
        self._close_transport()
        with self._lock:
            self._reap_dying()
        self.state.clear()

    def _close_transport(self) -> None:
        with self._lock:
            self._connected = False
            transport = self.transport
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    # -- descoberta ---------------------------------------------------------

    def prepare_bits(self, names, job=None) -> int:
        """Descobre/mapeia bits, com o polling parado ENQUANTO PRECISAR.

        Parar e' obrigatorio no telnet: e' um stream so, e intercalar
        `TAR <nome>` com o pipeline de Fast Meter embaralha as duas respostas.
        No MMS era pela mesma razao de fundo -- o cliente da py61850 nao e'
        thread-safe (um socket, um contador de invoke) -- mas hoje o transporte
        MMS nao pede o `pause`: desde que a leitura passou a ser por folha, o
        `prepare_bits` dele nao fala mais com o rele, e mapear os bits de um
        segundo diagrama nao tira uma volta do leitor do primeiro.

        E' tambem o que faz um SEGUNDO diagrama funcionar numa conexao que ja
        existe: ele traz bits que ninguem pediu ao rele ainda.

        Quem para e' a casca (a thread e' dela), mas QUANDO parar e' do
        transporte: quem sabe se vai mesmo falar com o rele e' ele. Por isso o
        `pause` vai como argumento em vez de embrulhar a chamada -- num 7xx os
        digitais vem do Fast Meter e nao ha o que descobrir, e num FID com
        cache completo nao ha bit faltando. Nesses casos a chamada volta sem
        entrar no `pause`, e a thread de polling nem fica sabendo. Embrulhar
        aqui derrubava e subia o leitor a cada conexao de 751, de graca.
        """
        with self._discovery_lock:
            with self._lock:
                transport, closed = self.transport, self._closed
            if transport is None or closed:
                return 0
            return transport.prepare_bits(names, job=job,
                                          pause=self.pause_polling)

    @contextmanager
    def pause_polling(self):
        """Para o polling na entrada e sobe de novo na saida, se estava rodando.

        Uma thread que ja ignorou um stop nao para: `_poll_dying` guarda a que
        sobreviveu ao join, e enquanto ela viver ninguem fala com o rele. Sem
        esta checagem o buraco voltava pelo outro lado -- com o `_poll_thread`
        ja zerado, `was_polling` dava False, o pause nao fazia nada, e a
        descoberta ia pro mesmo socket que a zumbi ainda esta lendo. Nao da pra
        pausar quem nao ouve: so da pra RECUSAR, e dizer no log.
        """
        with self._lock:
            dying = self._reap_dying()
            if dying is not None:
                self.logger.warning(
                    "[glv] %s: a volta de polling anterior ainda nao terminou; "
                    "recusando falar com o rele agora.", self.key)
                raise PollingWedged(
                    f"a leitura anterior de {self.key} não terminou; "
                    f"desconecte e conecte o diagrama de novo")
            was_polling = self._poll_thread is not None
            if was_polling:
                self._stop_polling()
        try:
            yield
        finally:
            if was_polling:
                self._start_polling()

    def _reap_dying(self):
        """Esquece a thread zumbi se ela finalmente morreu; devolve a que
        ainda estiver viva."""
        dying = self._poll_dying
        if dying is not None and not dying.is_alive():
            self._poll_dying = dying = None
        return dying

    def ensure_bits(self, names, job=None) -> int:
        return self.prepare_bits(names, job=job)

    # -- polling ------------------------------------------------------------

    def _start_polling(self) -> None:
        # TUDO sob o `_lock`: ele e' reentrante, entao `set_poll_interval`
        # segue atomico, e quem chama de fora (o `finally` do `pause_polling`,
        # o `connect()`) passa a ler `_closed` sem corrida.
        with self._lock:
            # Um link fechado nao sobe leitor. E' a outra metade da separacao
            # do `_discovery_lock`: agora que `close()` nao espera mais a
            # descoberta terminar, ele pode chegar ANTES do restart que o
            # `pause_polling` faz no `finally` -- e sem esta guarda esse
            # restart poria uma thread nova num transporte ja fechado.
            if self._closed:
                self.logger.info(
                    "[glv] %s: link fechado; nao subo polling.", self.key)
                return
            # Um leitor por conexao. Se a volta anterior nao morreu dentro do
            # join, subir outra poe duas threads no mesmo telnet e no mesmo
            # `FastMessageChannel` memoizado, e as respostas se embaralham.
            # Melhor ficar sem leitura (e dizer isso no log) do que ler errado.
            if self._reap_dying() is not None:
                self.logger.warning(
                    "[glv] %s: a volta de polling anterior ainda nao terminou; "
                    "nao subo um segundo leitor no mesmo telnet.", self.key)
                return
            if self.transport is None:
                return
            stop = threading.Event()
            thread = threading.Thread(
                target=self._poll_runner,
                args=(stop, self._poll_interval),
                daemon=True, name=f"glv-poll-{self.key}")
            self._poll_stop, self._poll_thread = stop, thread
            thread.start()

    def _poll_runner(self, stop, interval: float) -> None:
        """O loop do transporte, mais o que fazer se ele DESISTIR.

        Os tres loops de telnet marcam `state.error` e continuam girando; o do
        MMS encerra a volta num `Iec61850Error`, porque uma associacao caida
        nao volta sozinha e insistir num socket morto so' enche o log. A frase
        da spec ("um erro de leitura marca `state.error` e para o loop, como o
        telnet faz") esta' errada sobre o telnet, e a implementacao seguiu as
        palavras.

        Parar nao e' o problema -- o problema e' parar em SILENCIO: o link
        seguia com `_connected = True`, a aba continuava LIVE com "Desconectar",
        e `state.digitals` ficava CONGELADO na ultima leitura, com o SVG
        pintando aquelas cores debaixo de um badge vermelho. Congelado-e-velho
        e' o mais perto que esta branch chega de mostrar na tela um valor que
        ninguem leu.
        """
        try:
            self.transport.poll(self.state, interval, stop, self._once)
        except Exception as e:      # o transporte nao deve deixar vazar
            with self.state.lock:
                self.state.error = f"leitura: {e}"
            self.logger.exception("[glv] %s: a thread de leitura morreu",
                                  self.key)
        if stop.is_set():
            return                  # parada pedida: nao ha' o que anunciar
        self._poll_gave_up(stop)

    def _poll_gave_up(self, stop) -> None:
        """A leitura terminou sozinha: o diagrama volta a indeterminado.

        O motivo (o `state.error` que o loop deixou) e' preservado, entao a
        tela mostra POR QUE parou em vez de so' apagar. Quem chegar depois --
        um `close()`, ou um restart que ja trocou o stop event -- nao mexe em
        nada.
        """
        # NAO espera pelo `_lock`. Quem o tiver neste instante esta' fazendo
        # uma de duas coisas, e as duas ja tornam esta atualizacao sem efeito:
        # `close()` (que poe `_closed`) ou um `_stop_polling` seguido de
        # restart (que troca o `_poll_stop`) -- exatamente as duas guardas
        # logo abaixo. E esperar seria pior que inutil: `_stop_polling` faz o
        # `join` DENTRO do lock, entao a espera daqui vira o join estourando.
        # Medido sob interleaving forcado: 4,00 s de Desconectar travado (o
        # POLL_JOIN_TIMEOUT inteiro), terminando no aviso de leitor emperrado
        # -- que e' o sintoma que a separacao do `_discovery_lock` tinha
        # acabado de remover, voltando por uma porta mais estreita.
        if not self._lock.acquire(timeout=GIVE_UP_LOCK_TIMEOUT):
            self.logger.info(
                "[glv] %s: a leitura parou, mas o link ja esta sendo fechado "
                "ou reiniciado por outra thread; deixo com ela.", self.key)
            return
        try:
            if self._closed or self._poll_stop is not stop:
                return
            self._connected = False
            self._poll_stop = self._poll_thread = None
        finally:
            self._lock.release()
        reason = self.state.snapshot().get("error") or "a leitura parou"
        self.state.clear()
        with self.state.lock:
            self.state.error = reason
        self.error = reason
        self.logger.warning(
            "[glv] %s: a leitura parou (%s); o diagrama fica desconectado e "
            "tudo indeterminado.", self.key, reason)

    def set_poll_interval(self, seconds: float) -> bool:
        """Troca o periodo de polling em voo, reiniciando a thread se ela
        estiver rodando -- e' a unica forma de mudar o `interval` que uma
        thread ja iniciada recebeu por argumento e nunca mais releu.

        O stop-e-restart acontece TODO dentro de `self._lock`, sem soltar no
        meio: e' o que impede `close()` de entrar entre o stop e o restart e
        ver "nada pra parar", deixando o restart daqui subir uma thread nova
        sobre um transporte que `close()` ja fechou por baixo. `close()`
        tambem toma este lock pra parar o polling, entao os dois nunca
        interlacam -- um espera o outro terminar a operacao inteira.

        Mesma guarda de `pause_polling`: uma volta que sobreviveu ao join
        continua dona do telnet/socket, entao nao subimos um segundo leitor
        por cima dela. Nesse caso o novo periodo so vale a partir da proxima
        vez que o polling subir por conta propria.

        Devolve True se reiniciou o polling AGORA (o novo periodo ja vale);
        False se so guardou o valor pra quando o polling subir de novo por
        conta propria (nada rodando agora, ou leitura zumbi que ainda nao
        morreu).
        """
        with self._lock:
            self._poll_interval = seconds
            dying = self._reap_dying()
            applies_now = dying is None and self._poll_thread is not None
            if applies_now:
                self._stop_polling()
                self._start_polling()
            return applies_now

    @property
    def poll_interval(self) -> float:
        """O periodo configurado agora, em segundos -- pra quem precisa
        mostrar o valor em vigor sem torcer o braco de `_poll_interval`."""
        return self._poll_interval

    def _stop_polling(self) -> None:
        stop, thread = self._poll_stop, self._poll_thread
        self._poll_stop = self._poll_thread = None
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=POLL_JOIN_TIMEOUT)
            if thread.is_alive():
                # Guardada pro `_start_polling` recusar: uma thread que nao
                # morreu continua sendo dona do telnet.
                self._poll_dying = thread
                self.logger.warning(
                    "[glv] %s: a thread de polling nao terminou em %.1fs.",
                    self.key, POLL_JOIN_TIMEOUT)

    # -- estado -------------------------------------------------------------

    def set_wanted_bits(self, owner: str, bits) -> None:
        """Bits da pagina aberta num diagrama. Publica a UNIAO dos donos."""
        with self._lock:
            if bits:
                self._wanted[owner] = set(bits)
            else:
                self._wanted.pop(owner, None)
            union = set()
            for s in self._wanted.values():
                union |= s
        self.state.set_wanted_bits(union)

    @property
    def connected(self) -> bool:
        return self._connected

    def info(self) -> dict:
        return {
            "ip": self.ip, "port": self.port, "key": self.key,
            "fid": self.fid, "devid": self.devid, "mode": self.mode,
            "refs": len(self.owners), "connected": self.connected,
            "error": self.error,
        }

class LinkPool:
    """Mapa `ip:porta -> RelayLink` do processo."""

    def __init__(self, logger, max_links: int = 4):
        self.logger = logger
        self.max_links = max_links
        self._links: dict[str, RelayLink] = {}
        self._lock = threading.RLock()

    def acquire(self, ip: str, port: int, owner: str,
                make_transport=None) -> tuple[RelayLink, bool]:
        """Devolve `(link, criei_agora)`.

        `criei_agora=True` significa que quem chamou tem que rodar
        `link.connect(...)`; `False`, que basta esperar `link.ready`. Levanta
        TooManyLinks quando uma chave NOVA estouraria o teto -- entrar numa
        conexao existente nunca estoura, porque nao custa nada ao rele.

        `make_transport` e' a fabrica que o link vai usar em `connect()`.
        Ausente, e' o telnet de sempre -- e' por aqui que o modo de varredura
        entra no caminho vivo, e nao so nos testes.
        """
        key = f"{ip}:{port}"
        with self._lock:
            link = self._links.get(key)
            if link is not None:
                link.owners.add(owner)
                self.logger.info("[glv] %s: %s entrou na conexao (%d diagrama(s))",
                                 key, owner, len(link.owners))
                return link, False
            if len(self._links) >= self.max_links:
                raise TooManyLinks(
                    f"limite de {self.max_links} conexões simultâneas atingido; "
                    f"desconecte outro diagrama antes")
            link = RelayLink(ip, port, self.logger, pool=self,
                             make_transport=make_transport)
            link.owners.add(owner)
            self._links[key] = link
            self.logger.info("[glv] %s: conexao nova pedida por %s", key, owner)
            return link, True

    def release(self, link: RelayLink, owner: str) -> None:
        """Tira `owner` do link. Zerando, fecha -- fora do lock."""
        with self._lock:
            link.owners.discard(owner)
            if link.owners:
                self.logger.info("[glv] %s continua com %d diagrama(s)",
                                 link.key, len(link.owners))
                link.set_wanted_bits(owner, set())
                return
            # Sai do mapa DENTRO do lock: um acquire() concorrente nao pode
            # entrar num link que esta fechando.
            self._links.pop(link.key, None)
        link.set_wanted_bits(owner, set())
        link.close()
        self.logger.info("[glv] %s fechado (ultimo diagrama saiu)", link.key)

    def abandon(self, link: RelayLink) -> None:
        """Tira do mapa um link cujo setup travou, sem esperar os donos.

        Os `release()` que chegarem depois continuam validos: eles so mexem em
        `owners` e num `pop` que ja nao encontra nada.
        """
        with self._lock:
            if self._links.get(link.key) is link:
                self._links.pop(link.key, None)
                self.logger.warning(
                    "[glv] %s tirado do pool (setup travado); a vaga volta pro "
                    "teto de %d conexoes", link.key, self.max_links)

    def snapshot(self) -> list:
        with self._lock:
            return [lk.info() for lk in self._links.values()]
