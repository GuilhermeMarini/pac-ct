"""The GLV's telnet transport: SEL Fast Message over a telnet to the relay.

It came out of `link.py` whole, and the bodies arrived without a single line
changed: the telnet path is the only one verified against a real relay, and
the comments in here record bench measurements (Example, 203.0.113.x), not
opinions. `RelayLink` kept the lifecycle -- identity, refcount, `LiveState`,
watchdog and the polling thread -- and this module kept everything that knows
how to speak SEL.

`abort()` closes the SOCKET, and that is not a detail: a `selprotopy` stuck on
a read swallows the exception and tries again, so closing the socket is the
only thing that wakes it. A transport with a real socket timeout does not need
that -- which is why `abort()` belongs to the transport, not to the generic
watchdog.
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

# The TARGET region on the SEL-411L has up to 500 rows (3004h..31f7h via
# MAP 1 TARGET BL); manual p. 10.5 says char[~488]. We use 500 as the cap.
MIN_ROWS_DESIRED = 500
# The 311C has 111 rows and answers with an error past that, so
# discover_all_rows stops on its own; 256 is only a safety ceiling.
TAR_MAX_ROWS = 256


def setup_relay(ip: str, port: int, acc_password: str, logger=None,
                on_socket=None) -> SELClient:
    """Opens the telnet, logs in and autoconfigures; returns the SELClient.

    It takes explicit IP, port and password on purpose. It used to read
    `cfg.get("tcp","ip_address")`, and the session loop WROTE into that same
    cfg when the user typed an IP on the selection screen. With one relay that
    was invisible; with two diagrams, opening the second one pointing at
    another IP rewrote the first one's IP -- which went on saying on screen
    that it was relay A and reconnected to relay B. config.ini is now only the
    source of the default values, read once at boot.
    """
    tn = telnetlib.Telnet(ip, port, timeout=10)
    # `connect()`'s watchdog needs the socket to be able to abort: a peer
    # that accepts TCP and never answers (a switch, a dead port forward)
    # leaves selprotopy's login and autoconfig reading for ever.
    if on_socket is not None:
        on_socket(tn)
    drain_login_banner(tn, logger)
    client = SELClient(tn, autoconfig_now=False, verbose=False)
    client.access_level_1(level_1_pass=acc_password.encode())
    client.autoconfig_relay_definition(attempts=3, verbose=False)
    client.autoconfig_fastmeter(attempts=3, verbose=False)
    if client.access_level()[0] == 0:
        client.access_level_1()
    # Read DNA (so that fast_meter_block works)
    client._read_clean_prompt()
    client._write(commands.DNA)
    client.dnaDef = sel_parser.relay_dna_block(
        client._read_command_response(commands.DNA),
        encoding="utf-8",
    )
    # Read the ID block (for the FID -- used by the cache lookup)
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
    """Read mode for this model. With no model, assumes 4xx (the default of
    an absent `fast_read`, as before)."""
    if relay_model is None:
        return MODE_TARGET
    if getattr(relay_model, "digitals_via_tar", False):
        return MODE_TAR
    if getattr(relay_model, "uses_target_region", True):
        return MODE_TARGET
    return MODE_FAST_METER


class TelnetTransport:
    """Fast Message over telnet: the half of old `RelayLink` that speaks SEL.

    It keeps the `SELClient`, the `AsciiTargetReader` and the family's read
    mode (`fast_read` from the model JSON). It knows nothing about diagrams,
    refcounts or progress beyond the `job` it is handed.
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
        # The raw telnet, kept so that `abort()` can close the socket from
        # another thread. It arrives through `setup_relay`'s `on_socket=`.
        self._tn = None
        self._lock = threading.RLock()
        # How far the watchdog deadline reaches. The setup (telnet + login
        # + autoconfig) has a deadline; bit discovery does not -- on a FID
        # with no cache it takes minutes of its own accord, and always has.
        self.setup_done = threading.Event()

    # -- conexao ------------------------------------------------------------

    def connect(self, job=None) -> None:
        """Opens the telnet, identifies the relay and builds the bit map.

        Raises on any failure: handling it is the `RelayLink`'s job, which
        turns the exception into `self.error` and leaves the diagram open and
        disconnected.
        """
        client = setup_relay(self.ip, self.port, self.acc_password, self.logger,
                             on_socket=self._on_socket)
        with self._lock:
            self.client = client
        self.fid = client.fid or ""
        self.devid = client.devid or ""
        # From here on the watchdog no longer rules: what comes next is
        # discovery.
        self.setup_done.set()

        if self.mode in (MODE_TARGET, MODE_TAR):
            self._setup_ascii_reader(job)
        else:
            self._log_fast_meter_digitals()

    def _on_socket(self, tn) -> None:
        self._tn = tn

    def abort(self) -> None:
        """Closes the socket to lift a stuck read.

        There is no interrupting a blocked read from outside; closing the
        socket makes it raise, which is what we want.
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
        """Which of THESE names do not exist on the relay, by two criteria.

        In the modes with an `AsciiTargetReader` (4xx and 3xx) the criterion
        is the Relay Word map: what is not in `bit_to_pos` after discovery
        will never be read. That deliberately covers both ways of being
        missing -- those looked up with `TAR <name>` that fell into the
        blacklist (`not_findable`), and the `VB*`, which discovery SKIPS
        because they live in another region and Fast Message does not bring
        them. That second case is exactly what sends the user to MMS, so
        hiding it for never having been looked up would hide the answer.

        On the 7xx there is no reader: the digitals are the subset the relay
        names in the DNA, and that is the criterion. `*` is an unnamed row,
        not a bit.

        `None` when disconnected, and `None` on a 7xx that did not answer the
        DNA: without one of the two sources, calling the drawing's 400 bits
        absent would be inventing.
        """
        wanted = drawing_variables(bits)
        # Without the `_lock`, on purpose: `prepare_bits` holds it for the
        # WHOLE discovery (~90 s on a cold TAR sweep), and the panel refreshes
        # precisely when the connection changes -- that is, while it is
        # running. This here only reads two dictionaries already in memory;
        # reading a just-swapped reader is a diagnosis one turn late, and the
        # lock exists to keep `client`/`reader` coherent in `close()`, not to
        # protect a read.
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
        """Telnet reads the whole Relay Word: there is nothing to report.

        `None` and not `0/N`: zero would sound like "nothing mapped" when the
        right answer is "does not apply", and that is why the client hides the
        badge instead of showing a number."""
        return None

    # -- descoberta ---------------------------------------------------------

    def _setup_ascii_reader(self, job=None) -> None:
        """Relay Word map + per-FID cache (SEL-4xx and SEL-3xx)."""
        logger = self.logger
        client = self.client
        reader = AsciiTargetReader(client, logger=logger)
        cache_path = AsciiTargetReader.cache_path_for(client.fid)
        have_cache = reader.load_cache(cache_path, fid=client.fid)
        self.reader = reader
        self._cache_path = cache_path

        if self.mode == MODE_TAR:
            # 3xx: `MAP 1 TARGET BL` answers "Invalid Command", so the only
            # discovery possible is sweeping `TAR 0..N`. It costs ~90s once;
            # after that the per-FID cache settles it.
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
            # Fast path: 1 round-trip via MAP 1 TARGET BL (~1-3s) instead
            # of ~500 TAR n calls (~40s). It saves a raw dump for inspection
            # in case the parser does not recognise the specific firmware
            # format.
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
        """Rebuilds bit_to_pos from row_to_names (an old cache does not
        always have every bit indexed, but the rows are there)."""
        reader = self.reader
        if reader is None:
            return
        for row_idx, names in reader.layout.row_to_names.items():
            for j, nm in enumerate(names):
                if nm and nm != "*" and nm.upper() not in reader.layout.bit_to_pos:
                    reader.layout.bit_to_pos[nm.upper()] = (row_idx, 7 - j)

    def prepare_bits(self, names, job=None, pause=None) -> int:
        """Discovers on the relay this diagram's bits not yet in the map.

        It has to stop the polling: there is only one telnet, and interleaving
        `TAR <name>` with the Fast Meter pipeline would scramble both answers.
        Stopping and starting again costs one poll turn, and avoids touching
        the `poll_loop*`, which were moved as they were.

        `pause` is that "stopping", and comes from the shell, which owns the
        thread. It goes in only around `discover_bits`, AFTER the early exits:
        on a 7xx there is no reader (the digitals come from Fast Meter) and on
        a FID with a complete cache there is no missing bit -- on those paths
        nobody talks to the relay, and stopping the reader would be tearing
        the thread down and back up for nothing.

        It is also what makes a SECOND diagram work on a connection that
        already exists: it brings bits nobody has asked the relay for yet.
        """
        logger = self.logger
        if pause is None:
            pause = nullcontext          # direct call (test, script)
        with self._lock:
            reader = self.reader
            client = self.client
            if reader is None or client is None:
                return 0   # 7xx: digitals come from Fast Meter, no discovery
            missing = [
                b for b in sorted(names)
                if b not in reader.layout.bit_to_pos
                and b not in reader.layout.not_findable  # tried, not found
                and not b.startswith("VB")               # GOOSE, other region
                and not b.isdigit()                      # constants "0", "12"
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
        """SEL-7xx (AG95-10 fast read): A5D1 already carries digitals via
        numdigitalbank/digitaloffset; selprotopy already called DNA/BNA in the
        autoconfig to populate client.dnaDef."""
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
        # Diagnostic: re-fetch A5C1 + DNA raw and save into cache/ when the
        # mismatch happens, for later inspection.
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
        """One read turn per mode, until `stop`. The dispatch is what used to
        be the body of `RelayLink._start_polling`, with the same arguments."""
        client = self.client
        if client is None:
            return
        if self.mode == MODE_TARGET:
            poll_loop(client, self.reader, state, interval, self.logger, stop,
                      once)
        elif self.mode == MODE_TAR:
            # 3xx: each row costs ~200ms on the relay, so the minimum
            # useful interval is larger than in the other modes -- and we only
            # read the open page.
            poll_loop_tar(client, self.reader, state, max(interval, 1.5),
                          self.logger, stop, once)
        else:
            poll_loop_fastmeter(client, state, interval, self.logger, stop,
                                once)
