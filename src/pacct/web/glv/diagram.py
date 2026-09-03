"""Um diagrama aberto: um GLE renderizado que pode ou nao estar conectado.

E' o que antes eram os atributos de CLASSE do `DashboardHandler` -- um
diagrama por processo. Agora sao N, cada um com o seu par (GLE + rele).

Um diagrama nasce desconectado: `build_diagram()` nao toca na rede. Conectar
e' `connect_async()`, que dispara uma thread porque a descoberta de bits do
`AsciiTargetReader` num FID sem cache leva minutos e nao pode segurar a
resposta HTTP. Falha de conexao NAO derruba o diagrama: ele continua aberto e
desconectado, com o motivo no badge.
"""

from __future__ import annotations

import re
import threading

from pacct.parsers.gle import parse_gle, render_page
from pacct.web.glv.connectors import extract as extract_connectors
from pacct.web.glv.connectors import nets_on_page as connectors_on_page
from pacct.web.glv.gle_pages import (
    collect_analog_symbols_per_page,
    collect_bit_names,
    collect_bits_per_page,
    list_pages,
)
from pacct.web.glv.link import TooManyLinks
from pacct.web.glv.notes import NOTES, note_key
from pacct.web.glv.state import LiveState
from pacct.web.glv.transport import SCAN_MMS, SCAN_TELNET, pick_transport
from pacct.web.progress import REGISTRY, JobReporter

# Status de um diagrama, o que a bolinha da aba mostra.
IDLE = "idle"
CONNECTING = "connecting"
LIVE = "live"
ERROR = "error"


class GlvDiagram:
    """Um GLE renderizado + notas + (talvez) um RelayLink."""

    def __init__(self, diagram_id: str, *, relay_name: str, gle_name: str,
                 gle_path, ip: str, port: int, relay_model, logger,
                 scan_mode: str = SCAN_TELNET, scd_sha: str | None = None,
                 scd_path=None):
        self.id = diagram_id
        self.relay_name = relay_name
        self.gle_name = gle_name
        self.gle_path = gle_path
        self.ip = ip
        self.port = port
        self.relay_model = relay_model
        self.logger = logger
        self.title = f"{relay_name} · {gle_name}"
        # Modo de leitura escolhido na tela de selecao, por diagrama -- e' o
        # que permite comparar telnet e MMS lado a lado no mesmo rele. `scd_sha`
        # e' o que o cliente mandou; `scd_path` (resolvido pelo handler, que
        # tem a sessao e a biblioteca do projeto na mao -- este objeto nao
        # tem) e' o caminho de verdade que o transporte MMS usa pra ler o SCD.
        self.scan_mode = scan_mode
        self.scd_sha = scd_sha
        self.scd_path = scd_path
        # Override explicito de periodo, pedido via `/period`. Guardado
        # independente do link estar vivo: e' o que faz um pedido feito
        # "conectando" (ou ja desconectado) valer no proximo connect() em vez
        # de ser descartado em silencio -- `_poll_interval` le isto primeiro.
        self._interval_ms: int | None = None

        self.pages_meta: list = []
        # A pagina que este diagrama esta mostrando. Mora AQUI e nao no
        # navegador porque a lista de diagramas ja e' do servidor: trocar de
        # aba busca `/meta?d=` e re-renderiza, e sem isto `meta()` devolvia
        # sempre a pagina inicial -- quem estava na pagina 7 de um GLE de 12
        # voltava pra primeira a cada ida e volta entre dois reles. Guardado
        # por diagrama, entao vale tambem depois de um F5.
        self.open_page: str = ""
        self.svgs: dict = {}
        self.bits_per_page: dict = {}
        self.analogs_per_page: dict = {}
        self.analog_groups_meta: dict = {}
        self.var_index: dict = {}
        self.all_wanted_bits: set = set()
        # As redes de conector do GLE inteiro (`label -> ConnectorNet`). Um
        # conector nao e' uma aresta no XML: e' um NOME que duas pontas
        # compartilham, e sem isto o sinal morre nele. Ver `connectors.py`.
        self.connectors: dict = {}

        self.notes = NOTES.get(note_key(relay_name))
        # O que se ve desconectado: vazio, ou com o motivo da ultima falha.
        self.idle = LiveState()
        self.link = None
        self.status = IDLE
        self.error = ""
        # Um job por diagrama. Antes era um id fixo ("glv-session"), com o
        # comentario "existe uma sessao GLV so".
        self.job_id = f"glv-connect-{diagram_id}"
        self._lock = threading.RLock()
        # Geracao da tentativa de conexao. Desconectar ou fechar incrementa,
        # o que invalida a tentativa que estiver em voo: sem isso, fechar o
        # diagrama enquanto ele conecta deixaria a thread terminar e prender
        # a conexao pra sempre -- o `self.link` ainda e' None nessa janela,
        # entao `disconnect()` nao tem o que soltar.
        self._gen = 0

    # -- estado exibido -----------------------------------------------------

    @property
    def state(self) -> LiveState:
        """O LiveState que a tela deve mostrar.

        Conectado, e' o do RelayLink (compartilhado com os outros diagramas do
        mesmo rele). Desconectado, e' o `idle`, que esta vazio -- e' assim que
        desconectar devolve tudo a indeterminado sem limpar dicionario nenhum.
        """
        link = self.link
        return link.state if link is not None else self.idle

    @property
    def connected(self) -> bool:
        """Ha' link E ele ainda esta lendo.

        As duas metades importam. Um link que DESISTIU (a leitura terminou
        sozinha -- uma associacao MMS caida nao volta por conta propria -- e o
        `_poll_gave_up` marcou o link como nao-conectado) continua pendurado
        aqui, porque solta-lo e' com o pool e o pool esta com quem tem o
        `pool` em maos. Enquanto isto respondia so' `self.link is not None`, a
        aba seguia LIVE com "Desconectar" depois do erro, e a unica saida era
        desconectar antes de reconectar.
        """
        link = self.link
        return link is not None and link.connected

    # -- conexao ------------------------------------------------------------

    def connect_async(self, pool, defaults) -> str:
        """Dispara a conexao numa thread e devolve o id do job.

        Nao pode bloquear a resposta: num FID sem cache o AsciiTargetReader
        leva minutos montando o mapa nome -> (linha, bit). Quem acompanha e' a
        barra de progresso, pelo job `glv-connect-<id>`.
        """
        dead = None
        with self._lock:
            link = self.link
            if link is not None and not link.connected:
                # Um link que desistiu nao pode barrar a reconexao: ele nao
                # esta lendo nada. Sem isto o botao dizia "Conectar" (porque
                # `connected` ja fala a verdade) e nao fazia nada, e o usuario
                # tinha que passar por Desconectar pra voltar.
                dead, self.link = link, None
            elif link is not None or self.status == CONNECTING:
                return self.job_id
            self.status = CONNECTING
            self.error = ""
            self.idle.clear()
            self._gen += 1
            gen = self._gen
        if dead is not None:
            # Fora do `_lock`: `release` pode fechar o link.
            pool.release(dead, self.id)
            self.logger.info("[glv] diagrama %s: link %s tinha desistido; "
                             "soltei antes de reconectar", self.id, dead.key)
        threading.Thread(
            target=self._connect, args=(pool, defaults, gen), daemon=True,
            name=f"glv-connect-{self.id}",
        ).start()
        return self.job_id

    def _cancelled(self, gen: int) -> bool:
        with self._lock:
            return gen != self._gen

    def _poll_interval(self, defaults) -> float:
        """Periodo inicial de polling: o override de `/period` se ja houver
        um (pedido enquanto o diagrama ainda estava conectando, ou entre duas
        conexoes -- nao pode ser descartado em silencio), senao o do modo MMS
        ([web] glv_mms_interval_ms) pra um diagrama em MMS, senao o do telnet
        de sempre."""
        if self._interval_ms is not None:
            return self._interval_ms / 1000
        if self.scan_mode == SCAN_MMS:
            return defaults.mms_interval_ms / 1000
        return defaults.poll_interval

    def _make_transport(self, *, ip, port, acc_password, relay_model, logger):
        """Fabrica que o `LinkPool` chama em `connect()`: o modo escolhido na
        tela de selecao DESTE diagrama, e nao um default do processo -- e' o
        que faz o mesmo rele poder ser observado por telnet e por MMS ao
        mesmo tempo, em abas diferentes."""
        return pick_transport(self.scan_mode, ip=ip, port=port,
                              acc_password=acc_password,
                              relay_model=relay_model, logger=logger,
                              scd_path=self.scd_path, ied_name=self.relay_name)

    def _connect(self, pool, defaults, gen: int) -> None:
        job = JobReporter(self.job_id)
        link = None
        try:
            job.stage("Pedindo conexão com o relé...", 4)
            try:
                link, is_new = pool.acquire(self.ip, self.port, self.id,
                                            make_transport=self._make_transport)
            except TooManyLinks as e:
                self._fail(str(e), job)
                return
            if is_new:
                link.start_connect(acc_password=defaults.acc_password,
                                   relay_model=self.relay_model,
                                   poll_interval=self._poll_interval(defaults),
                                   job=job,
                                   setup_timeout=defaults.setup_timeout)
            else:
                # Outro diagrama ja abriu (ou esta abrindo) este rele.
                job.stage("Entrando na conexão existente...", 40)
            # Esperar em `ready` (e nao chamar connect direto) e' o que permite
            # desconectar ou fechar no meio: esta thread nunca fica presa
            # dentro do selprotopy. A espera nao tem prazo porque a descoberta
            # de bits num FID sem cache leva minutos de propria vontade -- quem
            # tem prazo e' o setup, pelo watchdog do link.
            while not link.ready.wait(timeout=2.0):
                if self._cancelled(gen):
                    self._abandon(link, pool, job)
                    return
            if link.error or not link.connected:
                reason = link.error or "conexão não ficou pronta"
                pool.release(link, self.id)
                link = None
                self._fail(reason, job, gen)
                return
            if self._cancelled(gen):
                self._abandon(link, pool, job)
                return
            job.stage("Localizando bits do diagrama...", 70)
            link.ensure_bits(self.all_wanted_bits, job=job)
            # A descoberta leva minutos num FID sem cache: olha de novo.
            if self._cancelled(gen):
                self._abandon(link, pool, job)
                return
            # Primeira conexao: adota o que ficou gravado pelo DEVID.
            self.notes.adopt_devid(link.devid, self.logger)
            with self._lock:
                if gen != self._gen:
                    link_to_drop, link = link, None
                    self._abandon(link_to_drop, pool, job)
                    return
                self.link = link
                self.status = LIVE
                self.error = ""
            job.finish(f"Conectado ({link.fid or link.key})")
            self.logger.info("[glv] diagrama %s ao vivo em %s (modo %s)",
                             self.id, link.key, link.mode)
        except Exception as e:      # nunca derruba a thread nem o diagrama
            if link is not None:
                pool.release(link, self.id)
            self._fail(f"falha ao conectar: {e}", job, gen)

    def _abandon(self, link, pool, job) -> None:
        """O usuario desconectou ou fechou enquanto isto conectava."""
        pool.release(link, self.id)
        job.finish("Conexão cancelada")
        REGISTRY.drop(self.job_id)
        self.logger.info("[glv] diagrama %s: conexao com %s abandonada "
                         "(desconectado no meio)", self.id, link.key)

    def _fail(self, reason: str, job=None, gen: int | None = None) -> None:
        with self._lock:
            if gen is not None and gen != self._gen:
                # Ja desconectaram: nao pinta de vermelho um diagrama que o
                # usuario deixou em repouso de proposito.
                self.logger.info("[glv] diagrama %s: %s (ignorado, tentativa "
                                 "abandonada)", self.id, reason)
                return
            self.link = None
            self.status = ERROR
            self.error = reason
            # O badge le values.error e pinta vermelho com prefixo "ERRO:".
            self.idle.error = reason
        if job:
            job.fail(reason)
        self.logger.warning("[glv] diagrama %s: %s", self.id, reason)

    def disconnect(self, pool) -> None:
        """Solta o link e devolve o diagrama a indeterminado."""
        with self._lock:
            link, self.link = self.link, None
            self.status = IDLE
            self.error = ""
            # Invalida a tentativa em voo, se houver: e' o que faz a thread de
            # conexao soltar a referencia em vez de anexa-la a um diagrama que
            # o usuario ja mandou parar.
            self._gen += 1
            # A tela nunca pode continuar mostrando a leitura antiga.
            self.idle.clear()
        if link is not None:
            pool.release(link, self.id)
            self.logger.info("[glv] diagrama %s desconectado de %s",
                             self.id, link.key)
        REGISTRY.drop(self.job_id)

    def close(self, pool) -> None:
        """Fecha o diagrama: solta a conexao e larga os SVGs."""
        self.disconnect(pool)
        self.svgs.clear()
        self.logger.info("[glv] diagrama %s fechado", self.id)

    def _current_interval_ms(self, defaults=None) -> int:
        """O periodo em vigor agora, ou o que entraria em vigor no proximo
        connect(): o do link vivo se houver, senao o ultimo override desta
        sessao, senao -- quando `defaults` e' passado -- o mesmo calculo de
        `_poll_interval(defaults)` (o default do modo MMS ou o de telnet de
        sempre), e so' na ausencia de tudo isso, 0.

        O `defaults=None` importa: `set_interval_ms` chama isto pra' devolver
        o periodo numa RECUSA (telnet) sem tocar em nada, e nesse caminho o
        valor nunca chega na tela (o controle nem existe pra telnet). Ja
        `tab()`/`meta()` chamam com `defaults` em maos, porque um diagrama
        MMS ainda sem override nenhum tambem precisa de um numero verdadeiro
        no campo -- 0 ali seria um placeholder mentindo que nao ha periodo
        nenhum, quando na verdade ha' um (o default do modo) que vai valer
        assim que a leitura começar."""
        link = self.link
        if link is not None:
            return int(round(link.poll_interval * 1000))
        if defaults is not None:
            return int(round(self._poll_interval(defaults) * 1000))
        return self._interval_ms if self._interval_ms is not None else 0

    def set_interval_ms(self, ms: int) -> dict:
        """Aplica (ou adia, ou recusa) um periodo de polling novo.

        A rota e' so' do MMS. No MMS o laco e' uma leitura em lote sincrona
        por volta e o proprio ciclo faz o ritmo (ver
        `transport.mms.effective_interval`), entao qualquer periodo pedido --
        inclusive 0 -- no maximo encosta o laco na velocidade do rele. Os
        modos telnet nao tem essa garantia escrita nem bancada pra medi-la
        agora: 4xx e 7xx conversam por Fast Message em varias idas e vindas
        por volta, e o 3xx trava em 1.5 s dentro do proprio transporte porque
        um `TAR <linha>` custa ~200 ms NO RELE. Apertar isso as cegas por uma
        caixa de texto e' o tipo de coisa que so' aparece como rele lento em
        comissionamento. Por isso um diagrama telnet e' RECUSADO, e devolve o
        periodo que ja estava em vigor, sem tocar em nada.

        As tres respostas possiveis, pro cliente distinguir:
          "aplicado"  -- havia leitura rodando agora, e ela ja usa o novo
                         periodo a partir do proximo ciclo.
          "adiado"    -- MMS, mas sem leitura rodando AGORA (conectando,
                         desconectado, ou leitura zumbi que ainda nao morreu
                         -- ver `RelayLink.set_poll_interval`). O valor fica
                         guardado (`self._interval_ms`) e `_poll_interval`
                         o usa no proximo connect() ou reinicio; nao e'
                         descartado em silencio.
          "recusado"  -- nao e' MMS. Nada mudou.

        O unico ajuste feito no valor pedido e' o corte em 0: periodo
        negativo nao quer dizer nada, e viraria um `sleep` negativo la' na
        frente.
        """
        if self.scan_mode != SCAN_MMS:
            return {
                "interval_ms": self._current_interval_ms(),
                "status": "recusado",
                "reason": "o período só é ajustável no modo MMS; este "
                         "diagrama está em telnet",
            }
        ms = max(int(ms), 0)
        self._interval_ms = ms
        link = self.link
        applied = link.set_poll_interval(ms / 1000) if link is not None else False
        if applied:
            return {"interval_ms": ms, "status": "aplicado", "reason": ""}
        return {
            "interval_ms": ms,
            "status": "adiado",
            "reason": "sem leitura ativa agora; vale a partir da próxima "
                     "leitura",
        }

    # -- payloads pro cliente ------------------------------------------------

    def tab(self, defaults=None) -> dict:
        """O que a faixa de abas precisa saber.

        `defaults` e' opcional (default None preserva quem ainda chama sem
        ele) mas o handler sempre tem um em maos e deve passa-lo: e' o que
        faz `interval_ms` ser o periodo REAL desta aba -- incluindo o default
        do modo MMS/telnet quando ainda nao houve nenhum /period nem conexao
        -- em vez de 0, que mentiria "sem periodo nenhum". Sem isto, trocar
        de aba entre dois diagramas MMS com periodos diferentes deixava o
        campo mostrando o valor da aba anterior."""
        link = self.link
        status, error = self.status, self.error
        if link is not None and not link.connected:
            # A leitura parou sozinha. `self.status` ainda diz LIVE porque
            # quem descobriu foi a thread de polling, que nao mexe no
            # diagrama -- entao a traducao acontece aqui, na hora de contar
            # pra tela, e o motivo vem do link.
            status = ERROR
            error = link.error or "a leitura parou"
        return {
            "id": self.id,
            "title": self.title,
            "relay": self.relay_name,
            "gle": self.gle_name,
            "ip": self.ip,
            "port": self.port,
            "status": status,
            "error": error,
            "connected": self.connected,
            "refs": len(link.owners) if link is not None else 0,
            "fid": link.fid if link is not None else "",
            "scan_mode": self.scan_mode,
            "interval_ms": self._current_interval_ms(defaults),
        }

    def default_page(self) -> str:
        """A pagina de quem nunca abriu nenhuma: a SEGUNDA, quando existe.

        A primeira pagina de um GLE do QuickSet e' capa/indice em quase todo
        arquivo do corpo; a segunda e' a primeira que tem logica desenhada.
        """
        if len(self.pages_meta) > 1:
            return self.pages_meta[1][1]
        return self.pages_meta[0][1] if self.pages_meta else ""

    def remember_page(self, safe_id: str) -> None:
        """Anota qual pagina o visitante abriu. Uma atribuicao de string, sem
        `_lock`: nao ha estado composto pra manter coerente aqui."""
        if safe_id and safe_id in self.svgs:
            self.open_page = safe_id

    def meta(self, defaults=None) -> dict:
        """Tudo o que a troca de aba precisa pra re-renderizar sem recarregar."""
        d = self.tab(defaults)
        # A pagina aberta vence a inicial -- e' o que faz a aba voltar onde
        # estava. Uma pagina anotada que nao existe mais (GLE trocado sob o
        # mesmo diagrama) cai na inicial em vez de abrir vazia.
        initial = (self.open_page if self.open_page in self.svgs
                   else self.default_page())
        d.update({
            "pages": self.pages_meta,
            "initial": initial,
            "var_index": self.var_index,
            "analog_groups": self.analog_groups_meta,
            "notes_key": self.notes.key,
        })
        return d

    def values(self, page: str) -> dict:
        """Snapshot filtrado pela pagina aberta (era o /values do handler)."""
        snap = self.state.snapshot()
        if page and page in self.bits_per_page:
            wanted = set(self.bits_per_page[page])
            # Os bits que ACIONAM os conectores desta pagina. A ponta que
            # emite pode estar numa pagina e o acionamento na outra (medido:
            # 9 redes do corpus atravessam pagina), e ai nada nesta pagina
            # pede esses bits -- o conector ficaria indeterminado pra sempre.
            # Custa <=10 bits por conector (mediana 10, maximo 10 no corpus),
            # e eles JA estao em `all_wanted_bits`, entao o mapa MMS ja os
            # cobre: o que faltava era o filtro por pagina os estreitar.
            page_nets = connectors_on_page(self.connectors, page)
            for net in page_nets.values():
                wanted |= set(net.bits)
            # Modo TAR (3xx): diz ao poll o que vale a pena ler. Com dois
            # diagramas no mesmo rele, o link publica a UNIAO dos dois.
            link = self.link
            if link is not None:
                link.set_wanted_bits(self.id, wanted)
            else:
                self.idle.set_wanted_bits(wanted)
            # Index case-insensitive dos bits que temos do rele
            ci_digitals = {k.upper(): v for k, v in snap["digitals"].items()}
            # Retorna TODOS os bits da pagina: valor 0/1 se conhecido, ou null
            # (indeterminado) se nao esta no mapa do rele.
            snap["digitals"] = {
                bit: ci_digitals.get(bit, None) for bit in sorted(wanted)
            }
            snap["page"] = page
            # Estrutura, nao valor: quem AVALIA a arvore e' o `evaluatePage`,
            # com as mesmas primitivas que ja usa pros blocos desenhados.
            # Avaliar aqui poria NOT/RTRIG/latch em duas linguagens.
            snap["connectors"] = {
                label: {"label": net.label, "equation": net.equation,
                        "tree": net.tree, "bits": sorted(net.bits),
                        "driver_page": net.driver_page}
                for label, net in page_nets.items()
            }
            snap["page_bits_total"] = len(wanted)
            snap["page_bits_known"] = sum(
                1 for v in snap["digitals"].values() if v is not None)
            # Cobertura da PAGINA aberta. Quem responde e' o transporte, pela
            # costura -- antes daqui saia um `getattr(link.transport, "_map")`,
            # que e' o diagrama metendo a mao no privado de um transporte que
            # ele nem sabe qual e'. O telnet devolve None e o cliente esconde o
            # badge, em vez de mostrar zero (que soaria como "nada mapeado"
            # quando o certo e' "nao se aplica").
            transport = getattr(link, "transport", None) if link else None
            snap["coverage"] = (transport.coverage_for(wanted)
                                if transport is not None else None)
        # Analogs: se a pagina tiver mapa de analogs (relay_model com
        # analog_groups configurados), entregamos { NAME: {value, group} },
        # onde value=null indica "rele nao expoe esse canal" (renderizado como
        # N/A). Senao deixamos o snap["analogs"] cru pra preservar
        # compatibilidade com modelos sem analog_groups.
        if page and page in self.analogs_per_page:
            wanted_an = self.analogs_per_page[page]
            ci_analogs = {k.upper(): v for k, v in snap["analogs"].items()}
            # Resolve alias quando o rele expoe o canal sob outro nome no Fast
            # Meter (ex.: SEL-487E: IAS -> IA1). Sem aliases, e' identidade.
            resolve = (self.relay_model.resolve_analog_name
                       if self.relay_model is not None else (lambda nm: nm))
            snap["analogs"] = {
                nm: {"value": ci_analogs.get(resolve(nm)), "group": wanted_an[nm]}
                for nm in sorted(wanted_an)
            }
            snap["analog_groups"] = self.analog_groups_meta
        snap["status"] = self.status
        snap["connected"] = self.connected
        return snap

    def unreachable(self) -> dict:
        """As variaveis do DESENHO INTEIRO que esta conexao nao alcanca.

        Do desenho inteiro, e nao da pagina aberta -- ao contrario da
        cobertura da faixa de status, que e' por pagina de proposito. Os dois
        numeros respondem perguntas diferentes: a cobertura diz o quanto do
        que esta na tela esta vivo, e esta lista existe pra montar o modelo do
        servidor do IED, onde percorrer pagina por pagina pra juntar os nomes
        e' justamente o trabalho que ela evita.

        `available: False` quando nao da pra saber (desconectado, sem mapa,
        sem DNA): um `0` numa tela que ninguem conectou leria como "esta tudo
        no rele".
        """
        total = len(self.all_wanted_bits)
        link = self.link
        transport = getattr(link, "transport", None) if link is not None else None
        out = (transport.unreachable(self.all_wanted_bits)
               if transport is not None else None)
        if not out:
            return {"available": False, "names": [], "count": 0,
                    "total": total, "reason": "", "scan_mode": self.scan_mode}
        names = sorted(out.get("names") or [])
        return {"available": True, "names": names, "count": len(names),
                "total": total, "reason": out.get("reason", ""),
                "scan_mode": self.scan_mode}

    def debug_analogs(self) -> dict:
        """Nomes/valores crus que o rele expoe via Fast Meter, sem filtro por
        pagina e sem alias. Use pra batear contra analog_name_aliases."""
        raw = self.state.snapshot().get("analogs", {})
        resolved = {}
        if self.relay_model is not None:
            all_names: set = set()
            for pg_map in self.analogs_per_page.values():
                all_names.update(pg_map.keys())
            ci_raw = {k.upper(): v for k, v in raw.items()}
            for gle_name in sorted(all_names):
                fm = self.relay_model.resolve_analog_name(gle_name)
                resolved[gle_name] = {
                    "fm_key": fm,
                    "value": ci_raw.get(fm.upper()),
                    "in_fm": fm.upper() in ci_raw,
                }
        return {"fm_keys": sorted(raw.keys()), "fm_n": len(raw),
                "gle_resolved": resolved}


def build_diagram(diagram_id: str, gle_path, relay_name: str, gle_name: str,
                  ip: str, port: int, relay_model, logger, *,
                  scan_mode: str = SCAN_TELNET, scd_sha: str | None = None,
                  scd_path=None) -> GlvDiagram:
    """Monta um diagrama SEM tocar na rede: parse, render, indices, notas.

    Roda em ~1 s. Conectar e' outra coisa, e vem depois, quando o usuario pedir.
    """
    d = GlvDiagram(diagram_id, relay_name=relay_name, gle_name=gle_name,
                   gle_path=gle_path, ip=ip, port=port,
                   relay_model=relay_model, logger=logger,
                   scan_mode=scan_mode, scd_sha=scd_sha, scd_path=scd_path)

    logger.info("[glv] carregando GLE: %s", gle_path)
    gle_root = parse_gle(gle_path)
    d.pages_meta = list_pages(gle_root)
    for p in gle_root.findall(".//page"):
        name = p.get("name", "")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(d.svgs)}"
        d.svgs[safe] = render_page(p, relay_model=relay_model)
    logger.info("  %d paginas renderizadas", len(d.svgs))

    d.bits_per_page = collect_bits_per_page(gle_root, relay_model=relay_model)
    d.analogs_per_page = collect_analog_symbols_per_page(gle_root, relay_model)
    gle_bits = collect_bit_names(gle_root, relay_model=relay_model)

    # Coleta TAMBEM os outputs derivados (PLT04, PCT03Q, AST01Q, ...). Os
    # patterns vem do JSON do modelo:
    #   LATCH (PLT)  -> "PLT{instance:02d}"   -> PLT04
    #   TIMER (PCT)  -> "PCT{instance:02d}Q"  -> PCT03Q
    #   AST          -> "AST{instance:02d}Q"  -> AST01Q
    #   PSV          -> "PSV{instance:02d}"   -> PSV05
    # Sem modelo carregado, nao geramos bits derivados.
    derived_bits: set = set()
    if relay_model is not None:
        for p in gle_root.findall(".//page"):
            for el in p.findall(".//element"):
                xml_type = el.get("type") or ""
                le = el.find("logic_element")
                if le is None:
                    continue
                try:
                    instance = int(le.get("physical_instance_number") or 0)
                except ValueError:
                    instance = 0
                name = le.get("physical_instance_name") or ""
                bit = relay_model.derived_bit_for(xml_type, instance, name)
                if bit:
                    derived_bits.add(bit)
    d.all_wanted_bits = set(b.upper() for b in gle_bits) | derived_bits

    # As redes de conector. Nao acrescentam bit nenhum a `all_wanted_bits`:
    # o que aciona um conector ja e' desenhado em ALGUMA pagina do GLE, entao
    # ja esta ali. O que elas mudam e' o filtro por pagina aberta, em
    # `values()`.
    d.connectors = extract_connectors(gle_root, relay_model=relay_model)
    if d.connectors:
        logger.info(
            "  %d rede(s) de conector: %s", len(d.connectors),
            ", ".join(f"{lab} ({len(n.emitters)} saida(s), {len(n.bits)} bit(s))"
                      for lab, n in sorted(d.connectors.items())))

    if relay_model is not None and relay_model.analog_groups:
        d.analog_groups_meta = {g.key: g.label for g in relay_model.analog_groups}

    # Indice global de variaveis -> paginas, usado pelo search box do header:
    # digital (bits + outputs derivados) e analogica (canais FM). Chave em
    # UPPER; valor: lista ordenada de safe_page_ids onde a variavel aparece.
    var_index: dict = {}
    for safe_id, names in d.bits_per_page.items():
        for nm in names:
            ent = var_index.setdefault(nm, {"kind": "bit", "pages": []})
            if safe_id not in ent["pages"]:
                ent["pages"].append(safe_id)
    for safe_id, names_map in d.analogs_per_page.items():
        for nm in names_map.keys():
            # Se ja existia como bit (raro: nome reusado), mantem como bit mas
            # registra a pagina; senao fica analog.
            ent = var_index.setdefault(nm, {"kind": "analog", "pages": []})
            if safe_id not in ent["pages"]:
                ent["pages"].append(safe_id)
    d.var_index = var_index

    logger.info(
        "  %d bits do GLE (%d com derivados), %d analogicos em %d familia(s); "
        "notas na chave %r",
        len(gle_bits), len(d.all_wanted_bits),
        sum(len(v) for v in d.analogs_per_page.values()),
        len(d.analog_groups_meta), d.notes.key)
    return d
