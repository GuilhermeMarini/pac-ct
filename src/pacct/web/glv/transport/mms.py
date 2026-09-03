"""O transporte MMS do GLV: le a pagina aberta numa Read em LOTE.

Medido no SEL-451-5 R331 da bancada, com o GL1.gle do proprio rele, quando
cada leitura custava uma requisicao:

    por bit        170 req / 739 ms      por DO   170 req / 731 ms
    por LN$FC       30 req / ~180 ms     LD todo   64 req / 1020 ms

Era dai que vinha a leitura por `LN$FC`: o RTT e' 3,1 ms e o ciclo saia
`containers x 3,1 ms`. O que faltava era a Read multi-variavel do MMS -- UMA
requisicao nomeando VARIAS variaveis --, que a py61850 passou a oferecer em
`read_refs` (pin dcbc20c). Com ela o ciclo deixa de ser contado em containers:
e' `ceil(bytes do pedido / max_pdu_size)`. Medido nos tres reles da bancada --
751 (.41, R402: 1055 bits/194 containers), 487E (.31, R323: 784/122) e 451-5
(.62, R331: 1422/207) -- por FORMA de pagina, que e' o que muda tudo aqui: um
desenho pede BITS, e eles caem espalhados pelos `LN$FC`. Por LN$FC -> lote:

                             751 (7xx)          487E (4xx)        451 (4xx)
    espalhada,  80 bits   190 ->  5,8 (33x)   214 -> 25 (8,7x)  298 -> 22 (14x)
    espalhada, 200 bits   242 -> 13,7 (18x)   376 -> 58 (6,4x)  437 -> 53 (8,2x)
    mapa inteiro          400 ->  72  (5,6x)  612 -> 320 (1,9x) 948 -> 435 (2,2x)
    concentrada, 8 cont.   10,7 ->  5,9 (1,8x)  42 -> 63 (0,7x)   36 -> 53 (0,7x)

A ultima linha e' a unica em que o lote PERDE, e so' nos dois 4xx: uma pagina
cujos bits cabem em 8 containers vira um pedido de ~230 nomes (~10 kB,
fragmentado em TPDU de 1024 B) contra 8 leituras curtas. Os numeros dos dois
lados estao abaixo do piso de 100 ms do polling, entao o laco dorme igual --
e a pagina real e' a espalhada (80 bits caem em 50 containers no 451), onde a
diferenca e' de uma ordem de grandeza.

Os valores conferem: nos tres reles, os 1055, 784 e 1422 bits do mapa saem
IGUAIS pelos dois caminhos (zero diferencas). Numa medicao anterior, 2 dos
1055 diferiram -- SV36/SV36T, que mudam sozinhos entre duas voltas do MESMO
caminho.

O lote e' de FOLHAS (`LN$FC$DO$DA`), nunca de containers. A py61850 orca o
PEDIDO contra o `max_pdu_size` negociado, e a RESPOSTA nao entra nessa conta:
16 containers ja devolvem ~11,2 kB contra um teto de 12000, e 32 containers
voltam `MmsError: service/3`. Uma folha booleana responde ~6 bytes, entao um
lote de folhas nunca chega perto do teto -- 1055 delas couberam em 5 pedidos.

A pagina aberta continua sendo o filtro (`state.wanted_bits`), agora bit a bit
e nao mais container a container: antes, pedir UM bit arrastava junto os
outros 30 do `LN$FC` dele. O mapa inteiro ate' caberia numa volta no 751
(72 ms, abaixo do piso de 100 ms), mas nao no 487E (320 ms) -- e ler o que a
tela nao mostra nao ajuda ninguem em rele nenhum.

Duas coisas medidas e DESCARTADAS, pra ninguem tentar de novo: pipeline e' 3x
mais LENTO (a assinatura e' delayed-ACK) mesmo o rele anunciando
maxServOutstanding=3, e TCP_NODELAY nao muda nada.

O cliente da py61850 e' um socket e um contador de invoke: NAO e' thread-safe,
e por isso nunca ha duas threads lendo o mesmo `MmsClient`. O `prepare_bits`
ainda recebe o `pause` da casca por contrato do `Transport`, mas hoje ele NAO
fala mais com o rele: o mapa sai do diretorio lido no connect, e a estrutura
dos containers deixou de ser lida quando a leitura passou a ser por folha.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from py61850 import MmsClient
from py61850.errors import Iec61850Error
from selfiles.scl import mms_tables
from selfiles.scl.mms_tables import decode_bit
from selfiles.scl.read import sel_short_addresses

from pacct.web.glv.mms_map import ld_suffixes, resolve_map
from pacct.web.glv.poll import FirstTimeLog
from pacct.web.glv.transport import MODE_MMS, drawing_variables

# Nao ha piso de periodo: quem pedir 0 ms le tao rapido quanto o rele
# responder. O que impede afogar o link nao e' um numero escolhido a mao -- e'
# a forma do laco. A leitura e' SINCRONA e ha uma so' por volta, e
# `effective_interval` nunca devolve menos do que a volta anterior custou,
# entao a proxima requisicao so' parte depois que a anterior foi respondida:
# nunca ha mais de um pedido em voo por link, e o pior caso e' 100% de
# ocupacao com o RELE ditando o ritmo. E' isso que "sem piso" pode significar
# sem virar enxurro.
#
# O unico periodo minimo que sobra e' o da volta que NAO falou com o rele:
# pagina sem nenhum bit mapeado, ou uma excecao que volta na hora. Ali nao ha
# resposta nenhuma pra esperar, e periodo 0 seria um laco quente queimando uma
# CPU sem tocar na rede. Nao e' periodo de comunicacao; e' de quanto em quanto
# tempo o laco olha se ja ha o que ler.
IDLE_INTERVAL = 0.050

# Prazo do socket da py61850. Vale pra associacao E pra cada leitura -- e' o
# que dispensa este transporte do truque do telnet de fechar o socket pra
# acordar uma leitura travada.
SOCKET_TIMEOUT = 10

# O item que carrega o FID. E' `DC` (description) e nao `ST`: NamPlt e' um DO
# de dados de placa, nao um estado.
SW_REV_ITEM = "LLN0$DC$NamPlt$swRev"

_logger = logging.getLogger(__name__)


class MmsSetupError(RuntimeError):
    """O rele respondeu, mas nao serve este diagrama.

    Nao e' `Iec61850Error`: a py61850 fez o trabalho dela sem falha nenhuma.
    O que faltou e' do dominio -- nenhum logical device anunciado, ou nenhum
    bit do desenho com endereco MMS. Chamar isso de erro de protocolo mandaria
    o usuario procurar rede onde o problema e' arquivo de projeto.
    """


def _part_from(relay_model, fid: str) -> str:
    """A peca que nomeia a tabela de fallback: `SEL-451` -> `451`.

    O modelo vem primeiro porque e' o que o RDB afirma; o FID e' o plano B, e
    tem a peca no segundo campo (`SEL-451-5-R331-...`).
    """
    model = getattr(relay_model, "model", "") or ""
    if model:
        return model.replace("SEL-", "").replace("sel-", "")
    parts = (fid or "").split("-")
    return parts[1] if len(parts) > 1 else ""


def _model_label(relay_model, fid: str) -> str:
    """Como CHAMAR o rele numa mensagem de erro.

    A spec pede que a recusa por falta de mapa "nomeie o modelo". O RDB e' a
    melhor fonte, o FID e' a segunda, e a peca extraida de um dos dois e' a
    terceira -- "esse rele" sozinho nao diz a ninguem o que procurar nem o
    que mandar pro fabricante.
    """
    model = getattr(relay_model, "model", "") or ""
    if model:
        return model
    if fid:
        return f"FID {fid}"
    part = _part_from(relay_model, fid)
    return f"peça {part}" if part else "modelo desconhecido"


class MmsTransport:
    """MMS/IEC 61850 por TCP 102. Implementa o mesmo `Transport` do telnet.

    O que ele NAO faz, de proposito: descobrir nome de bit no rele. O nome da
    Relay Word mora no `sAddr` do SCL, que o rele nao serve -- nao existe aqui
    o equivalente do `TAR <nome>`. As duas fontes sao o SCD do projeto e a
    tabela de fabrica, e as duas sao conferidas contra o diretorio do rele.
    """

    mode = MODE_MMS

    def __init__(self, ip, port, *, relay_model=None, logger=None,
                 scd_path=None, ied_name=None):
        self.ip, self.port = ip, port
        self.relay_model, self.logger = relay_model, logger or _logger
        self.scd_path, self.ied_name = scd_path, ied_name
        self.key = f"{ip}:{port}"
        self.fid = self.devid = ""
        self._client = None
        self._map = None
        self._ld_by_suffix: dict = {}
        self._directory: dict = {}      # sufixo -> nomes do LD (o que resolve_map le)
        self._directory_by_ld: dict = {}
        self._lds: list = []
        self._wanted: set = set()
        self._last_cycle = 0.0
        self._cycles: list = []
        self._lock = threading.RLock()
        # O plano de leitura publicado pra thread de polling: a tupla de
        # `MmsPoint` que vira a lista de pares `(ld, item)` do `read_refs`.
        # E' um objeto trocado INTEIRO sob `self._lock`; a thread so' o LE (uma
        # referencia de atributo, atomica), e nunca pega o lock -- pegar
        # deadlockaria contra o `pause()`, que espera essa mesma thread morrer.
        self._plan: tuple = ()
        # Ate onde o prazo do watchdog vale, igual ao telnet. Aqui a fronteira
        # e' a associacao: a varredura do diretorio que vem depois e' UMA
        # `GetNameList` por LD (~12.735 nomes so' na ANN do 451) e ninguem
        # cronometrou. Sem este evento o prazo de 60s cobria a varredura
        # tambem, e estourar ali dizia ao usuario que o rele "nao respondeu",
        # apontando pra rede quando o problema seria o tamanho do diretorio.
        # A py61850 tem prazo de socket de verdade (`SOCKET_TIMEOUT`), entao a
        # varredura nao fica sem protecao nenhuma -- fica com a certa.
        self.setup_done = threading.Event()

    def effective_interval(self, requested: float, last_cycle: float) -> float:
        """O periodo pedido, nunca abaixo do que a volta anterior custou.

        Nao ha piso alem desse, e e' esse `last_cycle` que faz a falta de piso
        ser segura: o ciclo ja inclui a resposta do rele, entao pedir 0 ms nao
        empilha requisicoes -- encosta o laco no ritmo do proprio rele, com um
        pedido em voo de cada vez.
        """
        return max(requested, last_cycle, 0.0)

    # -- conexao ------------------------------------------------------------

    def connect(self, job=None) -> None:
        """Associa, identifica o rele e le o diretorio de cada LD.

        O diretorio e' o que confere o mapa: e' dele que sai o FC de cada
        `LN$*$DO$DA` (ver `mms_map.resolve_map`). Uma leitura por LD, uma vez
        por conexao -- e' a parte cara, e e' aqui que ela pertence, com o job
        na mao pra dizer o que esta acontecendo.
        """
        if job:
            job.stage("Associando com o relé (MMS, porta 102)...", 10)
        client = MmsClient(self.ip, self.port, timeout=SOCKET_TIMEOUT)
        client.connect()
        with self._lock:
            self._client = client
        # Associou: daqui pra frente o prazo do watchdog nao manda mais.
        self.setup_done.set()

        lds = list(client.get_server_directory())
        if not lds:
            raise MmsSetupError(
                "o relé não anunciou nenhum logical device; "
                "confira se o servidor 61850 está habilitado")
        self._lds = lds
        # `commonprefix` num LD so' devolve o proprio nome -- e' o certo: sem
        # um segundo nome pra comparar nao ha' o que separar.
        self.devid = (os.path.commonprefix(lds).rstrip("_") if len(lds) > 1
                      else lds[0])

        self.fid = self._read_fid(client, lds)

        if job:
            job.stage("Lendo o diretório dos logical devices...", 40)
        for i, ld in enumerate(lds):
            self._directory_by_ld[ld] = list(
                client.get_logical_device_directory(ld))
            if job:
                job.fraction(f"Diretório de {ld}...", i + 1, len(lds))
        self.logger.info(
            "[glv] %s: MMS associado, FID=%s, %d LD(s), %d nomes no diretório",
            self.key, self.fid or "?", len(lds),
            sum(len(v) for v in self._directory_by_ld.values()))

    def _read_fid(self, client, lds) -> str:
        """`LLN0$DC$NamPlt$swRev` -> `SEL-451-5-R331-V1-Z033014-D20250919`.

        O rele responde com o prefixo `FID=` colado; o resto do projeto guarda
        o FID sem ele (e' assim que o cache do `AsciiTargetReader` e' nomeado),
        entao tiramos aqui e nao em quem le.

        Qualquer LD serve -- LLN0 existe em todos -- mas nem todo LD responde,
        entao tentamos ate um responder.
        """
        for ld in lds:
            try:
                value = client.read_value(ld, SW_REV_ITEM)
            except Iec61850Error:
                continue
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            if isinstance(value, str) and value:
                return value[4:] if value.startswith("FID=") else value
        self.logger.warning("[glv] %s: nenhum LD respondeu %s; seguindo sem FID",
                            self.key, SW_REV_ITEM)
        return ""

    def abort(self) -> None:
        """Fecha o socket pra levantar uma associacao travada.

        O prazo do socket da py61850 ja' cobre o caso normal; isto e' pra quem
        chama de outra thread (o watchdog do `RelayLink`) nao ter que esperar
        o prazo inteiro.
        """
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def unreachable(self, bits):
        """Os nomes sem ponto no mapa -- o que falta no modelo do servidor.

        Um nome so' entra no mapa quando o diretorio do proprio rele o
        confirma (ver `resolve_map`), entao o que sobra aqui e' precisamente o
        que o IED nao publica: ou nao esta no modelo, ou esta com outro nome
        de instancia (medido no 487E: a tabela pede `IT01PTOC1`, o rele
        publica `IT1PTOC46`).

        `None` antes de haver mapa, pelo mesmo motivo do `coverage_for`.
        """
        if self._map is None:
            return None
        cov = self._map.coverage(drawing_variables(bits))
        return {"names": list(cov.missing), "reason": "mms"}

    def coverage_for(self, bits):
        """Quanto DESTES bits este transporte consegue ler, e por qual fonte.

        Os bits sao os da PAGINA aberta, e nao a uniao acumulada do link: a
        cobertura roda perto de 100% numa pagina de GOOSE e perto de 50% numa
        de CS89/LED, e a uniao cresceria a cada diagrama novo mesmo que esta
        pagina em particular esteja mal coberta.

        `None` antes de haver mapa -- nao ha o que reportar, e zero seria
        mentira (soaria como "nada mapeado" quando e' "ainda nao se sabe").
        """
        if self._map is None:
            return None
        cov = self._map.coverage(bits)
        return {"mapped": cov.mapped, "total": cov.total,
                "source": self._map.source}

    # -- descoberta ---------------------------------------------------------

    def _sources(self):
        """`(pontos do SCD, tabela de fabrica)` -- as duas fontes do mapa.

        Nenhuma das duas fala com o rele: o SCD e' arquivo do projeto e a
        tabela e' dado embarcado. Quem confere e' o diretorio, ja lido.
        """
        scd_points: dict = {}
        if self.scd_path:
            try:
                by_ied = sel_short_addresses(self.scd_path)
            except Exception as e:
                self.logger.warning("[glv] %s: SCD ilegível (%s); seguindo só "
                                    "com a tabela de fábrica", self.key, e)
                by_ied = {}
            scd_points = self._points_for_ied(by_ied)
        table = mms_tables.lookup(_part_from(self.relay_model, self.fid))
        if table is None:
            self.logger.info("[glv] %s: sem tabela de fábrica pra essa peça; "
                             "só o SCD responde pelo mapa", self.key)
        return scd_points, table

    def _points_for_ied(self, by_ied) -> dict:
        """Qual IED do SCD e' este rele.

        Explicito ganha; um SCD de um IED so' nao tem ambiguidade; senao
        casamos pelo DEVID, que e' o prefixo dos LD. Sem casar, e' melhor nao
        usar SCD nenhum do que usar o do rele do lado.
        """
        if not by_ied:
            return {}
        # O nome vem do RDB (`GlvDiagram.relay_name`) e o SCD e' de outra
        # ferramenta: os dois nao sao obrigados a soletrar o rele igual. Por
        # isso e' uma DICA, e nao um veredito -- devolver `{}` num nome que
        # nao casa tornava as duas heuristicas abaixo codigo morto no caminho
        # vivo, e um unico desencontro de nome degradava, so' com uma linha de
        # log, a decisao de cabeceira desta branch: "o SCD do projeto primeiro,
        # porque ele e' a verdade como construida".
        #
        # O nome PRESENTE no SCD, esse sim, e' veredito: se o IED esta la e nao
        # tem nenhum sAddr, o mapa dele e' vazio mesmo -- cair pras heuristicas
        # ali levaria o mapa do rele do lado.
        if self.ied_name:
            if self.ied_name in by_ied:
                return by_ied[self.ied_name]
            self.logger.warning(
                "[glv] %s: IED '%s' não está no SCD; tentando casar por outro "
                "caminho antes de desistir do SCD do projeto",
                self.key, self.ied_name)
        if len(by_ied) == 1:
            name, points = next(iter(by_ied.items()))
            self.ied_name = name
            return points
        # O DEVID e' o prefixo comum dos LD, ou seja, o rele se nomeando. O
        # casamento e' de PREFIXO, nos dois sentidos, e nao de subcadeia: com
        # `name in self.devid` um IED chamado "TR1" casaria com o DEVID
        # "QPC1_TR1_UPC1" -- e tambem com "QPC2_TR1_UPC1", o rele do lado.
        # Enquanto esta heuristica era codigo morto isso nao aparecia; agora
        # que o nome do RDB e' so' uma dica, ela e' alcancavel de verdade.
        devid = (self.devid or "").upper()
        for name, points in by_ied.items():
            up = (name or "").upper()
            if devid and up and (devid.startswith(up) or up.startswith(devid)):
                self.ied_name = name
                return points
        self.logger.warning(
            "[glv] %s: o SCD tem %d IEDs e nenhum casa com '%s'; seguindo só "
            "com a tabela de fábrica", self.key, len(by_ied), self.devid)
        return {}

    def prepare_bits(self, names, job=None, pause=None) -> int:
        """Resolve os bits do diagrama contra o diretorio ja' lido no connect.

        Devolve quantos bits ficaram resolvidos AGORA -- e' o contrato do
        `Transport`, e e' o que um segundo diagrama entrando numa conexao viva
        precisa dizer: o que ELE acrescentou.

        `pause` existe pelo contrato do `Transport` e NAO e' usado aqui: desde
        que a leitura passou a ser por folha, nada nesta funcao fala com o
        rele. O que ela consulta -- `_directory_by_ld` -- foi lido de uma vez
        no connect, e antes dela existia a busca de estrutura de cada `LN$FC`,
        que era o unico motivo de parar o leitor. Um segundo diagrama entrando
        agora nao custa uma requisicao nem uma volta perdida de polling.
        """
        with self._lock:
            client = self._client
            if client is None:
                return 0
            wanted = {b.upper() for b in names if b and not b.isdigit()}
            before = set(self._map.points) if self._map else set()
            self._wanted |= wanted
            if wanted <= before:
                self.logger.info(
                    "[glv] %s: todos os %d bits do diagrama já estão no mapa "
                    "MMS", self.key, len(wanted))
                return 0

            if job:
                job.stage(f"Mapeando {len(self._wanted)} bits para itens MMS...",
                          60)
            scd_points, table = self._sources()
            if not scd_points and table is None:
                # Sem NENHUMA das duas fontes nao ha' mapa a construir. A
                # recusa por cobertura zero ate' dispararia mais abaixo, mas
                # dizendo "nenhum bit tem endereco MMS NESTE RELE" -- o que
                # manda o usuario procurar no rele um problema que e' de
                # arquivo. A spec pede a recusa que NOMEIA o modelo; e' esta.
                # As duas faltas sao diferentes e mandam o usuario a lugares
                # diferentes. Dizer "nenhum SCD foi associado" a quem ACABOU
                # de associar um manda procurar de novo o que ja esta la; o
                # que falta nesse caso e' `sAddr` pra este IED dentro dele.
                if self.scd_path:
                    why = (f"o SCD escolhido ({os.path.basename(str(self.scd_path))}) "
                           f"não traz endereço 61850 (sAddr) para este relé "
                           f"-- ou o IED não está nele, ou está sem sAddr")
                    fix = ("Confira o IED no SCD, escolha outro SCD na tela "
                           "de seleção, ou use o modo telnet.")
                else:
                    why = "nenhum SCD do projeto foi associado a este diagrama"
                    fix = ("Informe o SCD na tela de seleção, ou use o modo "
                           "telnet.")
                raise MmsSetupError(
                    f"não há mapa 61850 para "
                    f"{_model_label(self.relay_model, self.fid)}: não existe "
                    f"tabela de fábrica para essa peça e {why}. {fix}")
            # `ld_suffixes` PRECISA dos sufixos que as fontes nomeiam. Sem
            # eles ele cai num prefixo comum, e dois LD que compartilham mais
            # que o nome do IED (`...ANN` e `...CON`) resolvem pro errado --
            # em silencio, com o mapa inteiro apontando pro LD vizinho.
            suffixes = {p.ld_inst for p in scd_points.values() if p.ld_inst}
            if table is not None:
                suffixes |= {suf for suf, _ in table.bits.values() if suf}
            self._ld_by_suffix = ld_suffixes(self._lds, suffixes=suffixes)
            self._directory = {
                suf: self._directory_by_ld.get(ld, ())
                for suf, ld in self._ld_by_suffix.items()
            }

            self._map = resolve_map(
                wanted=self._wanted, directory=self._directory,
                ld_by_suffix=self._ld_by_suffix, scd_points=scd_points,
                table=table)
            # A recusa e' julgada sobre os bits que ESTE diagrama pediu, nao
            # sobre a uniao acumulada no link. `_wanted` nunca encolhe quando
            # um diagrama desconecta, entao julgar pela uniao deixaria um
            # segundo diagrama 100% inenderecavel entrar ao vivo e vazio, de
            # carona na cobertura do primeiro.
            cov = self._map.coverage(self._wanted)
            cov_call = self._map.coverage(wanted)
            if not cov_call.mapped:
                raise MmsSetupError(
                    f"nenhum dos {cov_call.total} bits deste diagrama tem "
                    f"endereço MMS neste relé. Um diagrama conectado e vazio é "
                    f"pior que uma recusa: use o modo telnet, ou informe o SCD "
                    f"do projeto para este IED.")

            # Publica o plano pra thread de polling. Sem isto ela seguiria
            # lendo o plano da PRIMEIRA chamada e os bits do segundo diagrama
            # ficariam indeterminados ate' alguem reconectar.
            #
            # Ordenado por (LD, item), e nao na ordem em que os bits sairam de
            # um `set`: a py61850 lote os pares COMO VIEREM, entao uma lista
            # que alterna de LD gasta uma requisicao a mais em cada fronteira
            # de lote -- e um plano estavel entre execucoes faz o log e a
            # captura de rede darem a mesma sequencia duas vezes.
            self._plan = tuple(sorted(self._map.points.values(),
                                      key=lambda p: (p.ld, p.item)))

            added = len(set(self._map.points) - before)
            self.logger.info(
                "[glv] %s: mapa MMS por %s -- %d/%d bits (%.0f%%), %d "
                "container(es), +%d agora",
                self.key, self._map.source or "nenhuma fonte", cov.mapped,
                cov.total, 100 * cov.fraction, len(self._map.containers()),
                added)
            if cov.missing:
                self.logger.info("[glv] %s: sem endereço MMS: %s", self.key,
                                 ", ".join(cov.missing[:20])
                                 + (" ..." if len(cov.missing) > 20 else ""))
            return added

    # -- polling ------------------------------------------------------------

    def poll(self, state, interval, stop, once) -> None:
        """Le os bits da pagina aberta ate `stop`, um lote por volta.

        `state.wanted_bits` vazio quer dizer o mapa inteiro (o diagrama ainda
        nao disse que pagina esta na tela); com bits, pedimos exatamente esses.
        E' a mesma regra do `poll_loop_tar`, e o filtro agora e' por BIT: com a
        leitura por container, um unico bit pedido arrastava junto os outros 30
        do `LN$FC` dele.

        Um erro do rele encerra o loop: quem reabre e' o `RelayLink`, e um
        leitor que insiste num socket morto so' enche o log.
        """
        if once is None:
            once = FirstTimeLog(self.logger)
        while True:
            if stop is not None and stop.is_set():
                return
            client = self._client
            if client is None:
                return          # `close()` de outra thread; nao ha o que ler
            # Relido a cada volta, e nao capturado antes do laco: `prepare_bits`
            # troca este objeto quando um segundo diagrama entra, e nem sempre
            # ha' pausa pra reiniciar a thread. Custa uma leitura de atributo
            # contra um RTT de 3,1 ms.
            plan = self._plan
            t0 = time.monotonic()
            # Esta volta chegou a falar com o rele? Sem piso de periodo, e' o
            # que distingue um ciclo cujo custo E' a espera do rele (e ja se
            # paga) de um que voltou de graca e precisa do `IDLE_INTERVAL`.
            talked = False
            try:
                with state.lock:
                    wanted = set(state.wanted_bits)
                points = [p for p in plan
                          if not wanted or p.bit in wanted]
                digitals: dict = {}
                expected = len(points)
                # As folhas pedidas, SEM repetir: um ponto decorado
                # (`db:52A|52B?0:1:2:3`) poe dois bits no mesmo
                # `LN$FC$DO$DA`, e nomear a folha duas vezes so' gasta bytes
                # do TPDU. Ordem preservada -- o plano ja vem ordenado por
                # (LD, item) pra nao gastar uma requisicao a mais em cada
                # fronteira de lote.
                refs: list = []
                seen: set = set()
                for p in points:
                    ref = (p.ld, p.item)
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)
                # UMA Read nomeando todas as folhas da pagina -- a py61850
                # divide em quantos pedidos o `max_pdu_size` negociado exigir e
                # devolve os valores JA decodificados, na ordem em que foram
                # pedidos. Por isso o `zip` e' seguro: `read_refs` promete uma
                # resposta por par, sempre.
                values = dict(zip(refs, client.read_refs(refs), strict=True))
                talked = bool(refs)
                for p in points:
                    value = values.get((p.ld, p.item))
                    # Um acesso que falha volta `{"error": ...}` NO LUGAR do
                    # valor, e nao como excecao: um nome que este rele nao
                    # serve tira o bit da leitura sem derrubar os outros mil.
                    # `int(bool({...}))` daria 1 -- um bit ligado que ninguem
                    # leu, na tela de quem esta comissionando.
                    if value is None or isinstance(value, dict):
                        continue
                    if p.rule is not None:
                        # O item carrega mais de um bit e o valor e' um
                        # enumerado, nao um booleano: um Dbpos volta da
                        # py61850 como a STRING "10", e `bool("00")` e' True.
                        # `decode_bit` devolve `None` no valor que nao casa
                        # com alternativa nenhuma -- um Dbpos 3 (bad-state)
                        # contra um ponto `?1:2` --, e ai o bit sai do payload
                        # como qualquer leitura que nao aconteceu.
                        reading = decode_bit(p.rule, value)
                        if reading is None:
                            continue
                        digitals[p.bit] = reading
                        continue
                    digitals[p.bit] = int(bool(value))

                # Leitura parcial precisa aparecer, como no modo TAR: um bit
                # que nao veio some do payload e o diagrama o pinta como
                # indeterminado. Em comissionamento "nao consegui ler" e
                # "o rele nao sabe" sao coisas bem diferentes.
                err = ("" if len(digitals) >= expected else
                       f"leitura parcial: {len(digitals)}/{expected} bits")
                with state.lock:
                    state.error = err
                    if expected:
                        state.digitals = digitals
                    state.mark_updated()
                if digitals:
                    once.info("mms_first",
                              f"[poll] 1a leitura MMS ok: {len(digitals)} bits "
                              f"em lote, {1000 * (time.monotonic() - t0):.0f} ms")
            except Iec61850Error as e:
                with state.lock:
                    state.error = f"MMS: {e}"
                self.logger.warning("[glv] %s: erro MMS no polling: %s",
                                    self.key, e)
                return
            except Exception as e:
                with state.lock:
                    state.error = f"poll: {e}"
                self.logger.warning("[glv] %s: erro no polling MMS: %s",
                                    self.key, e)

            cycle = time.monotonic() - t0
            self._last_cycle = cycle
            sleep_for = max(0.0, self.effective_interval(interval, cycle) - cycle)
            if talked:
                self._record_cycle(cycle)
            else:
                # Nada foi pedido ao rele (pagina sem bit mapeado) ou o erro
                # voltou na hora: nao ha custo de rede pra medir, e com periodo
                # 0 este ramo seria um laco quente.
                sleep_for = max(sleep_for, IDLE_INTERVAL)
            if stop is None:
                time.sleep(sleep_for)
            elif stop.wait(timeout=sleep_for):
                return

    def _record_cycle(self, cycle: float) -> None:
        """Guarda os ultimos ciclos e resume um a cada 100.

        O custo do ciclo e' o unico numero que justifica todo o desenho deste
        transporte (leitura por `LN$FC`, so' a pagina aberta); medi-lo em campo
        e' o que diz se a medicao de bancada se sustenta na subestacao.
        """
        self._cycles.append(cycle)
        if len(self._cycles) < 100:
            return
        ordered = sorted(self._cycles)
        self.logger.info(
            "[glv] %s: 100 ciclos MMS -- mediana %.0f ms, pior %.0f ms",
            self.key, 1000 * ordered[len(ordered) // 2], 1000 * ordered[-1])
        self._cycles.clear()
