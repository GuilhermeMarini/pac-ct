"""
CLI do PAC CT: polling de um rele no terminal.

Este script:
  1. Le as configuracoes do arquivo `config.ini`
  2. Estabelece conexao com o rele SEL (via TCP ou Serial)
  3. Executa a autoconfiguracao (descobre capacidades do rele)
  4. Imprime as informacoes de identificacao do equipamento
  5. Faz polling de Fast Meter (analogicos + digitais) em um loop
  6. (Opcional) Envia um comando de Fast Operate ao final

Uso:
    python example_usage.py
    python example_usage.py --config caminho/para/config.ini
"""

import argparse
import configparser
import logging
import sys

# `time.monotonic`, nunca `time.time`, para TODA duracao e todo prazo deste
# arquivo -- que e' o que ele mede, do primeiro ao ultimo uso do relogio: nao
# ha aqui nenhum registro de "hora do dia". O motivo esta medido no docs/ENGINEERING-NOTES.md:
# nesta maquina o relogio de parede do WSL ficou 82,5 s atras do host e
# ressincronizou, entao `time.time()` pulava nos dois sentidos. Numa volta que
# atravessa o pulo, `time.time() - t0` da 82,3 s (prazo expira na hora) e, no
# pulo para tras, da NEGATIVO -- o prazo passa a ser 82 s no futuro e a leitura
# fica pendurada. O caminho web ja tinha sido convertido junto com a medicao;
# este arquivo tinha ficado para tras, com os mesmos 22 usos do relogio errado.
import time
import warnings
from pathlib import Path

# `selprotopy/` mora na raiz do projeto (irmao de `pacct/`). Quando esse
# modulo eh rodado via `python -m pacct.cli.runner` ou pelo app.py launcher
# a partir da raiz, o sys.path ja inclui PROJECT_ROOT. Garantimos isso aqui
# pra casos em que o cwd seja outro.
from pacct.paths import PROJECT_ROOT, ensure_config_file

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Python 3.13+ removeu `telnetlib` da stdlib. `selprotopy` faz `import
# telnetlib` direto, entao o shim precisa vir ANTES do import dele.
from pacct.compat import ensure_telnetlib

ensure_telnetlib()

# Importar selprotopy ANTES de telnetlib para aplicar o patch de bytes nulos
import selprotopy  # noqa: F401,E402  (efeito colateral: patch telnetlib)
from selprotopy import exceptions as sel_exceptions
from selprotopy.client.base import SELClient
from selprotopy.client.serial import SerialSELClient
from selprotopy.protocol import commands, parser

# Leitura da regiao TARGET (Relay Word completo)
from pacct.core.relay_conn import drain_login_banner
from pacct.core.target_region import AsciiTargetReader, get_target_reader

# Suprime DeprecationWarning do telnetlib (Python 3.12)
warnings.filterwarnings("ignore", category=DeprecationWarning, module="telnetlib")

# Resolvido pelo ensure_telnetlib() acima (stdlib ou backport do telnetlib3).
import telnetlib  # noqa: E402


def load_config(path: Path) -> configparser.ConfigParser:
    # `config/config.ini` nao e' versionado -- e' o arquivo onde se digitam as
    # senhas ACC/2AC reais do rele -- entao num clone limpo ele nao existe.
    # Semeia do modelo versionado em vez de morrer na primeira execucao; se nem
    # o modelo existir, ai sim para, dizendo o que falta.
    try:
        ensure_config_file(path, logging.getLogger("selprotopy_example"))
    except FileNotFoundError as exc:
        sys.exit(f"[ERRO] {exc}")
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(path, encoding="utf-8")
    return cfg


def setup_logger(log_file: str) -> logging.Logger:
    logger = logging.getLogger("selprotopy_example")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        file_h = logging.FileHandler(log_file, encoding="utf-8")
        file_h.setLevel(logging.DEBUG)
        file_h.setFormatter(fmt)
        logger.addHandler(file_h)

    return logger


def connect(cfg: configparser.ConfigParser, logger: logging.Logger,
            verbose: bool, debug: bool):
    conn_type = cfg.get("connection", "type", fallback="tcp").strip().lower()

    if conn_type == "tcp":
        ip = cfg.get("tcp", "ip_address")
        port = cfg.getint("tcp", "port", fallback=23)
        logger.info(f"Conectando via Telnet a {ip}:{port}...")
        # Usa telnetlib (com patch de bytes nulos aplicado pelo selprotopy)
        # em vez de socket raw, pois reles SEL geralmente esperam negociacao
        # Telnet na porta 23.
        tn = telnetlib.Telnet(ip, port, timeout=10)
        # Sem isso, um banner de login com mais de ~5 linhas estoura as
        # tentativas de verificacao do selprotopy e a conexao falha.
        drain_login_banner(tn, logger)
        return SELClient(
            connApi=tn,
            logger=logger,
            verbose=verbose,
            debug=debug,
            autoconfig_now=False,
        )

    if conn_type == "serial":
        port = cfg.get("serial", "port")
        baud = cfg.getint("serial", "baudrate", fallback=9600)
        logger.info(f"Conectando via Serial em {port} @ {baud} bps...")
        return SerialSELClient(
            port=port,
            baudrate=baud,
            logger=logger,
            verbose=verbose,
            debug=debug,
            autoconfig_now=False,
        )

    sys.exit(f"[ERRO] Tipo de conexao invalido: '{conn_type}'. Use 'tcp' ou 'serial'.")


def run_autoconfig(client, logger, verbose: bool, skip_fast_operate: bool = True):
    """
    Versao defensiva do autoconfig().

    Chama cada subpasso individualmente com numero limitado de tentativas
    e pula partes que falharem (em vez de retentar para sempre). Isso
    contorna problemas conhecidos da v0.1.4 do selprotopy com certos
    modelos de rele (ex.: parse incompleto do Fast Operate config block).
    """
    # 1. Definicao do rele (obrigatorio)
    logger.info("  -> Lendo Relay Definition Block...")
    client.autoconfig_relay_definition(attempts=3, verbose=verbose)

    # 2. Fast Meter (obrigatorio para polling)
    if client.fast_meter_supported:
        logger.info("  -> Lendo Fast Meter Config Block...")
        try:
            client.autoconfig_fastmeter(attempts=3, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.error(f"     Falha no Fast Meter: {e}")

    # 3. Fast Meter Demand (opcional)
    if client.fast_meter_demand_supported:
        logger.info("  -> Lendo Fast Meter Demand Config Block...")
        try:
            client.autoconfig_fastmeter_demand(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Meter Demand: {e}")

    # 4. Fast Meter Peak Demand (opcional)
    if client.fast_meter_peak_demand_supported:
        logger.info("  -> Lendo Fast Meter Peak Demand Config Block...")
        try:
            client.autoconfig_fastmeter_peakdemand(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Meter Peak Demand: {e}")

    # 5. Fast Operate (opcional, desligado por padrao por causar problemas)
    if client.fast_operate_supported and not skip_fast_operate:
        logger.info("  -> Lendo Fast Operate Config Block...")
        try:
            client.autoconfig_fastoperate(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Operate: {e}")
    elif client.fast_operate_supported and skip_fast_operate:
        logger.info("  -> Pulando Fast Operate config (skip_fast_operate=True)")

    # 6. DNA (nomes dos sinais digitais) e ID (metadados do rele)
    if client.access_level()[0] == 0:
        client.access_level_1()

    logger.info("  -> Lendo DNA Block (nomes de sinais digitais)...")
    client._read_clean_prompt()
    client._write(commands.DNA)
    client.dnaDef = parser.relay_dna_block(
        client._read_command_response(commands.DNA),
        encoding="utf-8",
        verbose=verbose,
    )

    logger.info("  -> Lendo ID Block (metadados)...")
    client._write(commands.ID)
    id_block = parser.relay_id_block(
        client._read_command_response(commands.ID),
        encoding="utf-8",
        verbose=verbose,
    )
    client.fid = id_block["FID"]
    client.bfid = id_block["BFID"]
    client.cid = id_block["CID"]
    client.devid = id_block["DEVID"]
    client.partno = id_block["PARTNO"]
    client.config = id_block["CONFIG"]


def _drain_connection(conn, quiet_period: float = 0.08, max_drain: float = 0.5):
    """
    Consome tudo que esta no buffer ate ficar quieto por `quiet_period`.

    Garante que nenhum byte residual de respostas anteriores fica para tras.
    Otimizado para baixa latencia (cadence 5ms).
    """
    deadline = time.monotonic() + max_drain
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        try:
            chunk = conn.read_very_eager()
        except Exception:
            chunk = b""
        if chunk:
            last_data = time.monotonic()
        else:
            if time.monotonic() - last_data >= quiet_period:
                return
        time.sleep(0.005)


def pipelined_poll(client, target_reader, logger,
                   timeout: float = 3.0) -> tuple[dict, bytes | None]:
    """
    Envia o comando Fast Meter (`\\xa5\\xd1`) E o `VIEW 1:TARGET` em pipeline,
    sem esperar a primeira resposta antes de mandar a segunda. Le ambas as
    respostas do mesmo stream. Economiza ~50-80ms de RTT.

    Retorna (fast_meter_data, target_raw_bytes_or_None).
    """
    from pacct.core.target_region import _RE_HEX_BYTE, _RE_TARGET_BYTES

    fm_marker = client.fm_command_1
    min_plausible_len = 100

    # Drain so se nao soubermos que esta limpo
    if not getattr(client, "_buffer_clean", False):
        _drain_connection(client.conn, quiet_period=0.05, max_drain=0.3)

    client._buffer_clean = False

    # Envia AMBOS em sequencia, sem esperar
    client._write(fm_marker + commands.CR)
    client._write(b"VIEW 1:TARGET\r\n")

    buf = b""
    fm_frame = None
    target_bytes = None
    deadline = time.monotonic() + timeout
    last_data = time.monotonic()
    search_start = 0

    while time.monotonic() < deadline:
        try:
            chunk = client.conn.read_very_eager()
        except Exception:
            chunk = b""
        if chunk:
            buf += chunk
            last_data = time.monotonic()

        # 1. Procura frame Fast Meter
        if fm_frame is None:
            idx = buf.find(fm_marker, search_start)
            if idx >= 0 and len(buf) - idx >= 3:
                declared_len = buf[idx + 2]
                if declared_len < min_plausible_len:
                    search_start = idx + 2
                elif len(buf) - idx >= declared_len:
                    fm_frame = bytes(buf[idx:idx + declared_len])

        # 2. Procura resposta de VIEW 1:TARGET
        if target_bytes is None:
            m = _RE_TARGET_BYTES.search(buf)
            if m:
                hex_section = m.group(1)
                hex_bytes = _RE_HEX_BYTE.findall(hex_section)
                if len(hex_bytes) >= 500:
                    target_bytes = bytes(int(h, 16) for h in hex_bytes[:500])

        # Saimos quando tem ambos
        if fm_frame is not None and target_bytes is not None:
            break
        # Ou quando o stream esfria com pelo menos o FM
        if not chunk and time.monotonic() - last_data > 0.15 and fm_frame is not None:
            break
        time.sleep(0.005)

    if fm_frame is None:
        raise TimeoutError(
            f"Frame Fast Meter incompleto em pipeline ({len(buf)} bytes recebidos)"
        )

    client._buffer_clean = True

    fm_data = parser.fast_meter_block(
        fm_frame,
        client.fast_meter_definition,
        client.dnaDef,
        verbose=False,
    )
    return fm_data, target_bytes


def robust_poll_fast_meter(client, logger, timeout: float = 5.0):
    """
    Polling de Fast Meter robusto que le o frame binario completo.

    O metodo `client.poll_fast_meter()` da biblioteca termina a leitura cedo
    quando ve um prompt `\\r=`, mesmo que o frame binario nao tenha chegado
    completo. Aqui lemos ate ter o frame inteiro (length declarado no byte 3
    do cabecalho A5), validando que o tamanho declarado bate com o esperado.
    """
    fm_marker = client.fm_command_1  # ex: b'\xa5\xd1'
    # O frame de resposta tem comprimento >= 100 bytes (FM real tem 252,
    # frames truncados/lixo costumam ter <20). Use isso para filtrar
    # falsos positivos em vez de confiar no msglen do config block.
    min_plausible_len = 100

    # 1. Drain so se buffer nao esta conhecidamente limpo
    if not getattr(client, "_buffer_clean", False):
        _drain_connection(client.conn, quiet_period=0.05, max_drain=0.3)

    # 2. Envia comando Fast Meter (sem pausa fixa)
    client._buffer_clean = False
    client._write(fm_marker + commands.CR)

    # 4. Le ate ter o frame valido (polling cadence 5ms)
    buf = b""
    deadline = time.monotonic() + timeout
    search_start = 0  # onde reiniciar a busca por A5 (pula falsos positivos)

    while time.monotonic() < deadline:
        try:
            chunk = client.conn.read_very_eager()
        except Exception:
            chunk = b""
        if chunk:
            buf += chunk

        # Procura cabecalho a partir de search_start
        idx = buf.find(fm_marker, search_start)
        if idx >= 0 and len(buf) - idx >= 3:
            declared_len = buf[idx + 2]

            # Filtra falsos positivos (lixo residual com tamanhos pequenos)
            if declared_len < min_plausible_len:
                search_start = idx + 2
                continue

            # Tamanho valido: espera o frame todo chegar
            if len(buf) - idx >= declared_len:
                frame = bytes(buf[idx:idx + declared_len])
                # Buffer ainda pode ter prompt residual (\r=>\x03); aguarda
                # mais 30ms e drena pra deixar limpo.
                time.sleep(0.03)
                try:
                    client.conn.read_very_eager()
                except Exception:
                    pass
                client._buffer_clean = True
                return parser.fast_meter_block(
                    frame,
                    client.fast_meter_definition,
                    client.dnaDef,
                    verbose=False,
                )
        time.sleep(0.005)

    raise TimeoutError(
        f"Frame Fast Meter incompleto apos {timeout}s "
        f"(recebido {len(buf)} bytes)"
    )


def print_relay_info(client, relay_name: str, description: str, logger):
    logger.info("=" * 60)
    logger.info(f"Rele: {relay_name}")
    if description:
        logger.info(f"Descricao: {description}")
    logger.info("-" * 60)
    logger.info(f"  FID    : {client.fid}")
    logger.info(f"  BFID   : {client.bfid}")
    logger.info(f"  CID    : {client.cid}")
    logger.info(f"  DEVID  : {client.devid}")
    logger.info(f"  PARTNO : {client.partno}")
    logger.info(f"  CONFIG : {client.config}")
    logger.info("-" * 60)
    logger.info(f"  Fast Meter            : {client.fast_meter_supported}")
    logger.info(f"  Fast Meter Demand     : {client.fast_meter_demand_supported}")
    logger.info(f"  Fast Meter Peak Demand: {client.fast_meter_peak_demand_supported}")
    logger.info(f"  Fast Operate          : {client.fast_operate_supported}")
    logger.info("=" * 60)


def _parse_signal_list(raw: str) -> list[str]:
    """Converte uma string CSV em lista de nomes (sem espacos, em maiusculas)."""
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _format_analog(value, fmt: str) -> str:
    """Formata um valor analogico conforme o modo escolhido (raw/phasor/both)."""
    import cmath
    if isinstance(value, complex):
        mag = abs(value)
        ang_deg = cmath.phase(value) * 180.0 / 3.141592653589793
        if fmt == "raw":
            return f"{value}"
        if fmt == "phasor":
            return f"{mag:.4f} angulo {ang_deg:+.2f}graus"
        return f"{value}   |  {mag:.4f} angulo {ang_deg:+.2f}graus"
    # Valor escalar
    if isinstance(value, float):
        return f"{value:.6g}"
    return f"{value}"


def _filter_signals(available: dict, wanted: list[str]) -> dict:
    """
    Filtra um dict de sinais pela lista desejada (case-insensitive).
    Lista vazia -> retorna tudo. Sinais nao encontrados ficam como None.
    """
    if not wanted:
        return dict(available)
    upper_map = {k.upper(): k for k in available.keys()}
    result = {}
    for name in wanted:
        original_key = upper_map.get(name)
        result[name] = available.get(original_key) if original_key else None
    return result


def print_meter_data(data: dict, iteration: int, logger,
                     wanted_analogs: list[str], wanted_digitals: list[str],
                     analog_format: str = "both"):
    logger.info(f"\n--- Leitura #{iteration} ---")
    analogs_all = data.get("analogs", {}) if data else {}
    digitals_all = data.get("digitals", {}) if data else {}

    if iteration == 1:
        logger.info(f"[DEBUG] Total de analogicos no frame: {len(analogs_all)}")
        logger.info(f"[DEBUG] Total de digitais no frame: {len(digitals_all)}")
        if digitals_all:
            sample = list(digitals_all.keys())[:20]
            logger.info(f"[DEBUG] Primeiros nomes digitais: {sample}")
        else:
            logger.info("[DEBUG] Nenhum digital foi parseado (digitals_all vazio)")

    # --- Analogicos -----------------------------------------------------------
    analogs_show = _filter_signals(analogs_all, wanted_analogs)
    if analogs_show:
        logger.info("Analogicos:")
        for name, value in analogs_show.items():
            if value is None:
                logger.info(f"  {name:<20} = [NAO ENCONTRADO]")
            else:
                logger.info(f"  {name:<20} = {_format_analog(value, analog_format)}")
    elif analogs_all:
        logger.info("(nenhum analogico configurado em [signals].analogs)")

    # --- Digitais -------------------------------------------------------------
    # "*"   -> mostra todos
    # "*ON" -> mostra apenas os que estao ativos (= True/1)
    # vazio -> nao mostra digitais
    # lista -> mostra apenas os listados
    if wanted_digitals == ["*"]:
        digitals_show = dict(digitals_all)
    elif wanted_digitals == ["*ON"]:
        digitals_show = {k: v for k, v in digitals_all.items() if v}
    elif wanted_digitals:
        digitals_show = _filter_signals(digitals_all, wanted_digitals)
    else:
        digitals_show = {}

    if digitals_show:
        logger.info("Digitais:")
        for name, value in digitals_show.items():
            if value is None:
                logger.info(f"  {name:<20} = [NAO ENCONTRADO]")
            else:
                logger.info(f"  {name:<20} = {int(bool(value))}")
    elif wanted_digitals and digitals_all:
        logger.info("(nenhum digital ativo / encontrado)")

    if not analogs_all and not digitals_all:
        logger.warning("Nenhum dado retornado pelo Fast Meter.")


def maybe_send_fast_operate(client, cfg: configparser.ConfigParser, logger):
    if not cfg.getboolean("fast_operate", "enabled", fallback=False):
        return

    ctrl_type = cfg.get("fast_operate", "control_type").strip().lower()
    ctrl_point = cfg.get("fast_operate", "control_point").strip()
    command = cfg.get("fast_operate", "command").strip().upper()

    logger.warning("=" * 60)
    logger.warning(f"!!! Enviando FAST OPERATE: {ctrl_type} {ctrl_point} {command} !!!")
    logger.warning("=" * 60)

    # Fast Operate exige nivel 2AC
    if not client.access_level_2(level_2_pass=cfg.get("auth", "ac2_password").encode()):
        logger.error("Falha ao subir para nivel 2AC. Comando cancelado.")
        return

    if ctrl_type == "remote_bit":
        client.send_remote_bit_fast_op(ctrl_point, command.lower())
    elif ctrl_type == "breaker_bit":
        client.send_breaker_bit_fast_op(ctrl_point, command.lower())
    else:
        logger.error(f"control_type invalido: {ctrl_type}")
        return

    logger.info("Comando enviado.")


def main():
    from pacct.paths import DEFAULT_CONFIG_FILE
    parser = argparse.ArgumentParser(description="CLI do PAC CT (polling no terminal)")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE),
        help="Caminho para o arquivo de configuracao (.ini)",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))

    verbose = cfg.getboolean("options", "verbose", fallback=False)
    debug = cfg.getboolean("options", "debug", fallback=False)
    log_file = cfg.get("options", "log_file", fallback="").strip()
    logger = setup_logger(log_file)

    relay_name = cfg.get("relay", "name", fallback="(sem nome)")
    description = cfg.get("relay", "description", fallback="")

    client = None
    try:
        # 1. Conexao -----------------------------------------------------------
        client = connect(cfg, logger, verbose=verbose, debug=debug)
        logger.info("Conexao estabelecida com sucesso.")

        # 2. Login (sobe para o nivel minimo configurado) ----------------------
        min_lvl = cfg.getint("auth", "min_access_level", fallback=1)
        if min_lvl >= 1:
            ok = client.access_level_1(
                level_1_pass=cfg.get("auth", "acc_password").encode()
            )
            logger.info(f"Login nivel 1 (ACC): {'OK' if ok else 'FALHOU'}")
        if min_lvl >= 2:
            ok = client.access_level_2(
                level_2_pass=cfg.get("auth", "ac2_password").encode()
            )
            logger.info(f"Login nivel 2 (2AC): {'OK' if ok else 'FALHOU'}")

        # 3. Autoconfiguracao --------------------------------------------------
        logger.info("Executando autoconfiguracao do rele...")
        skip_fo_config = not cfg.getboolean(
            "fast_operate", "enabled", fallback=False
        )
        run_autoconfig(
            client, logger, verbose=verbose, skip_fast_operate=skip_fo_config
        )
        print_relay_info(client, relay_name, description, logger)

        if not client.fast_meter_supported:
            logger.error("Rele nao suporta Fast Meter. Encerrando.")
            return

        # 4. Polling de Fast Meter --------------------------------------------
        iterations = cfg.getint("polling", "iterations", fallback=5)
        interval = cfg.getfloat("polling", "interval_seconds", fallback=2.0)

        wanted_analogs = _parse_signal_list(
            cfg.get("signals", "analogs", fallback="")
        )
        wanted_digitals = _parse_signal_list(
            cfg.get("signals", "digitals", fallback="")
        )
        analog_format = cfg.get(
            "signals", "analog_format", fallback="both"
        ).strip().lower()
        digitals_source = cfg.get(
            "signals", "digitals_source", fallback="ascii"
        ).strip().lower()
        # Threshold de bits para usar TAR <bit> em vez de VIEW 1:TARGET
        tar_threshold = cfg.getint("signals", "tar_threshold", fallback=2)
        # Pipeline FM + VIEW 1:TARGET (so funciona se nao for TAR)
        use_pipeline = cfg.getboolean("signals", "pipeline", fallback=True)

        if wanted_analogs:
            logger.info(f"Sinais analogicos configurados: {', '.join(wanted_analogs)}")
        else:
            logger.info("Sinais analogicos: TODOS")
        if wanted_digitals:
            logger.info(f"Sinais digitais configurados: {', '.join(wanted_digitals)}")
            logger.info(f"Fonte dos digitais: {digitals_source}")
        else:
            logger.info("Sinais digitais: (nenhum)")

        # ---- Inicializa leitor de TARGET region se ha digitais para ler -----
        target_reader = None
        if wanted_digitals and digitals_source != "fastmeter":
            logger.info("Inicializando leitor da regiao TARGET...")
            target_reader = get_target_reader(client, mode=digitals_source, logger=logger)
            if isinstance(target_reader, AsciiTargetReader):
                target_reader.tar_threshold = tar_threshold
                # Cache persistente (evita redescobrir bits a cada execucao)
                cache_enabled = cfg.getboolean(
                    "signals", "cache_enabled", fallback=True
                )
                force_rediscovery = cfg.getboolean(
                    "signals", "force_rediscovery", fallback=False
                )
                cache_path = AsciiTargetReader.cache_path_for(client.fid)

                cache_loaded = False
                if cache_enabled and not force_rediscovery:
                    cache_loaded = target_reader.load_cache(
                        cache_path, fid=client.fid
                    )

                need_discovery = True
                if cache_loaded:
                    # Verifica se todos os bits pedidos ja estao no cache
                    if wanted_digitals in (["*"], ["*ON"]):
                        # Para "*" / "*ON" o cache cobre tudo (varremos todas as linhas)
                        # Se ja temos linhas mapeadas, assumimos que esta completo
                        need_discovery = len(target_reader.layout.row_to_names) == 0
                    else:
                        missing = [
                            b for b in wanted_digitals
                            if b.upper() not in target_reader.layout.bit_to_pos
                        ]
                        if missing:
                            logger.info(
                                f"  bits ausentes no cache: {', '.join(missing)}"
                            )
                            target_reader.discover_bits(missing)
                            need_discovery = False  # ja foi feito acima
                        else:
                            need_discovery = False

                if need_discovery:
                    # Pre-descobre posicao dos bits pedidos (uma unica vez)
                    if wanted_digitals in (["*"], ["*ON"]):
                        logger.info("  varrendo todas as linhas da Relay Word (uma vez)...")
                        target_reader.discover_all_rows(max_rows=500)
                        logger.info(
                            f"  descobertos {len(target_reader.layout.bit_to_pos)} bits"
                        )
                    else:
                        logger.info("  descobrindo posicao de cada bit pedido...")
                        target_reader.discover_bits(wanted_digitals)

                found = sum(
                    1 for b in wanted_digitals
                    if b.upper() in target_reader.layout.bit_to_pos
                ) if wanted_digitals not in (["*"], ["*ON"]) else len(target_reader.layout.bit_to_pos)
                if wanted_digitals not in (["*"], ["*ON"]):
                    logger.info(f"  {found}/{len(wanted_digitals)} bits localizados")

                # Salva cache (pode incluir bits descobertos agora)
                if cache_enabled:
                    target_reader.save_cache(
                        cache_path, fid=client.fid, devid=client.devid
                    )

        # Determina estrategia de polling:
        #   pipeline   -> FM + VIEW 1:TARGET juntos (sempre o mais rapido qdo disponivel)
        #   tar        -> TAR <bit> por bit (so vale a pena sem pipeline e poucos bits)
        #   sequential -> FM separado de VIEW 1:TARGET (fallback)
        is_wildcard = wanted_digitals in (["*"], ["*ON"]) if wanted_digitals else False
        strategy = "sequential"
        if target_reader is not None and wanted_digitals:
            if use_pipeline:
                # Pipeline e o melhor em todos os casos com VIEW
                strategy = "pipeline"
            elif not is_wildcard and len(wanted_digitals) <= tar_threshold:
                # Sem pipeline, TAR e mais rapido para poucos bits
                strategy = "tar"
            else:
                strategy = "sequential"
        logger.info(f"Estrategia de polling: {strategy}")
        logger.info(f"Iniciando polling: {iterations} leituras a cada {interval}s")

        for i in range(1, iterations + 1):
            t_start = time.monotonic()
            t_fm = t_tgt = 0.0
            try:
                if strategy == "pipeline":
                    # FM + VIEW 1:TARGET em pipeline (1 round-trip combinado)
                    t0 = time.monotonic()
                    data, target_raw = pipelined_poll(client, target_reader, logger)
                    t_fm = time.monotonic() - t0
                    # Extrai bits dos bytes ja recebidos
                    if target_raw is not None:
                        t0 = time.monotonic()
                        tgt_digitals = {}
                        if wanted_digitals == ["*ON"]:
                            for row_idx, names in target_reader.layout.row_to_names.items():
                                if row_idx >= len(target_raw):
                                    continue
                                bv = target_raw[row_idx]
                                for j, nm in enumerate(names):
                                    if nm != "*" and (bv & (1 << (7 - j))):
                                        tgt_digitals[nm] = 1
                        elif wanted_digitals == ["*"]:
                            for row_idx, names in target_reader.layout.row_to_names.items():
                                if row_idx >= len(target_raw):
                                    continue
                                bv = target_raw[row_idx]
                                for j, nm in enumerate(names):
                                    if nm != "*":
                                        tgt_digitals[nm] = int(bool(bv & (1 << (7 - j))))
                        else:
                            for name in wanted_digitals:
                                pos = target_reader.layout.bit_to_pos.get(name.upper())
                                if pos is None or pos[0] >= len(target_raw):
                                    tgt_digitals[name] = None
                                else:
                                    row, msb = pos
                                    tgt_digitals[name] = int(bool(target_raw[row] & (1 << msb)))
                        data["digitals"] = tgt_digitals
                        t_tgt = time.monotonic() - t0
                else:
                    # Estrategia sequential ou tar
                    t0 = time.monotonic()
                    data = robust_poll_fast_meter(client, logger)
                    t_fm = time.monotonic() - t0

                    if target_reader is not None and wanted_digitals:
                        try:
                            t0 = time.monotonic()
                            if wanted_digitals == ["*ON"]:
                                tgt_digitals = target_reader.read_all_active()
                            elif wanted_digitals == ["*"]:
                                raw = target_reader.read_raw_bytes()
                                tgt_digitals = {}
                                for row_idx, names in target_reader.layout.row_to_names.items():
                                    if row_idx >= len(raw):
                                        continue
                                    bv = raw[row_idx]
                                    for j, nm in enumerate(names):
                                        if nm != "*":
                                            tgt_digitals[nm] = int(bool(bv & (1 << (7 - j))))
                            else:
                                # read() escolhe TAR ou VIEW conforme threshold
                                tgt_digitals = target_reader.read(wanted_digitals)
                            data["digitals"] = tgt_digitals
                            t_tgt = time.monotonic() - t0
                        except Exception as tgt_err:
                            logger.warning(f"  Falha ao ler TARGET: {tgt_err}")

                print_meter_data(
                    data, i, logger,
                    wanted_analogs=wanted_analogs,
                    wanted_digitals=wanted_digitals,
                    analog_format=analog_format,
                )
                t_iter = time.monotonic() - t_start
                logger.info(
                    f"  [timing] iter={t_iter*1000:.0f}ms "
                    f"(FastMeter={t_fm*1000:.0f}ms, TARGET={t_tgt*1000:.0f}ms)"
                )
            except Exception as e:
                logger.warning(f"Leitura #{i} falhou: {e}")
            # Sleep so o que falta pra completar o intervalo (nao adiciona em cima)
            elapsed = time.monotonic() - t_start
            remaining = interval - elapsed
            if i < iterations and remaining > 0:
                time.sleep(remaining)

        # 5. Fast Operate (opcional) ------------------------------------------
        maybe_send_fast_operate(client, cfg, logger)

    except KeyboardInterrupt:
        logger.warning("Interrompido pelo usuario.")
    except Exception as e:
        logger.exception(f"Erro fatal: {e}")
        sys.exit(1)
    finally:
        if client is not None:
            try:
                client.quit()
                if hasattr(client.conn, "close"):
                    client.conn.close()
                logger.info("Conexao encerrada.")
            except Exception:
                pass


if __name__ == "__main__":
    main()
