"""The GLV's polling threads: one per connection to a relay.

Three modes, one per family (the model JSON's `fast_read` decides which):

  `poll_loop`            4xx -- Fast Meter + `VIEW 1:TARGET` pipelined
  `poll_loop_fastmeter`  7xx -- digitals inside the A5D1 banks (AG95-10)
  `poll_loop_tar`        3xx -- A5D1 analogs, digitals via `TAR <row>`

They came out of `dashboard.py` without a single line changed. All of them
take `(client, [reader,] state, interval, logger, stop_event)` and write into
the LiveState of the RelayLink that started them.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import warnings

from pacct.paths import PROJECT_ROOT

# `selprotopy/` lives in PROJECT_ROOT, outside the `pacct/` package.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Python 3.13+ removed `telnetlib` from the stdlib. `selprotopy` does `import
# telnetlib` directly, so the shim has to come BEFORE importing it.
from pacct.compat import ensure_telnetlib

ensure_telnetlib()


import selprotopy  # noqa: F401,E402

warnings.filterwarnings("ignore", category=DeprecationWarning, module="telnetlib")

# `commands` and `telnetlib` are REEXPORTED from here: the telnet transport
# imports them from this module, and not from the source, on purpose. This
# file is the only one that guarantees the ORDER -- `ensure_telnetlib()` runs
# above, before any `import telnetlib` -- and whoever imports telnetlib
# directly on 3.13+ gets an ImportError. They are used by
# `pacct.web.glv.transport.telnet`; the `noqa: F401` tells ruff they are not
# leftovers.
import telnetlib  # noqa: E402,F401

from selprotopy.client.base import SELClient  # noqa: E402
from selprotopy.protocol import commands  # noqa: E402,F401
from selprotopy.protocol import parser as sel_parser  # noqa: E402

from pacct.core.relay_conn import channel_for  # noqa: E402
from pacct.core.target_region import (  # noqa: E402
    TARGET_REGION_BYTES,
    AsciiTargetReader,
    target_bytes_from_stream,
)
from pacct.web.glv.state import LiveState  # noqa: E402

# --- Loop timings ---------------------------------------------------------
#
# All measured on the Example bench (203.0.113.x), with the real relays each
# mode serves: 411L-A-R133 and 451-5-R331 and 487E-3-R323 (4xx), 751-R402
# (7xx), 311C-1-R509 (3xx). They are not numbers picked at random, and
# changing them without a relay in front of you is changing them blind.

# Ceiling for draining what is left of a previous ASCII answer before sending
# the next Fast Meter. It only kicks in when the buffer is dirty
# (`_buffer_clean` false), which in practice is the first turn after an ASCII
# command.
DRAIN_DEADLINE_S = 0.3

# How long to wait for a Fast Meter round-trip's answer before giving up on
# the turn. Measured: the complete A5D1 answer arrives in ~40-90 ms on the 4xx
# and ~25 ms on the 751; 3 s is an order of magnitude of slack, not a typical
# value.
RESPONSE_DEADLINE_S = 3.0

# Once the Fast Meter frame has arrived, how much silence on the socket is
# enough to conclude that the rest (the pipelined TARGET) is not coming.
IDLE_AFTER_FM_S = 0.15

# The same silence, for the modes that pipeline nothing after the FM.
IDLE_NO_PIPELINE_S = 0.5

# Maximum wait in ONE call to the selector. The loop has deadlines of its
# own; the ceiling exists only so that a `stop_event` fired mid-turn does not
# stay stuck until the whole deadline.
SELECT_SLICE_S = 0.1


class FirstTimeLog:
    """Diagnostics worth exactly once per CONNECTION, not once per process.

    These lines exist to be read against a relay: "which analog channels does
    this firmware actually expose" is how you check a profile's
    `analog_name_aliases`, and the FM parse/timeout warnings say what the relay
    on the other end is doing wrong. All four used to be flags hung on the
    poll functions themselves (`poll_loop._logged_an_keys = True`), which is
    per PROCESS -- so the first relay connected after a restart got the
    diagnostics and every relay after it got silence. The GLV opens N diagrams
    over N relays; that made the logs useless exactly when they were needed.

    One of these lives on each `RelayLink`, so it is scoped to one telnet to
    one relay. `ensure_bits()` stops and restarts the polling thread when a
    second diagram joins, and that deliberately does NOT re-log -- it is the
    same connection to the same relay. A genuine reconnect builds a new
    `RelayLink` and therefore a new log.
    """

    __slots__ = ("_logger", "_seen")

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._seen: set[str] = set()

    def _first(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def info(self, key: str, msg: str) -> None:
        if self._first(key):
            self._logger.info(msg)

    def warning(self, key: str, msg: str) -> None:
        if self._first(key):
            self._logger.warning(msg)


def poll_loop(client: SELClient, reader: AsciiTargetReader,
              state: LiveState, interval: float, logger: logging.Logger,
              stop_event: threading.Event | None = None,
              once: FirstTimeLog | None = None):
    """Reads Fast Meter + TARGET in a loop, updating the LiveState.

    If `stop_event` is given, the loop ends as soon as it is signalled (used
    when the user clicks "Trocar GLE" to go back to the landing page).
    """
    # With no `once` from the RelayLink (a direct call, a test), the loop gets
    # its own -- the scope becomes the thread, which is still better than the
    # process.
    if once is None:
        once = FirstTimeLog(logger)
    # One channel per connection: the `AsciiTargetReader` picks up the SAME
    # object, and it is through it that the two agree on who drains the
    # buffer.
    ch = channel_for(client)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            # Quick drain
            ch.drain(DRAIN_DEADLINE_S)

            ch.mark_dirty()
            # Pipeline: FM + VIEW 1:TARGET together
            ch.send_fast_meter()
            ch.send_target_region()

            buf = b""
            fm_frame = None
            target_bytes = None
            wait_deadline = time.monotonic() + RESPONSE_DEADLINE_S
            last_data = time.monotonic()
            search_start = 0
            while time.monotonic() < wait_deadline:
                chunk = ch.read_available()
                if chunk:
                    buf += chunk
                    last_data = time.monotonic()
                # FM
                if fm_frame is None:
                    idx = buf.find(ch.fm_marker, search_start)
                    if idx >= 0 and len(buf) - idx >= 3:
                        dl = buf[idx + 2]
                        if dl < 100:
                            search_start = idx + 2
                        elif len(buf) - idx >= dl:
                            fm_frame = bytes(buf[idx:idx + dl])
                # TARGET
                if target_bytes is None:
                    target_bytes = target_bytes_from_stream(buf,
                                                            TARGET_REGION_BYTES)
                if fm_frame is not None and target_bytes is not None:
                    break
                if not chunk and time.monotonic() - last_data > IDLE_AFTER_FM_S \
                        and fm_frame:
                    break
                if not chunk:
                    ch.wait_readable(min(
                        SELECT_SLICE_S,
                        max(0.0, wait_deadline - time.monotonic())))

            ch.mark_clean()

            # Parse OUTSIDE the lock: `fast_meter_block` unpacks the whole
            # frame and the TARGET becomes up to 3.4 thousand bits: with that
            # inside `with state.lock`, every `/values` from the browser
            # waited on the parse instead of on a dict copy.
            new_analogs = None
            new_digitals = None
            parse_error = ""
            if fm_frame is not None:
                try:
                    fm_data = sel_parser.fast_meter_block(
                        fm_frame,
                        ch.fast_meter_definition,
                        ch.dna_definition,
                        verbose=False,
                    )
                    new_analogs = fm_data.get("analogs", {})
                    # One-shot log of the names the firmware exposes in
                    # the FM (to check against the relay model's
                    # analog_name_aliases).
                    keys = list(new_analogs.keys())
                    once.info(
                        "an_keys",
                        f"[poll] 1a leitura FM: {len(keys)} analog channels: "
                        f"{keys}",
                    )
                except Exception as e:
                    parse_error = f"FM parse: {e}"

            if target_bytes is not None:
                new_digitals = {}
                for row_idx, names in reader.layout.row_to_names.items():
                    if row_idx >= len(target_bytes):
                        continue
                    byte_val = target_bytes[row_idx]
                    for i, nm in enumerate(names):
                        if nm != "*":
                            new_digitals[nm] = int(bool(byte_val & (1 << (7 - i))))

            with state.lock:
                state.error = parse_error
                if new_analogs is not None:
                    state.analogs = new_analogs
                if new_digitals is not None:
                    state.digitals = new_digitals
                state.mark_updated()

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro: {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)


def poll_loop_fastmeter(client: SELClient, state: LiveState, interval: float,
                          logger: logging.Logger,
                          stop_event: threading.Event | None = None,
                          once: FirstTimeLog | None = None):
    """Polling loop for relays that carry digitals inside the Fast Meter
    answer (SEL-7xx: 751/787/etc.). Implements AG95-10 faithfully -- a single
    A5D1 round-trip brings analogs + N banks of 8 digital bits.

    Differences vs `poll_loop` (4xx):
      - No parallel `VIEW 1:TARGET` command (the 7xx does not expose a TARGET
        region via Fast Message).
      - Digitals come from `fm_data['digitals']` (assembled by
        `selprotopy.parser.fast_meter_block` out of
        `numdigitalbank`/`digitaloffset` + `dnaDef`).
      - No AsciiTargetReader -- the Relay Word exposed is the subset
        configured on the relay (BNA/DNA) and already comes ready with
        name->0/1 value.
    """
    # With no `once` from the RelayLink (a direct call, a test), the loop gets
    # its own -- the scope becomes the thread, which is still better than the
    # process.
    if once is None:
        once = FirstTimeLog(logger)
    ch = channel_for(client)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            # Drain residual ASCII (same logic as poll_loop)
            ch.drain(DRAIN_DEADLINE_S)

            ch.mark_dirty()
            ch.send_fast_meter()

            buf = b""
            fm_frame = None
            wait_deadline = time.monotonic() + RESPONSE_DEADLINE_S
            search_start = 0
            last_data = time.monotonic()
            while time.monotonic() < wait_deadline:
                chunk = ch.read_available()
                if chunk:
                    buf += chunk
                    last_data = time.monotonic()
                idx = buf.find(ch.fm_marker, search_start)
                if idx >= 0 and len(buf) - idx >= 3:
                    dl = buf[idx + 2]
                    if dl < 8:
                        # Invalid length byte -- keep looking
                        search_start = idx + 2
                    elif len(buf) - idx >= dl:
                        fm_frame = bytes(buf[idx:idx + dl])
                        break
                if (not chunk
                        and time.monotonic() - last_data > IDLE_NO_PIPELINE_S):
                    # No data and no prompt; end this turn
                    break
                if not chunk:
                    ch.wait_readable(min(
                        SELECT_SLICE_S,
                        max(0.0, wait_deadline - time.monotonic())))

            ch.mark_clean()

            # Parse outside the lock -- see the note in `poll_loop`.
            new_analogs = None
            new_digitals = None
            err = ""
            if fm_frame is not None:
                try:
                    fm_data = sel_parser.fast_meter_block(
                        fm_frame,
                        ch.fast_meter_definition,
                        ch.dna_definition,
                        verbose=False,
                    )
                    new_analogs = fm_data.get("analogs", {})
                    # selprotopy.fast_meter_block returns digitals as
                    # bool (via int_to_bool_list). The dashboard's JS uses
                    # `v === 0 || v === 1` (strict), so we have to serialise
                    # them as int -- otherwise everything goes indeterminate.
                    raw_digitals = fm_data.get("digitals", {})
                    new_digitals = {
                        k: int(bool(v)) for k, v in raw_digitals.items()
                    }
                    # Diagnostic once at startup: size of the block
                    # received, first 6 bits parsed.
                    sample = list(new_digitals.items())[:6]
                    once.info(
                        "fm_first",
                        f"[poll] 1a leitura FM ok: frame={len(fm_frame)}B, "
                        f"{len(new_digitals)} digitals, "
                        f"{len(new_analogs)} analogs. sample: {sample}",
                    )
                except Exception as e:
                    err = f"FM parse: {e}"
                    once.warning(
                        "fm_parse_err",
                        f"[poll] FM parse falhou (frame {len(fm_frame)}B): {e} | "
                        f"primeiros 32 bytes hex: {fm_frame[:32].hex(' ')}",
                    )
            else:
                err = "FM timeout"
                once.warning(
                    "fm_timeout",
                    f"[poll] FM timeout: nenhum frame A5D1 recebido em "
                    f"{RESPONSE_DEADLINE_S:.0f}s. "
                    f"buffer recebido ({len(buf)}B): {bytes(buf[:64]).hex(' ')}...",
                )

            with state.lock:
                state.error = err
                if new_analogs is not None:
                    state.analogs = new_analogs
                if new_digitals is not None:
                    state.digitals = new_digitals
                state.mark_updated()

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro (fastmeter): {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)


def _read_fast_meter_analogs(client: SELClient, logger: logging.Logger,
                             timeout: float = 3.0):
    """One A5D1 round-trip; returns `(analogs, error)`.

    Shared by the modes that take ONLY the analogs from the Fast Meter.
    """
    ch = channel_for(client)
    ch.drain(DRAIN_DEADLINE_S)

    ch.mark_dirty()
    ch.send_fast_meter()

    buf = b""
    fm_frame = None
    wait_deadline = time.monotonic() + timeout
    search_start = 0
    last_data = time.monotonic()
    while time.monotonic() < wait_deadline:
        chunk = ch.read_available()
        if chunk:
            buf += chunk
            last_data = time.monotonic()
        idx = buf.find(ch.fm_marker, search_start)
        if idx >= 0 and len(buf) - idx >= 3:
            dl = buf[idx + 2]
            if dl < 8:
                search_start = idx + 2
            elif len(buf) - idx >= dl:
                fm_frame = bytes(buf[idx:idx + dl])
                break
        if not chunk and time.monotonic() - last_data > IDLE_NO_PIPELINE_S:
            break
        if not chunk:
            ch.wait_readable(min(
                SELECT_SLICE_S, max(0.0, wait_deadline - time.monotonic())))
    ch.mark_clean()

    if fm_frame is None:
        return {}, "FM timeout"
    try:
        fm_data = sel_parser.fast_meter_block(
            fm_frame, ch.fast_meter_definition, ch.dna_definition, verbose=False,
        )
        return fm_data.get("analogs", {}), ""
    except Exception as e:
        return {}, f"FM parse: {e}"


def poll_loop_tar(client: SELClient, reader: AsciiTargetReader,
                  state: LiveState, interval: float, logger: logging.Logger,
                  stop_event: threading.Event | None = None,
                  once: FirstTimeLog | None = None):
    """Polling loop for the 3xx family (SEL-311C/311L).

    Neither the 4xx path nor the 7xx one serves here (measured on a
    SEL-311C-1-R509):

      - `VIEW 1:TARGET` / `MAP 1 TARGET BL` -> "Invalid Command".
      - A5D1 announces numdigitalbank=111, but the DNA block comes with 111
        rows of "*": no named bit, so the Fast Meter's digital half is
        undecipherable.

    What is left: A5D1 analogs (10 channels, those do work) + digitals via
    ASCII `TAR <row>`, 8 named bits per round-trip.

    Each `TAR` costs ~200ms ON THE RELAY -- pipelining several does not help
    (measured: 2.81s sequential vs 2.53s pipelined for 13 rows). That is why
    we read only the open page's bits (`state.wanted_bits`), and not the whole
    diagram: the heaviest page of the example GLE has 46 bits in 13 rows
    (~2.6s), against 41 rows (~8s) if we read everything.
    """
    logged_first = False
    prev_wanted: set[str] | None = None
    # With no `once` from the RelayLink (a direct call, a test), the loop gets
    # its own -- the scope becomes the thread, which is still better than the
    # process.
    if once is None:
        once = FirstTimeLog(logger)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            analogs, err = _read_fast_meter_analogs(client, logger)

            with state.lock:
                wanted = sorted(state.wanted_bits)
            digitals = {}
            if wanted:
                raw = reader.read_via_tar_rows(wanted)
                digitals = {k: v for k, v in raw.items() if v is not None}

            # A partial read has to show: a bit that was not read vanishes
            # from the payload and the diagram paints it indeterminate. Better
            # to say the read failed than to let it look like "unknown state
            # on the relay" -- in commissioning the two mean very different
            # things.
            readable = [b for b in wanted if b in reader.layout.bit_to_pos]
            page_changed = prev_wanted is not None and set(wanted) != prev_wanted
            if (readable and len(digitals) < len(readable)
                    and not page_changed and prev_wanted is not None):
                # On the first turn after a page change the requested set
                # changed midway (and new bits still go through discovery), so
                # "missing" there is a transition, not a failure.
                err = (f"{err} " if err else "") + (
                    f"leitura parcial: {len(digitals)}/{len(readable)} bits"
                )
            prev_wanted = set(wanted)

            with state.lock:
                state.error = err
                if analogs:
                    state.analogs = analogs
                if wanted:
                    state.digitals = digitals
                state.mark_updated()

            if not logged_first and (analogs or digitals):
                rows = len({reader.layout.bit_to_pos[b][0]
                            for b in wanted if b in reader.layout.bit_to_pos})
                logger.info(
                    f"[poll] 1a leitura TAR ok: {len(analogs)} analogs, "
                    f"{len(digitals)}/{len(wanted)} digitais em {rows} linha(s), "
                    f"{time.monotonic() - t0:.2f}s"
                )
                logged_first = True

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro (tar): {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)

