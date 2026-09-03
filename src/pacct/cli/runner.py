"""
PAC CT CLI: polling one relay from the terminal.

This script:
  1. Reads the settings from the `config.ini` file
  2. Opens a connection to the SEL relay (over TCP or Serial)
  3. Runs the autoconfiguration (discovers the relay's capabilities)
  4. Prints the device's identification information
  5. Polls Fast Meter (analogs + digitals) in a loop
  6. (Optional) Sends a Fast Operate command at the end

Usage:
    python example_usage.py
    python example_usage.py --config path/to/config.ini
"""

import argparse
import configparser
import logging
import sys

# `time.monotonic`, never `time.time`, for EVERY duration and every deadline
# in this file -- which is what it measures, from the first use of the clock
# to the last: there is no "time of day" record here. The reason is measured
# in docs/ENGINEERING-NOTES.md: on this machine the WSL wall clock sat 82.5 s behind the host
# and resynced, so `time.time()` jumped in both directions. On a turn that
# straddles the jump, `time.time() - t0` reads 82.3 s (the deadline expires
# at once) and, on a backward jump, it is NEGATIVE -- the deadline becomes
# 82 s in the future and the read hangs. The web path had already been
# converted along with the measurement; this file had been left behind, with
# the same 22 uses of the wrong clock.
import time
import warnings
from pathlib import Path

# `selprotopy/` lives at the project root (a sibling of `pacct/`). When this
# module is run via `python -m pacct.cli.runner` or by the app.py launcher
# from the root, sys.path already includes PROJECT_ROOT. We make sure of it
# here for the cases where the cwd is somewhere else.
from pacct.paths import PROJECT_ROOT, ensure_config_file

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Python 3.13+ removed `telnetlib` from the stdlib. `selprotopy` does a plain
# `import telnetlib`, so the shim has to come BEFORE importing it.
from pacct.compat import ensure_telnetlib

ensure_telnetlib()

# Import selprotopy BEFORE telnetlib so the null-byte patch is applied
import selprotopy  # noqa: F401,E402  (side effect: patches telnetlib)
from selprotopy import exceptions as sel_exceptions
from selprotopy.client.base import SELClient
from selprotopy.client.serial import SerialSELClient
from selprotopy.protocol import commands, parser

# Reading of the TARGET region (the full Relay Word)
from pacct.core.relay_conn import drain_login_banner
from pacct.core.target_region import AsciiTargetReader, get_target_reader

# Suppress telnetlib's DeprecationWarning (Python 3.12)
warnings.filterwarnings("ignore", category=DeprecationWarning, module="telnetlib")

# Resolved by ensure_telnetlib() above (stdlib or the telnetlib3 backport).
import telnetlib  # noqa: E402


def load_config(path: Path) -> configparser.ConfigParser:
    # `config/config.ini` is not versioned -- it is the file where the
    # relay's real ACC/2AC passwords are typed -- so on a clean clone it does
    # not exist. Seed it from the versioned model instead of dying on the
    # first run; if not even the model exists, then it does stop, saying what
    # is missing.
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
        # Uses telnetlib (with the null-byte patch applied by selprotopy)
        # instead of a raw socket, because SEL relays usually expect Telnet
        # negotiation on port 23.
        tn = telnetlib.Telnet(ip, port, timeout=10)
        # Without this, a login banner longer than ~5 lines exhausts
        # selprotopy's verification attempts and the connection fails.
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
    Defensive version of autoconfig().

    Calls each substep individually with a limited number of attempts and
    skips the parts that fail (instead of retrying forever). This works
    around known problems of selprotopy v0.1.4 with certain relay models
    (e.g. incomplete parse of the Fast Operate config block).
    """
    # 1. Relay definition (mandatory)
    logger.info("  -> Lendo Relay Definition Block...")
    client.autoconfig_relay_definition(attempts=3, verbose=verbose)

    # 2. Fast Meter (mandatory for polling)
    if client.fast_meter_supported:
        logger.info("  -> Lendo Fast Meter Config Block...")
        try:
            client.autoconfig_fastmeter(attempts=3, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.error(f"     Falha no Fast Meter: {e}")

    # 3. Fast Meter Demand (optional)
    if client.fast_meter_demand_supported:
        logger.info("  -> Lendo Fast Meter Demand Config Block...")
        try:
            client.autoconfig_fastmeter_demand(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Meter Demand: {e}")

    # 4. Fast Meter Peak Demand (optional)
    if client.fast_meter_peak_demand_supported:
        logger.info("  -> Lendo Fast Meter Peak Demand Config Block...")
        try:
            client.autoconfig_fastmeter_peakdemand(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Meter Peak Demand: {e}")

    # 5. Fast Operate (optional, off by default because it causes trouble)
    if client.fast_operate_supported and not skip_fast_operate:
        logger.info("  -> Lendo Fast Operate Config Block...")
        try:
            client.autoconfig_fastoperate(attempts=2, verbose=verbose)
        except sel_exceptions.MalformedByteArray as e:
            logger.warning(f"     Pulando Fast Operate: {e}")
    elif client.fast_operate_supported and skip_fast_operate:
        logger.info("  -> Pulando Fast Operate config (skip_fast_operate=True)")

    # 6. DNA (digital signal names) and ID (relay metadata)
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
    Consume everything in the buffer until it stays quiet for `quiet_period`.

    Guarantees that no residual byte from earlier responses is left behind.
    Optimised for low latency (cadence 5ms).
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
    Sends the Fast Meter command (`\\xa5\\xd1`) AND `VIEW 1:TARGET` pipelined,
    without waiting for the first response before sending the second. Reads
    both responses from the same stream. Saves ~50-80ms of RTT.

    Returns (fast_meter_data, target_raw_bytes_or_None).
    """
    from pacct.core.target_region import _RE_HEX_BYTE, _RE_TARGET_BYTES

    fm_marker = client.fm_command_1
    min_plausible_len = 100

    # Drain only if we do not know that it is clean
    if not getattr(client, "_buffer_clean", False):
        _drain_connection(client.conn, quiet_period=0.05, max_drain=0.3)

    client._buffer_clean = False

    # Send BOTH back to back, without waiting
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

        # 1. Look for the Fast Meter frame
        if fm_frame is None:
            idx = buf.find(fm_marker, search_start)
            if idx >= 0 and len(buf) - idx >= 3:
                declared_len = buf[idx + 2]
                if declared_len < min_plausible_len:
                    search_start = idx + 2
                elif len(buf) - idx >= declared_len:
                    fm_frame = bytes(buf[idx:idx + declared_len])

        # 2. Look for the VIEW 1:TARGET response
        if target_bytes is None:
            m = _RE_TARGET_BYTES.search(buf)
            if m:
                hex_section = m.group(1)
                hex_bytes = _RE_HEX_BYTE.findall(hex_section)
                if len(hex_bytes) >= 500:
                    target_bytes = bytes(int(h, 16) for h in hex_bytes[:500])

        # We leave once we have both
        if fm_frame is not None and target_bytes is not None:
            break
        # Or when the stream goes cold with at least the FM
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
    Robust Fast Meter polling that reads the complete binary frame.

    The library's `client.poll_fast_meter()` method ends the read early when
    it sees a `\\r=` prompt, even if the binary frame has not arrived in
    full. Here we read until we have the whole frame (length declared in
    byte 3 of the A5 header), checking that the declared size matches what
    was expected.
    """
    fm_marker = client.fm_command_1  # e.g. b'\xa5\xd1'
    # The response frame is >= 100 bytes long (a real FM is 252; truncated
    # frames/garbage are usually <20). Use that to filter out false
    # positives instead of trusting the config block's msglen.
    min_plausible_len = 100

    # 1. Drain only if the buffer is not known to be clean
    if not getattr(client, "_buffer_clean", False):
        _drain_connection(client.conn, quiet_period=0.05, max_drain=0.3)

    # 2. Send the Fast Meter command (no fixed pause)
    client._buffer_clean = False
    client._write(fm_marker + commands.CR)

    # 4. Read until we have a valid frame (polling cadence 5ms)
    buf = b""
    deadline = time.monotonic() + timeout
    search_start = 0  # where to restart the A5 search (skips false hits)

    while time.monotonic() < deadline:
        try:
            chunk = client.conn.read_very_eager()
        except Exception:
            chunk = b""
        if chunk:
            buf += chunk

        # Look for the header starting at search_start
        idx = buf.find(fm_marker, search_start)
        if idx >= 0 and len(buf) - idx >= 3:
            declared_len = buf[idx + 2]

            # Filter out false positives (residual garbage, small sizes)
            if declared_len < min_plausible_len:
                search_start = idx + 2
                continue

            # Valid size: wait for the whole frame to arrive
            if len(buf) - idx >= declared_len:
                frame = bytes(buf[idx:idx + declared_len])
                # The buffer may still hold a residual prompt (\r=>\x03);
                # waits another 30ms and drains it to leave it clean.
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
    """Convert a CSV string into a list of names (no spaces, uppercased)."""
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _format_analog(value, fmt: str) -> str:
    """Format an analog value in the chosen mode (raw/phasor/both)."""
    import cmath
    if isinstance(value, complex):
        mag = abs(value)
        ang_deg = cmath.phase(value) * 180.0 / 3.141592653589793
        if fmt == "raw":
            return f"{value}"
        if fmt == "phasor":
            return f"{mag:.4f} angulo {ang_deg:+.2f}graus"
        return f"{value}   |  {mag:.4f} angulo {ang_deg:+.2f}graus"
    # Scalar value
    if isinstance(value, float):
        return f"{value:.6g}"
    return f"{value}"


def _filter_signals(available: dict, wanted: list[str]) -> dict:
    """
    Filter a dict of signals by the wanted list (case-insensitive).
    An empty list -> returns everything. Signals not found stay as None.
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

    # --- Analogs --------------------------------------------------------------
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

    # --- Digitals -------------------------------------------------------------
    # "*"   -> shows everything
    # "*ON" -> shows only the ones that are active (= True/1)
    # empty -> shows no digitals
    # list  -> shows only the listed ones
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

    # Fast Operate requires level 2AC
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
        # 1. Connection --------------------------------------------------------
        client = connect(cfg, logger, verbose=verbose, debug=debug)
        logger.info("Conexao estabelecida com sucesso.")

        # 2. Login (raises to the configured minimum level) --------------------
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

        # 3. Autoconfiguration -------------------------------------------------
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

        # 4. Fast Meter polling -----------------------------------------------
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
        # Bit threshold for using TAR <bit> instead of VIEW 1:TARGET
        tar_threshold = cfg.getint("signals", "tar_threshold", fallback=2)
        # FM + VIEW 1:TARGET pipeline (only works when it is not TAR)
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

        # ---- Set up the TARGET region reader if digitals are wanted ---------
        target_reader = None
        if wanted_digitals and digitals_source != "fastmeter":
            logger.info("Inicializando leitor da regiao TARGET...")
            target_reader = get_target_reader(client, mode=digitals_source, logger=logger)
            if isinstance(target_reader, AsciiTargetReader):
                target_reader.tar_threshold = tar_threshold
                # Persistent cache (avoids rediscovering bits on every run)
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
                    # Check whether every requested bit is already cached
                    if wanted_digitals in (["*"], ["*ON"]):
                        # For "*" / "*ON" the cache covers everything (we
                        # sweep every row). If rows are already mapped, we
                        # assume it is complete
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
                            need_discovery = False  # already done above
                        else:
                            need_discovery = False

                if need_discovery:
                    # Pre-discover each requested bit's position (once only)
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

                # Save the cache (may include bits discovered just now)
                if cache_enabled:
                    target_reader.save_cache(
                        cache_path, fid=client.fid, devid=client.devid
                    )

        # Determines the polling strategy:
        #   pipeline   -> FM + VIEW 1:TARGET together (always the fastest
        #                 when available)
        #   tar        -> TAR <bit> per bit (only worth it without pipeline
        #                 and with few bits)
        #   sequential -> FM separate from VIEW 1:TARGET (fallback)
        is_wildcard = wanted_digitals in (["*"], ["*ON"]) if wanted_digitals else False
        strategy = "sequential"
        if target_reader is not None and wanted_digitals:
            if use_pipeline:
                # The pipeline is best in every case that uses VIEW
                strategy = "pipeline"
            elif not is_wildcard and len(wanted_digitals) <= tar_threshold:
                # Without the pipeline, TAR is faster for few bits
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
                    # FM + VIEW 1:TARGET pipelined (1 combined round-trip)
                    t0 = time.monotonic()
                    data, target_raw = pipelined_poll(client, target_reader, logger)
                    t_fm = time.monotonic() - t0
                    # Extract the bits from the bytes already received
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
                    # sequential or tar strategy
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
                                # read() picks TAR or VIEW by the threshold
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
            # Sleep only what is left of the interval (does not add on top)
            elapsed = time.monotonic() - t_start
            remaining = interval - elapsed
            if i < iterations and remaining > 0:
                time.sleep(remaining)

        # 5. Fast Operate (optional) ------------------------------------------
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
