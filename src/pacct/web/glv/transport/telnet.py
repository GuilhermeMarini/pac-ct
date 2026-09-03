"""O transporte telnet do GLV: SEL Fast Message por um telnet com o rele.

Saiu inteiro do `link.py`, e os corpos vieram sem uma linha de mudanca: o
caminho telnet e' o unico verificado contra rele de verdade, e os comentarios
em portugues aqui dentro registram medicoes de bancada (Exemplo,
203.0.113.x), nao opinioes. O `RelayLink` ficou com o ciclo de vida --
identidade, refcount, `LiveState`, watchdog e a thread de polling -- e este
modulo, com tudo que sabe falar SEL.

`abort()` fecha o SOCKET, e nao e' um detalhe: um `selprotopy` travado numa
leitura engole a excecao e tenta de novo, entao fechar o socket e' a unica
coisa que o acorda. Um transporte com timeout de socket de verdade nao precisa
disso -- por isso `abort()` e' do transporte, e nao do watchdog generico.
"""

from __future__ import annotations

import re
import threading
from contextlib import nullcontext

from pacct.core.relay_conn import drain_login_banner
from pacct.core.target_region import AsciiTargetReader
from pacct.paths import CACHE_DIR, PROJECT_ROOT
from pacct.web.glv.poll import (
    SELClient,
    commands,
    poll_loop,
    poll_loop_fastmeter,
    poll_loop_tar,
    sel_parser,
    telnetlib,
)
from pacct.web.glv.transport import (
    MODE_FAST_METER,
    MODE_TAR,
    MODE_TARGET,
    drawing_variables,
)

# A regiao TARGET no SEL-411L tem ate 500 rows (3004h..31f7h por
# MAP 1 TARGET BL); manual pag. 10.5 diz char[~488]. Usamos 500 como cap.
MIN_ROWS_DESIRED = 500
# O 311C tem 111 linhas e responde erro depois disso, entao discover_all_rows
# para sozinho; 256 e' so um teto de seguranca.
TAR_MAX_ROWS = 256


def setup_relay(ip: str, port: int, acc_password: str, logger=None,
                on_socket=None) -> SELClient:
    """Abre o telnet, faz login e autoconfig, e devolve o SELClient pronto.

    Recebe IP, porta e senha explicitos de proposito. Antes lia
    `cfg.get("tcp","ip_address")`, e o loop de sessao ESCREVIA nesse mesmo cfg
    quando o usuario digitava um IP na tela de selecao. Com um rele isso era
    invisivel; com dois diagramas, abrir o segundo apontando pra outro IP
    reescrevia o IP do primeiro -- que continuava dizendo na tela que era o
    rele A e reconectava no rele B. O config.ini agora e' so a fonte dos
    valores padrao, lida uma vez no boot.
    """
    tn = telnetlib.Telnet(ip, port, timeout=10)
    # O watchdog de `connect()` precisa do socket pra conseguir abortar: um
    # peer que aceita TCP e nunca responde (switch, port forward morto) deixa
    # o login e o autoconfig do selprotopy lendo pra sempre.
    if on_socket is not None:
        on_socket(tn)
    drain_login_banner(tn, logger)
    client = SELClient(tn, autoconfig_now=False, verbose=False)
    client.access_level_1(level_1_pass=acc_password.encode())
    client.autoconfig_relay_definition(attempts=3, verbose=False)
    client.autoconfig_fastmeter(attempts=3, verbose=False)
    if client.access_level()[0] == 0:
        client.access_level_1()
    # Le DNA (para fast_meter_block funcionar)
    client._read_clean_prompt()
    client._write(commands.DNA)
    client.dnaDef = sel_parser.relay_dna_block(
        client._read_command_response(commands.DNA),
        encoding="utf-8",
    )
    # Le ID block (para FID -- usado pelo lookup do cache)
    client._write(commands.ID)
    id_block = sel_parser.relay_id_block(
        client._read_command_response(commands.ID),
        encoding="utf-8",
    )
    client.fid = id_block.get("FID", "")
    client.bfid = id_block.get("BFID", "")
    client.devid = id_block.get("DEVID", "")
    return client


def mode_for(relay_model) -> str:
    """Modo de leitura pra esse modelo. Sem modelo, assume 4xx (o default do
    `fast_read` ausente, como antes)."""
    if relay_model is None:
        return MODE_TARGET
    if getattr(relay_model, "digitals_via_tar", False):
        return MODE_TAR
    if getattr(relay_model, "uses_target_region", True):
        return MODE_TARGET
    return MODE_FAST_METER


class TelnetTransport:
    """Fast Message por telnet: a metade do antigo `RelayLink` que fala SEL.

    Guarda o `SELClient`, o `AsciiTargetReader` e o modo de leitura da familia
    (`fast_read` do JSON do modelo). Nao sabe nada de diagrama, de refcount nem
    de progresso alem do `job` que recebe.
    """

    def __init__(self, ip: str, port: int, *, acc_password: str = "",
                 relay_model=None, logger=None):
        self.ip = ip
        self.port = port
        self.acc_password = acc_password or ""
        self.logger = logger
        self.key = f"{ip}:{port}"
        self.mode = mode_for(relay_model)
        self.fid = ""
        self.devid = ""
        self.client = None
        self.reader = None
        self._cache_path = None
        # O telnet cru, guardado pra `abort()` conseguir fechar o socket de
        # outra thread. Chega pelo `on_socket=` do `setup_relay`.
        self._tn = None
        self._lock = threading.RLock()
        # Ate onde o prazo do watchdog vale. O setup (telnet + login +
        # autoconfig) tem prazo; a descoberta de bits, nao -- num FID sem cache
        # ela leva minutos de propria vontade, e sempre levou.
        self.setup_done = threading.Event()

    # -- conexao ------------------------------------------------------------

    def connect(self, job=None) -> None:
        """Abre o telnet, identifica o rele e monta o mapa de bits.

        Levanta em qualquer falha: quem trata e' o `RelayLink`, que transforma
        a excecao em `self.error` e deixa o diagrama aberto e desconectado.
        """
        client = setup_relay(self.ip, self.port, self.acc_password, self.logger,
                             on_socket=self._on_socket)
        with self._lock:
            self.client = client
        self.fid = client.fid or ""
        self.devid = client.devid or ""
        # Daqui pra frente o watchdog nao manda mais: o que vem e' descoberta.
        self.setup_done.set()

        if self.mode in (MODE_TARGET, MODE_TAR):
            self._setup_ascii_reader(job)
        else:
            self._log_fast_meter_digitals()

    def _on_socket(self, tn) -> None:
        self._tn = tn

    def abort(self) -> None:
        """Fecha o socket pra levantar uma leitura travada.

        Nao da pra interromper uma leitura bloqueada de fora; fechar o socket
        faz ela levantar, que e' o que queremos.
        """
        tn = self._tn
        if tn is not None:
            try:
                tn.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            client, self.client, self.reader = self.client, None, None
        if client is not None:
            try:
                client.conn.close()
            except Exception:
                pass

    def unreachable(self, bits):
        """Quais DESTES nomes nao existem no rele, por dois criterios.

        Nos modos com `AsciiTargetReader` (4xx e 3xx) o criterio e' o mapa da
        Relay Word: o que nao esta em `bit_to_pos` depois da descoberta nao
        vai ser lido nunca. Isso engloba de proposito os dois jeitos de faltar
        -- os que foram procurados com `TAR <nome>` e cairam na lista negra
        (`not_findable`), e os `VB*`, que a descoberta PULA porque moram em
        outra regiao e o Fast Message nao os traz. Esse segundo caso e'
        exatamente o que manda o usuario pro MMS, entao esconde-lo por nunca
        ter sido procurado seria esconder a resposta.

        No 7xx nao ha reader: os digitais sao o subconjunto que o rele nomeia
        no DNA, e o criterio e' esse. `*` e' linha sem nome, e nao um bit.

        `None` desconectado, e `None` num 7xx que nao respondeu o DNA: sem uma
        das duas fontes, chamar os 400 bits do desenho de ausentes seria
        inventar.
        """
        wanted = drawing_variables(bits)
        # Sem o `_lock`, de proposito: `prepare_bits` o segura durante a
        # descoberta INTEIRA (~90 s numa varredura TAR fria), e o painel se
        # atualiza justamente quando a conexao muda -- ou seja, enquanto ela
        # roda. Isto aqui so' le dois dicionarios que ja estao na memoria; ler
        # um leitor recem-trocado e' um diagnostico uma volta atrasado, e o
        # lock existe pra manter `client`/`reader` coerentes no `close()`, nao
        # pra proteger uma leitura.
        client = self.client
        reader = self.reader
        if client is None:
            return None
        if self.mode in (MODE_TARGET, MODE_TAR):
            if reader is None:
                return None
            known = set(reader.layout.bit_to_pos)
            reason = "relay_word"
        else:
            dna = getattr(client, "dnaDef", None) or []
            known = {nm.upper() for row in dna for nm in row
                     if nm and nm != "*"}
            if not known:
                return None
            reason = "dna"
        return {"names": sorted(wanted - known), "reason": reason}

    def coverage_for(self, bits):
        """Telnet le a Relay Word inteira: nao ha o que reportar.

        `None` e nao `0/N`: zero soaria como "nada mapeado" quando o certo e'
        "nao se aplica", e e' por isso que o cliente esconde o badge em vez de
        mostrar um numero."""
        return None

    # -- descoberta ---------------------------------------------------------

    def _setup_ascii_reader(self, job=None) -> None:
        """Mapa da Relay Word + cache por FID (SEL-4xx e SEL-3xx)."""
        logger = self.logger
        client = self.client
        reader = AsciiTargetReader(client, logger=logger)
        cache_path = AsciiTargetReader.cache_path_for(client.fid)
        have_cache = reader.load_cache(cache_path, fid=client.fid)
        self.reader = reader
        self._cache_path = cache_path

        if self.mode == MODE_TAR:
            # 3xx: `MAP 1 TARGET BL` responde "Invalid Command", entao a unica
            # descoberta possivel e' varrer `TAR 0..N`. Custa ~90s uma vez;
            # depois disso o cache por FID resolve.
            if not have_cache:
                logger.info("Sem cache; varrendo TAR 0..N (3xx, ~1-2 min)...")
                if job:
                    job.stage("Mapeando a Relay Word via TAR (leva ~1 min)...", 30)
                reader.discover_all_rows(max_rows=TAR_MAX_ROWS)
                reader.save_cache(cache_path, fid=client.fid, devid=client.devid)
            else:
                logger.info(f"Cache TAR: {len(reader.layout.row_to_names)} linhas, "
                            f"{len(reader.layout.bit_to_pos)} bits.")
        elif not have_cache:
            # Fast path: 1 round-trip via MAP 1 TARGET BL (~1-3s) em vez de
            # ~500 chamadas TAR n (~40s). Salva dump cru para inspecao caso o
            # parser nao reconheca o formato do firmware especifico.
            logger.info("Sem cache; tentando MAP 1 TARGET BL (fast path)...")
            if job:
                job.stage("Descobrindo bits da regiao TARGET...", 30)
            dump_path = CACHE_DIR / f"{client.fid or 'unknown'}_map_bl.raw"
            added = reader.discover_via_map_bl(debug_dump=dump_path)
            if added == 0:
                logger.info("Fast path falhou; caindo para TAR 0..N (lento)...")
                reader.discover_all_rows(max_rows=MIN_ROWS_DESIRED)
            reader.save_cache(cache_path, fid=client.fid, devid=client.devid)
        elif len(reader.layout.row_to_names) < MIN_ROWS_DESIRED:
            already = (max(reader.layout.row_to_names.keys()) + 1
                       if reader.layout.row_to_names else 0)
            logger.info(
                f"Cache tem apenas {len(reader.layout.row_to_names)} linhas "
                f"(ultima: {already}). Tentando completar via MAP 1 TARGET BL..."
            )
            added = reader.discover_via_map_bl()
            if added == 0:
                reader.discover_all_rows(max_rows=MIN_ROWS_DESIRED)
            reader.save_cache(cache_path, fid=client.fid, devid=client.devid)

        self._reindex()
        logger.info(f"  {len(reader.layout.bit_to_pos)} bits no mapa, "
                    f"{len(reader.layout.row_to_names)} linhas")

    def _reindex(self) -> None:
        """Reconstroi bit_to_pos a partir de row_to_names (cache antigo nem
        sempre tem todos os bits indexados, mas as linhas estao la)."""
        reader = self.reader
        if reader is None:
            return
        for row_idx, names in reader.layout.row_to_names.items():
            for j, nm in enumerate(names):
                if nm and nm != "*" and nm.upper() not in reader.layout.bit_to_pos:
                    reader.layout.bit_to_pos[nm.upper()] = (row_idx, 7 - j)

    def prepare_bits(self, names, job=None, pause=None) -> int:
        """Descobre no rele os bits desse diagrama que ainda nao estao no mapa.

        Precisa parar o polling: o telnet e' um so, e intercalar `TAR <nome>`
        com o pipeline de Fast Meter embaralharia as duas respostas. Parar e
        subir de novo custa uma volta de poll, e evita mexer nas `poll_loop*`,
        que foram movidas como estavam.

        `pause` e' esse "parar", e vem da casca, que e' a dona da thread. Entra
        so em volta do `discover_bits`, DEPOIS das saidas rapidas: num 7xx nao
        ha reader (os digitais vem do Fast Meter) e num FID com cache completo
        nao ha bit faltando -- nesses caminhos ninguem fala com o rele, e parar
        o leitor seria derrubar e subir a thread de graca.

        E' tambem o que faz um SEGUNDO diagrama funcionar numa conexao que ja
        existe: ele traz bits que ninguem pediu ao rele ainda.
        """
        logger = self.logger
        if pause is None:
            pause = nullcontext          # chamada direta (teste, script)
        with self._lock:
            reader = self.reader
            client = self.client
            if reader is None or client is None:
                return 0   # 7xx: digitais vem do Fast Meter, sem descoberta
            missing = [
                b for b in sorted(names)
                if b not in reader.layout.bit_to_pos
                and b not in reader.layout.not_findable  # ja tentamos e nao achou
                and not b.startswith("VB")               # GOOSE em outra regiao
                and not b.isdigit()                      # constantes "0", "12"
            ]
            if not missing:
                logger.info(
                    "[glv] %s: todos os bits do diagrama ja sao conhecidos ou "
                    "estao na blacklist (%d bits)",
                    self.key, len(reader.layout.not_findable))
                return 0
            logger.info("[glv] %s: localizando %d bits faltantes via TAR <nome>...",
                        self.key, len(missing))
            if job:
                job.stage(f"Localizando {len(missing)} bits faltantes...", 85)
            before = len(reader.layout.bit_to_pos)
            before_failed = len(reader.layout.not_findable)
            with pause():
                try:
                    reader.discover_bits(missing)
                except Exception as e:
                    logger.warning("  Falha parcial na descoberta: %s", e)
                finally:
                    self._reindex()
            added = len(reader.layout.bit_to_pos) - before
            new_failed = len(reader.layout.not_findable) - before_failed
            logger.info(
                f"  +{added} bits descobertos, +{new_failed} marcados como "
                f"nao-findable (total {len(reader.layout.bit_to_pos)} bits, "
                f"{len(reader.layout.not_findable)} blacklist)")
            try:
                reader.save_cache(self._cache_path, fid=client.fid,
                                  devid=client.devid)
            except OSError as e:
                logger.warning("  Falha ao gravar cache de bits: %s", e)
            return added

    def _log_fast_meter_digitals(self) -> None:
        """SEL-7xx (AG95-10 fast read): A5D1 ja carrega digitals via
        numdigitalbank/digitaloffset; selprotopy ja chamou DNA/BNA no autoconfig
        pra popular client.dnaDef."""
        logger = self.logger
        client = self.client
        fmd = client.fast_meter_definition or {}
        nbanks = fmd.get("numdigitalbank", 0)
        offset = fmd.get("digitaloffset", -1)
        dna_rows = len(client.dnaDef) if client.dnaDef else 0
        logger.info(
            f"Fast Meter digitals: {nbanks} banks @ offset {offset} "
            f"(DNA={dna_rows} rows). Pulando AsciiTargetReader."
        )
        # Diagnostic: re-fetch A5C1 + DNA raw e salva em cache/ quando o
        # mismatch acontece, pra inspecao posterior.
        if nbanks == dna_rows:
            return
        try:
            dbg_dir = CACHE_DIR
            dbg_dir.mkdir(parents=True, exist_ok=True)
            safe_fid = re.sub(r"[^A-Za-z0-9._-]", "_", client.fid or "unknown")
            client._read_clean_prompt()
            client._write(client.fm_config_command_1 + commands.CR)
            a5c1_raw = client._read_to_prompt()
            (dbg_dir / f"{safe_fid}_A5C1.bin").write_bytes(bytes(a5c1_raw))
            client._read_clean_prompt()
            client._write(commands.DNA)
            dna_raw = client._read_command_response(commands.DNA)
            (dbg_dir / f"{safe_fid}_DNA.txt").write_bytes(bytes(dna_raw))
            a5_off = bytes(a5c1_raw).find(b"\xa5")
            a5_slice = bytes(a5c1_raw)[a5_off:a5_off + 32] if a5_off >= 0 else b""
            logger.warning(
                "Mismatch numdigitalbank(%d) vs DNA rows(%d). "
                "A5C1 head (32 bytes hex): %s ... A5C1+DNA dumps em %s/",
                nbanks, dna_rows, a5_slice.hex(' '),
                dbg_dir.relative_to(PROJECT_ROOT),
            )
        except Exception as e:
            logger.warning(f"Falha no dump diagnostico: {e}")

    # -- polling ------------------------------------------------------------

    def poll(self, state, interval, stop, once) -> None:
        """Uma volta de leitura por modo, ate `stop`. O despacho e' o que era
        o corpo de `RelayLink._start_polling`, com os mesmos argumentos."""
        client = self.client
        if client is None:
            return
        if self.mode == MODE_TARGET:
            poll_loop(client, self.reader, state, interval, self.logger, stop,
                      once)
        elif self.mode == MODE_TAR:
            # 3xx: cada linha custa ~200ms no rele, entao o intervalo minimo
            # util e' maior que o dos outros modos -- e so lemos a pagina aberta.
            poll_loop_tar(client, self.reader, state, max(interval, 1.5),
                          self.logger, stop, once)
        else:
            poll_loop_fastmeter(client, state, interval, self.logger, stop,
                                once)
