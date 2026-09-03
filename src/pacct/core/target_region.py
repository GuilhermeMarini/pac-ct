"""
Access to the TARGET region of the Fast Message database on SEL-400 relays.

SEL-411L relays (and others in the 400 series) do NOT return usable names in
the Fast Meter DNA block -- the block comes back full of '*' placeholders.
The real Relay Word names (Z1T, 50P1, TRIP, etc.) live in another database
("Communications Database") reachable through two paths:

  1. ASCII via the `MAP 1:TARGET` / `VIEW 1:TARGET` / `TAR <bit>` commands
  2. Binary via the SEL Fast Message protocol (A546h header) with Function
     Codes 30h/31h/33h/10h

This module implements the ASCII path completely (it works end-to-end) and
provides a stub of the binary path explaining why it is not implementable
without additional documentation from SEL.

References:
  - SEL-411L Instruction Manual, pages 626-634 (Communications Database)
  - SEL-400 Series Manual, pages 1398-1400 (SEL Protocol section)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pacct.core.relay_conn import channel_for

CACHE_VERSION = 1

# Regex to extract the byte array from the VIEW 1:TARGET response
# Format:  "TARGET = C0h,00h,02h,...,FFh"
_RE_TARGET_BYTES = re.compile(rb"TARGET\s*=\s*([0-9A-Fa-fh,\s]+)", re.IGNORECASE)
_RE_HEX_BYTE = re.compile(rb"([0-9A-Fa-f]{1,2})h")

# Regex for the TAR <bit> header: a line with 8 names separated by spaces
# Ex: "Z1T     Z2T     Z3T     Z4T     Z5T     *       *       *"
_RE_TAR_HEADER = re.compile(rb"^([A-Za-z0-9_*]+(?:\s+[A-Za-z0-9_*]+){7})\s*$", re.MULTILINE)

# How many bytes the TARGET region of the Fast Message database carries.
# Measured on the 4xx bench relays: the 411L-A-R133 exposes 506 Relay Word
# rows and `VIEW 1:TARGET` returns the whole array; 500 is the floor the
# poll requires before it accepts a reading as complete.
TARGET_REGION_BYTES = 500


def target_bytes_from_stream(buf: bytes, count: int = TARGET_REGION_BYTES):
    """Extract the TARGET region bytes from a possibly incomplete stream.

    Returns `bytes` with exactly `count` positions, or `None` while the
    stream does not have all of them yet -- that is how the poll knows it
    still has to keep reading.

    It exists as a public function because the `VIEW 1:TARGET` format belongs
    to this module. Before, `poll_loop` imported `_RE_TARGET_BYTES` and
    `_RE_HEX_BYTE` from here -- two private names of another module -- and
    did it ON EVERY TURN of the wait loop, inside the hot path.
    """
    match = _RE_TARGET_BYTES.search(buf)
    if not match:
        return None
    hexes = _RE_HEX_BYTE.findall(match.group(1))
    if len(hexes) < count:
        return None
    return bytes(int(h, 16) for h in hexes[:count])

# Regex for lines with a hex address at the start. Captures lines such as:
#   "3004h   <8 names>"  or
#   "3004h   TARGET   char[488]   N1 N2 N3 N4 N5 N6 N7 N8"
# The first capture takes the address (3 or 4 hex digits + 'h'), the second
# takes the rest of the line (which will be parsed looking for the 8 names).
_RE_ADDR_LINE = re.compile(
    rb"^\s*([0-9A-Fa-f]{3,4})h?\s+(.+?)\s*$",
    re.MULTILINE,
)
# TARGET starts at 3004h on the SEL-411L. Accepts 3000h..3FFFh for safety.
_TARGET_ADDR_START = 0x3004
_TARGET_ADDR_END = 0x3FFF


@dataclass
class TargetLayout:
    """Mapping bit_name -> (row_index, bit_position) in the TARGET region."""
    bit_to_pos: dict = field(default_factory=dict)
    row_to_names: dict = field(default_factory=dict)  # row_idx -> [8 names]
    not_findable: set = field(default_factory=set)    # bits tried and not found


# =============================================================================
# ASCII path (WORKS)
# =============================================================================

class AsciiTargetReader:
    """
    Reads Relay Word bits through the ASCII MAP/VIEW/TAR commands.

    Typical use:
        reader = AsciiTargetReader(client)
        reader.discover_bits(['Z1T', '50P1', 'TRIP'])  # once
        values = reader.read(['Z1T', '50P1', 'TRIP'])  # on every poll
    """

    def __init__(self, client, logger=None):
        self.client = client
        self.logger = logger
        self._channel = None
        self.layout = TargetLayout()
        # Threshold: if `len(bits) <= tar_threshold`, use TAR <bit> instead
        # of VIEW 1:TARGET. TAR is lighter (~80ms) than VIEW (~220ms) for a
        # few bits, but for many bits VIEW is better (1 round-trip).
        self.tar_threshold = 2

    @property
    def channel(self):
        """The SAME channel the poll loop uses (`channel_for` memoises per
        client). It is through it that the two agree on whether the buffer is
        clean: that handshake used to be a `_buffer_clean` hung on the
        selprotopy object, written from both sides.

        Lazy on purpose: half of this class (loading/saving the cache,
        building the layout) talks to no relay at all, and the tests for that
        part build the reader with `client=None`.
        """
        if self._channel is None:
            self._channel = channel_for(self.client)
        return self._channel

    # --- persistent cache in a JSON file -------------------------------------

    @staticmethod
    def cache_path_for(relay_fid: str, cache_dir: Path | str | None = None) -> Path:
        """
        Compute the cache file path for the relay's FID.
        Sanitises the FID into a safe file name.
        """
        if cache_dir is None:
            from pacct.paths import CACHE_DIR
            cache_dir = CACHE_DIR
        else:
            cache_dir = Path(cache_dir)
        sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", relay_fid or "unknown")
        return cache_dir / f"{sanitized}.json"

    def load_cache(self, path: Path | str, fid: str = "") -> bool:
        """
        Try to load the layout (bit_to_pos + row_to_names) from the cache.
        Returns True if it loaded successfully, False if it does not exist /
        is invalid / has a different FID.
        """
        path = Path(path)
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            if self.logger:
                self.logger.warning(f"  cache invalido em {path}: {e}")
            return False

        if payload.get("version") != CACHE_VERSION:
            if self.logger:
                self.logger.info("  cache versao incompativel; ignorando")
            return False
        if fid and payload.get("fid") and payload["fid"] != fid:
            if self.logger:
                self.logger.info(
                    f"  cache pertence a outro firmware (cache={payload['fid']!r}, "
                    f"rele={fid!r}); ignorando"
                )
            return False

        bit_to_pos = payload.get("bit_to_pos", {})
        row_to_names = payload.get("row_to_names", {})
        not_findable = payload.get("not_findable", [])
        # JSON has neither tuples nor int keys; converting back:
        self.layout.bit_to_pos = {
            k: (int(v[0]), int(v[1])) for k, v in bit_to_pos.items()
        }
        self.layout.row_to_names = {
            int(k): list(v) for k, v in row_to_names.items()
        }
        self.layout.not_findable = set(s.upper() for s in not_findable)
        if self.logger:
            self.logger.info(
                f"  cache carregado de {path.name}: "
                f"{len(self.layout.bit_to_pos)} bits, "
                f"{len(self.layout.row_to_names)} linhas, "
                f"descoberto em {payload.get('discovered_at', '?')}"
            )
        return True

    def save_cache(self, path: Path | str, fid: str = "",
                   devid: str = "") -> None:
        """Save the current layout as JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "fid": fid,
            "devid": devid,
            "discovered_at": datetime.now().isoformat(timespec="seconds"),
            "bit_to_pos": {k: list(v) for k, v in self.layout.bit_to_pos.items()},
            "row_to_names": {str(k): v for k, v in self.layout.row_to_names.items()},
            "not_findable": sorted(self.layout.not_findable),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.logger:
            self.logger.info(
                f"  cache salvo em {path.name} "
                f"({len(self.layout.bit_to_pos)} bits)"
            )

    # --- basic communication -------------------------------------------------

    def _send_ascii(self, cmd: bytes, wait: float = 0.0,
                    quiet_period: float = 0.08,
                    deadline_s: float = 2.5) -> bytes:
        """
        Send an ASCII command and return the raw response.

        Optimised for low latency:
          - drain only if the buffer is not known to be clean
          - no `time.sleep(wait)` after the write
          - wait on the socket, not on a fixed 10ms
          - mark the channel clean when finished
        """
        # Always drain quickly (<15ms, avoids contamination between TAR's)
        try:
            for _ in range(3):
                if not self.channel.read_available():
                    break
                time.sleep(0.005)
        except Exception:
            pass

        self.channel.mark_dirty()
        self.channel.send_ascii(cmd)
        if wait > 0:
            time.sleep(wait)

        chunks = []
        deadline = time.monotonic() + deadline_s
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.channel.read_available()
            except Exception:
                chunk = b""
            if chunk:
                chunks.append(chunk)
                last_data = time.monotonic()
            elif time.monotonic() - last_data > quiet_period:
                break
            else:
                # Wait on the socket instead of waking up at 100 Hz. This
                # is where nearly all of the 3xx mode's CPU burn came from: a
                # `TAR <row>` costs ~200 ms ON THE RELAY, and the poll does
                # one per occupied row of the page -- measured on a
                # 311C-1-R509, 1913 empty socket reads in 20 s of polling.
                self.channel.wait_readable(
                    min(0.05, max(0.0, deadline - time.monotonic())))
        # Buffer drained down to quiet_period -> it is clean at the prompt
        self.channel.mark_clean()
        return b"".join(chunks)

    # --- discovery: find each requested bit's row/bit-position ---------------

    def discover_bits(self, bit_names: list[str]) -> dict[str, tuple[int, int]]:
        """
        For each requested bit, send `TAR <bit>` and find out which row and
        position it is in. Updates self.layout.bit_to_pos.

        Bits that fail (TAR returns nothing, or the row index cannot be
        located) are marked in layout.not_findable and skipped on later
        calls.

        Returns a map: {bit_name: (row, bit_position_msb)}.
        """
        for bit in bit_names:
            bit_upper = bit.upper()
            if bit_upper in self.layout.bit_to_pos:
                continue  # already known
            if bit_upper in self.layout.not_findable:
                continue  # we already know it is not found

            resp = self._send_ascii(f"TAR {bit_upper}".encode(), wait=1.0)
            row_info = self._parse_tar_header(resp)
            if row_info is None:
                self.layout.not_findable.add(bit_upper)
                if self.logger:
                    self.logger.warning(f"  bit '{bit}' nao encontrado via TAR")
                continue
            names_row = row_info
            try:
                _ = names_row.index(bit_upper)
            except ValueError:
                self.layout.not_findable.add(bit_upper)
                continue

            row_idx = self._find_row_index_for(names_row)
            if row_idx is None:
                self.layout.not_findable.add(bit_upper)
                if self.logger:
                    self.logger.warning(
                        f"  bit '{bit}' encontrado mas sem row index"
                    )
                continue

            self.layout.row_to_names[row_idx] = names_row
            for i, name in enumerate(names_row):
                if name == "*":
                    continue
                msb_pos = 7 - i
                self.layout.bit_to_pos[name] = (row_idx, msb_pos)

        return self.layout.bit_to_pos

    def discover_via_map_bl(self, debug_dump: Path | str | None = None) -> int:
        """
        Discover every Relay Word bit in a single `MAP 1 TARGET BL` call.

        Replaces the `TAR 0..499` sweep (~40s) with 1 round-trip (~1-3s).
        The command lists each byte of the TARGET region (3004h..) with its
        8 bit labels in MSB->LSB order (SEL-400 series manual, p. 14.46).

        Parameters
        ----------
        debug_dump : Path | str | None
            If given, saves the raw response to that file (useful for
            adjusting the parser if the firmware's format differs).

        Returns
        -------
        int : number of bits discovered. 0 means a parse failure (the caller
              may fall back to `discover_all_rows`).
        """
        # The response can reach ~30-50KB (488 bytes * 8 names * ~6 chars).
        # Longer quiet_period because the relay sometimes pauses between pages.
        resp = self._send_ascii(
            b"MAP 1 TARGET BL",
            wait=0.0,
            quiet_period=0.3,
            deadline_s=15.0,
        )
        if debug_dump:
            try:
                Path(debug_dump).parent.mkdir(parents=True, exist_ok=True)
                Path(debug_dump).write_bytes(resp)
                if self.logger:
                    self.logger.info(
                        f"  resposta crua de MAP 1 TARGET BL salva em {debug_dump} "
                        f"({len(resp)} bytes)"
                    )
            except OSError:
                pass

        added = self._parse_map_bl(resp)
        if self.logger:
            if added:
                self.logger.info(
                    f"  MAP 1 TARGET BL: {added} bits em "
                    f"{len(self.layout.row_to_names)} linhas"
                )
            else:
                self.logger.warning(
                    "  MAP 1 TARGET BL: nenhum bit reconhecido no response. "
                    "Verifique o dump bruto para ajustar o parser."
                )
        return added

    def _parse_map_bl(self, resp: bytes) -> int:
        """
        Parse the response of `MAP 1 TARGET BL`.

        Expected format (one line per TARGET byte):
            <addr>h   <8 names or '*'>
        or:
            <addr>h   TARGET   char[N]   <8 names or '*'>

        For each valid line in 3004h..31F7h, it computes
        `row_idx = addr - 3004h` and populates `layout.row_to_names[row_idx]`
        + `layout.bit_to_pos`.

        Returns the number of NEW bits added to `bit_to_pos`.
        """
        before = len(self.layout.bit_to_pos)
        # Token = a valid Relay Word identifier or '*' (empty slot).
        # Accepts names such as Z1T, 50P1, TRIPLED, AMV001, etc.
        #
        # The first character MAY be a digit, and that is not a detail: a
        # protection element is named after its ANSI number -- 50P1 and 50P2
        # (instantaneous overcurrent), 51T (time overcurrent), 27P1
        # (undervoltage), 59, 81, 3PO. They are most of the Relay Word of a
        # protection relay.
        #
        # The old regex required `[A-Za-z_]` at the start and got it wrong in
        # two ways, both silent. If a name with a digit fell in the middle of
        # the line, the reverse scan stopped and the WHOLE line vanished --
        # along with the other seven names. And if it fell at the end (the
        # LSB), it was skipped and the scan carried on leftwards until it had
        # eight, swallowing the word `TARGET` from the noise prefix as if it
        # were a bit: the line became ['TARGET', 'TRIP', 'Z1T', ...] and ALL
        # the real bits shifted one position. `TRIP` came to be read from bit
        # 6 instead of 7, and the GLV painted another bit's state on the
        # diagram with nothing looking wrong.
        tok_re = re.compile(rb"^[A-Za-z0-9_]+$")

        for m in _RE_ADDR_LINE.finditer(resp):
            try:
                addr = int(m.group(1), 16)
            except ValueError:
                continue
            if not (_TARGET_ADDR_START <= addr <= _TARGET_ADDR_END):
                continue
            tail = m.group(2)
            tokens = tail.split()
            # Take the LAST 8 tokens that are identifiers or '*'.
            # If the line has noise like "TARGET char[488]" before, ignore it.
            name_tokens = []
            for t in reversed(tokens):
                if t == b"*" or tok_re.match(t):
                    name_tokens.append(t)
                    if len(name_tokens) == 8:
                        break
                else:
                    # non-name token found: if we already have 8, ok; else reset
                    if name_tokens:
                        break
            if len(name_tokens) != 8:
                continue
            name_tokens.reverse()  # back to MSB->LSB order
            names = [t.decode("utf-8", errors="replace").upper() for t in name_tokens]

            row_idx = addr - _TARGET_ADDR_START
            self.layout.row_to_names[row_idx] = names
            for i, nm in enumerate(names):
                if nm == "*":
                    continue
                if nm not in self.layout.bit_to_pos:
                    self.layout.bit_to_pos[nm] = (row_idx, 7 - i)

        return len(self.layout.bit_to_pos) - before

    def discover_all_rows(self, max_rows: int = 500) -> dict[int, list[str]]:
        """
        Sweep `TAR 0`..`TAR max_rows-1` and populate the full name map.
        Use when the user asks for "*" or "*ON" (show everything).
        """
        for row in range(max_rows):
            if row in self.layout.row_to_names:
                continue
            resp = self._send_ascii(f"TAR {row}".encode(), wait=0.6)
            names = self._parse_tar_header(resp)
            if names is None:
                continue
            self.layout.row_to_names[row] = names
            for i, name in enumerate(names):
                if name == "*":
                    continue
                msb_pos = 7 - i
                if name not in self.layout.bit_to_pos:
                    self.layout.bit_to_pos[name] = (row, msb_pos)
            # Early-termination heuristic: give up only if we see a long
            # run of all-'*' rows. The SEL-411L's TARGET has several
            # stretches of 3+ all-'*' rows in the middle (77-79, 249-251,
            # 453-455, 489-491), so we require at least 30 consecutive empty
            # rows before deciding we have reached the end of the region.
            if all(n == "*" for n in names) and row > 30:
                empty_count = 1
                for next_row in range(row + 1, row + 30):
                    resp = self._send_ascii(f"TAR {next_row}".encode(), wait=0.4)
                    next_names = self._parse_tar_header(resp)
                    if next_names is None:
                        # TAR returned error/nothing -- probably out of range
                        empty_count += 1
                    elif all(n == "*" for n in next_names):
                        empty_count += 1
                        self.layout.row_to_names[next_row] = next_names
                    else:
                        # Found names -> not the end, resume the normal sweep
                        self.layout.row_to_names[next_row] = next_names
                        for i, name in enumerate(next_names):
                            if name == "*":
                                continue
                            if name not in self.layout.bit_to_pos:
                                self.layout.bit_to_pos[name] = (next_row, 7 - i)
                        empty_count = 0
                        break
                if empty_count >= 30:
                    break
        return self.layout.row_to_names

    def _parse_tar_header(self, resp: bytes) -> list[str] | None:
        """Extract the 8 names from the response of a TAR <x>."""
        # Strip command/echo lines
        for line in resp.split(b"\r"):
            line = line.strip()
            if not line:
                continue
            # Skip lines with numbers (those are the values)
            tokens = line.split()
            if len(tokens) != 8:
                continue
            # Heuristic: the header has letters; values are only 0 and 1
            if all(tok in (b"0", b"1") for tok in tokens):
                continue
            return [tok.decode("utf-8", errors="replace").upper() for tok in tokens]
        return None

    def _find_row_index_for(self, names: list[str]) -> int | None:
        """
        Given a set of 8 names (one row's header), find the absolute row
        number by searching incrementally via `TAR <row>`.
        Caches the result.
        """
        # Optimisation: many bits show up in the first ~80 rows
        # Upper bound = 500 because the TARGET region on the SEL-411L has up
        # to ~488 bytes (3004h..31f7h = 500 rows). Manual p. 10.5: TARGET
        # char[~488].
        for row in range(0, 500):
            if row in self.layout.row_to_names:
                if self.layout.row_to_names[row] == names:
                    return row
                continue
            resp = self._send_ascii(f"TAR {row}".encode(), wait=0.4)
            row_names = self._parse_tar_header(resp)
            if row_names is None:
                continue
            self.layout.row_to_names[row] = row_names
            if row_names == names:
                return row
        return None

    # --- reading: read all 500 bytes at once via VIEW 1:TARGET ---------------

    def read_raw_bytes(self) -> bytes:
        """Send VIEW 1:TARGET and return the ~500 Relay Word bytes."""
        resp = self._send_ascii(b"VIEW 1:TARGET", wait=0.0,
                                quiet_period=0.1, deadline_s=2.0)
        match = _RE_TARGET_BYTES.search(resp)
        if not match:
            raise ValueError(
                f"Resposta inesperada de VIEW 1:TARGET (sem 'TARGET ='): "
                f"{resp[:200]!r}"
            )
        hex_section = match.group(1)
        bytes_list = [int(m.group(1), 16) for m in _RE_HEX_BYTE.finditer(hex_section)]
        return bytes(bytes_list)

    def read(self, bit_names: list[str]) -> dict[str, int | None]:
        """
        Read the requested bits. Chooses automatically:
          - `TAR <bit>` per bit, if len <= tar_threshold (faster)
          - `VIEW 1:TARGET` (reads 500 bytes at once) otherwise
        """
        if 0 < len(bit_names) <= self.tar_threshold:
            return self.read_via_tar(bit_names)
        return self.read_via_view(bit_names)

    def read_via_view(self, bit_names: list[str]) -> dict[str, int | None]:
        """Read the whole TARGET via VIEW and extract the requested bits."""
        unknown = [b for b in bit_names if b.upper() not in self.layout.bit_to_pos]
        if unknown:
            self.discover_bits(unknown)

        raw = self.read_raw_bytes()
        result: dict[str, int | None] = {}
        for name in bit_names:
            pos = self.layout.bit_to_pos.get(name.upper())
            if pos is None:
                result[name] = None
                continue
            row_idx, msb_bit = pos
            if row_idx >= len(raw):
                result[name] = None
                continue
            byte_val = raw[row_idx]
            result[name] = int(bool(byte_val & (1 << msb_bit)))
        return result

    def read_via_tar(self, bit_names: list[str]) -> dict[str, int | None]:
        """
        Read each bit individually via `TAR <bit>` -- ~120ms per bit, but it
        does not require parsing the 500 bytes of the TARGET.

        Recommended only when the pipeline is off and there is 1 bit; in
        other cases use the pipeline path (FM+VIEW in 1 round-trip).
        """
        result = {}
        for name in bit_names:
            bit_upper = name.upper()
            # longer quiet_period: the relay may pause between names and values
            resp = self._send_ascii(
                f"TAR {bit_upper}".encode(),
                wait=0.0, quiet_period=0.12, deadline_s=1.5,
            )
            value = self._parse_tar_value(resp, bit_upper)
            result[name] = value
        return result

    @staticmethod
    def _parse_tar_row(resp: bytes) -> tuple[list[str], list[bytes]] | None:
        """Extract `(names, values)` from a TAR response.

        Both `TAR <bit>` and `TAR <row>` return the SAME thing: the whole
        8-bit row, with one line of names and one of 0/1 values. That is what
        makes it possible to read 8 bits per round-trip instead of 1.
        """
        header = None
        values = None
        for line in resp.split(b"\r"):
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if len(tokens) != 8:
                continue
            if all(tok in (b"0", b"1") for tok in tokens):
                values = tokens
            elif header is None:
                header = [t.decode("utf-8", errors="replace").upper() for t in tokens]
        if header is None or values is None:
            return None
        return header, values

    def _parse_tar_value(self, resp: bytes, bit_name: str) -> int | None:
        """Extract the value of a specific bit from the TAR <bit> response."""
        parsed = self._parse_tar_row(resp)
        if parsed is None:
            return None
        header, values = parsed
        try:
            idx = header.index(bit_name)
        except ValueError:
            return None
        return int(values[idx] == b"1")

    def read_via_tar_rows(self, bit_names: list[str]) -> dict[str, int | None]:
        """Read the requested bits, grouping them by Relay Word ROW.

        The path for relays with no reachable TARGET region (3xx): the
        SEL-311C answers `Invalid Command` to `VIEW 1:TARGET` and to
        `MAP 1 TARGET BL`, but answers `TAR <row>` normally -- and each
        response carries the 8 bits of the row. Grouping the requested bits
        by row drops the cost from one round-trip per BIT (`read_via_tar`) to
        one per OCCUPIED ROW.
        """
        # Excludes the ones we already tried and that do not exist in the
        # Relay Word (SELOGIC equations, display points, etc.). Without this
        # the poll rebuilds the discovery list every turn just to have it
        # thrown away again.
        unknown = [
            b for b in bit_names
            if b.upper() not in self.layout.bit_to_pos
            and b.upper() not in self.layout.not_findable
        ]
        if unknown:
            self.discover_bits(unknown)

        by_row: dict[int, list[str]] = {}
        result: dict[str, int | None] = {}
        for name in bit_names:
            pos = self.layout.bit_to_pos.get(name.upper())
            if pos is None:
                result[name] = None
                continue
            by_row.setdefault(pos[0], []).append(name)

        for row_idx in sorted(by_row):
            parsed = None
            # A row occasionally comes back truncated (the relay pauses
            # between the header and the values and the quiet_period closes
            # the read first). One retry with more slack fixes it, and it
            # only costs anything when it fails.
            for attempt, (quiet, deadline) in enumerate(((0.12, 1.5), (0.30, 2.5))):
                resp = self._send_ascii(
                    f"TAR {row_idx}".encode(),
                    wait=0.0, quiet_period=quiet, deadline_s=deadline,
                )
                parsed = self._parse_tar_row(resp)
                if parsed is not None:
                    break
                if self.logger is not None and attempt == 0:
                    self.logger.debug("  TAR %d veio incompleto; repetindo", row_idx)
            if parsed is None:
                for name in by_row[row_idx]:
                    result[name] = None
                continue
            header, values = parsed
            for name in by_row[row_idx]:
                try:
                    idx = header.index(name.upper())
                except ValueError:
                    result[name] = None
                    continue
                result[name] = int(values[idx] == b"1")
        return result

    def read_all_active(self) -> dict[str, int]:
        """
        Read ALL Relay Word bits and return only the active ones (=1).
        Requires a previous discover_all_rows().
        """
        raw = self.read_raw_bytes()
        active = {}
        for row_idx, names in self.layout.row_to_names.items():
            if row_idx >= len(raw):
                continue
            byte_val = raw[row_idx]
            for i, name in enumerate(names):
                if name == "*":
                    continue
                msb_bit = 7 - i
                if byte_val & (1 << msb_bit):
                    active[name] = 1
        return active


# =============================================================================
# Binary path (NOT IMPLEMENTED -- SEL documentation missing)
# =============================================================================

class BinaryTargetReader:
    """
    Stub for the binary path via the SEL Fast Message protocol (A546h).

    The A546h header and the Function Codes (30h/31h/33h/10h) are listed in
    the SEL-400 manual (Table 15.23), but the complete frame format (total
    length, payload layout, checksum computation, byte order, sequencing,
    etc.) is NOT in the public manual. The manual itself says on page 15.34:

        "you will also need to contact SEL for Fast Message protocol details"

    We probed the relay with several plausible formats (A5 46 06 30 ...,
    A5 46 05 30 ..., etc.) and the relay merely echoed bytes without
    answering. Without the official documentation, any implementation would
    be guesswork and would not work reliably.

    Alternatives for efficient binary access:
      - Request the Fast Message specification from SEL directly
      - Use DNP3 (described in Section 16 of the SEL-400 manual)
      - Use IEC 61850 GOOSE/MMS if available on the relay

    For practical use, the AsciiTargetReader is enough: the TARGET region
    updates every 0.5s, and VIEW 1:TARGET returns 500 bytes at once
    (1 round-trip per reading).
    """

    def __init__(self, client, logger=None):
        self.client = client
        self.logger = logger
        self._channel = None

    def read(self, bit_names: list[str]) -> dict:
        raise NotImplementedError(
            "Acesso binario via SEL Fast Message (A546h) nao e suportado.\n"
            "O formato do frame nao esta documentado publicamente -- o manual\n"
            "do rele instrui a 'contact SEL for Fast Message protocol details'.\n"
            "\n"
            "Use o caminho ASCII (AsciiTargetReader) em vez disso. Ele\n"
            "tambem cumpre a taxa de atualizacao de 0.5s da regiao TARGET\n"
            "e ja esta implementado e testado."
        )

    def read_all_active(self) -> dict:
        return self.read([])


# =============================================================================
# Factory
# =============================================================================

def get_target_reader(client, mode: str = "ascii", logger=None):
    """Return the appropriate reader ('ascii' or 'binary')."""
    mode = (mode or "ascii").strip().lower()
    if mode == "binary":
        return BinaryTargetReader(client, logger=logger)
    return AsciiTargetReader(client, logger=logger)
