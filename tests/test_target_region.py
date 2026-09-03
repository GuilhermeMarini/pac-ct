"""Reading the SEL-4xx TARGET region -- the PARSING half only.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

**What is deliberately NOT tested here.** `AsciiTargetReader` talks to a relay
over a telnet socket: `_send_ascii`, `discover_bits`, `discover_via_map_bl`,
`discover_all_rows`, `_find_row_index_for`, `read_raw_bytes`, `read`,
`read_via_view`, `read_via_tar`, `read_via_tar_rows` and `read_all_active` all
depend on how a specific firmware answers within a quiet period measured in
tens of milliseconds. **Those cannot be verified without hardware, and no relay
is mocked here** -- a hand-written fake would only prove that the fake agrees
with the code that reads it, which is worth nothing and would make the real
failure modes (a truncated row, a banner crossing the response, a firmware that
answers `Invalid Command`) look covered.

What IS pure, and is covered: the regexes that turn a captured reply into
bytes, the parsers that turn one into names, the row -> (row, bit) layout
mapping, and the JSON cache. Every response below is a literal built from the
formats documented in the module and in docs/ENGINEERING-NOTES.md.

Why it matters: this layout is the ONLY thing that says which bit of which byte
is `TRIP`. An off-by-one in the MSB mapping does not fail -- it lights up the
neighbouring signal on the diagram, which reads as a perfectly plausible relay
state.
"""

from __future__ import annotations

import json

from pacct.core import target_region as tr


def _reader() -> tr.AsciiTargetReader:
    """A reader with no client. Every method exercised below is pure -- none of
    them touches `self.client`."""
    return tr.AsciiTargetReader(client=None)


#: A `MAP 1 TARGET BL` reply. First row carries the noise the real firmware
#: prints before the names (`TARGET   char[488]`); the rest are bare.
MAP_BL = (
    b"=>>MAP 1 TARGET BL\r\n"
    b"3004h   TARGET   char[488]   Z1T Z2T Z3T Z4T Z5T * * *\r\n"
    b"3005h   TRIP TRIPLED IN101 IN102 * * * *\r\n"
    b"3007h   PLT01 PLT02 PLT03 PLT04 ALT01 ALT02 * *\r\n"
    b"2000h   FORA FORA2 FORA3 FORA4 FORA5 FORA6 FORA7 FORA8\r\n"
    b"=>>"
)

#: A `TAR 5` reply: the echo, a row of eight names, a row of eight values.
TAR_ROW = (
    b"=>>TAR 5\r\n"
    b"\r\n"
    b"TRIP    TRIPLED IN101   IN102   *       *       *       *\r\n"
    b"  1       0       1       0       0       0       0       0\r\n"
    b"\r\n=>>"
)


# -----------------------------------------------------------------------------
# VIEW 1:TARGET -- the byte array
# -----------------------------------------------------------------------------

class TestTargetByteRegexes:

    def test_the_hex_array_is_extracted_from_the_reply(self):
        """`VIEW 1:TARGET` answers `TARGET = C0h,00h,...`. Fails if
        `_RE_TARGET_BYTES` stops accepting the spacing around `=`, or if
        `_RE_HEX_BYTE` stops requiring the trailing `h` -- the byte array would
        come back empty and every 4xx bit would read as indeterminate."""
        resp = b"=>>VIEW 1:TARGET\r\nTARGET = C0h,00h,02h,FFh\r\n=>>"
        section = tr._RE_TARGET_BYTES.search(resp).group(1)
        got = bytes(int(m.group(1), 16)
                    for m in tr._RE_HEX_BYTE.finditer(section))
        assert got == bytes([0xC0, 0x00, 0x02, 0xFF])

    def test_a_single_digit_byte_is_accepted(self):
        """Some firmwares print `0h` rather than `00h`. Fails if the `{1,2}`
        quantifier is tightened to `{2}` -- every short byte would drop out and
        SHIFT the whole array, moving every bit onto its neighbour's row."""
        got = [m.group(1) for m in tr._RE_HEX_BYTE.finditer(b"0h,1h,A0h")]
        assert got == [b"0", b"1", b"A0"]

    def test_the_match_is_case_insensitive_on_both_halves(self):
        """A lower-case `target =` and lower-case hex digits both occur. Fails
        if `re.IGNORECASE` is dropped from `_RE_TARGET_BYTES` or if
        `_RE_HEX_BYTE` narrows its character class."""
        section = tr._RE_TARGET_BYTES.search(b"target = c0h,ffh").group(1)
        assert [int(m.group(1), 16) for m in tr._RE_HEX_BYTE.finditer(section)
                ] == [0xC0, 0xFF]

    def test_the_array_stops_at_the_first_character_outside_the_class(self):
        """The capture is `[0-9A-Fa-fh,\\s]+`, so it runs to the first character
        that is none of those -- the `=` of the trailing `=>>` prompt here.
        Fails if the class is widened: the prompt and whatever follows it would
        be scanned for hex bytes."""
        resp = b"TARGET = C0h,00h\r\n=>>SHOW\r\nDEADh"
        assert tr._RE_TARGET_BYTES.search(resp).group(1) == b"C0h,00h\r\n"

    def test_a_reply_without_the_marker_does_not_match(self):
        """A relay that answers `Invalid Command` (every 3xx does) must produce
        no match, which is what makes `read_raw_bytes` raise instead of
        returning a plausible-looking empty array. Fails if the marker becomes
        optional."""
        assert tr._RE_TARGET_BYTES.search(b"Invalid Command\r\n=>>") is None


# -----------------------------------------------------------------------------
# MAP 1 TARGET BL -- the row -> names layout
# -----------------------------------------------------------------------------

class TestParseMapBl:

    def test_a_row_maps_eight_names_msb_first(self):
        """THE mapping. Names are printed most-significant bit first, so index
        `i` in the row is bit `7 - i` of that byte, and the row index is
        `addr - 0x3004`.

        Fails if the `7 - i` is dropped or the base address changes -- every
        bit on every 4xx diagram would show its neighbour's value, and nothing
        anywhere would report an error."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        assert r.layout.row_to_names[0] == [
            "Z1T", "Z2T", "Z3T", "Z4T", "Z5T", "*", "*", "*"]
        assert r.layout.bit_to_pos["Z1T"] == (0, 7)
        assert r.layout.bit_to_pos["Z5T"] == (0, 3)
        assert r.layout.bit_to_pos["TRIP"] == (1, 7)
        assert r.layout.bit_to_pos["ALT01"] == (3, 3)      # 3007h - 3004h

    def test_the_noise_before_the_names_is_skipped(self):
        """The first row prints `TARGET   char[488]` between the address and
        the names, so the parser takes the LAST eight name-shaped tokens.
        Fails if it starts taking the first eight -- row 0 would be named
        `TARGET`, `char[488]`, ..."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        assert "TARGET" not in r.layout.bit_to_pos
        assert r.layout.row_to_names[0][0] == "Z1T"

    def test_an_empty_slot_is_recorded_but_never_named(self):
        """`*` is a byte position with no Relay Word bit. It stays in
        `row_to_names` (the row is 8 wide, always) and never enters
        `bit_to_pos`. Fails if the `*` guard goes -- the poller would ask the
        relay for a bit called `*`."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        assert r.layout.row_to_names[0][5] == "*"
        assert "*" not in r.layout.bit_to_pos

    def test_an_address_outside_the_target_region_is_ignored(self):
        """`MAP 1 TARGET BL` prints other regions too. Fails if the
        3004h..3FFFh window goes: a row from another region would be filed at a
        negative index and read out of the wrong byte."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        assert "FORA" not in r.layout.bit_to_pos
        assert set(r.layout.row_to_names) == {0, 1, 3}

    def test_a_row_with_fewer_than_eight_names_is_skipped_whole(self):
        """A truncated line is not a partial row -- filing 6 names as if they
        were 8 would put every one of them on the wrong bit. Fails if the
        `len(name_tokens) != 8` guard loosens."""
        r = _reader()
        assert r._parse_map_bl(b"3004h   A B C D E F\r\n") == 0
        assert r.layout.row_to_names == {}

    def test_the_first_definition_of_a_name_wins(self):
        """A name repeated in two rows keeps the first position. Fails if the
        `not in` guard goes -- re-running discovery over a superset would
        silently move bits."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        r._parse_map_bl(b"3010h   TRIP A B C D E F G\r\n")
        assert r.layout.bit_to_pos["TRIP"] == (1, 7)

    def test_the_return_value_counts_only_newly_learned_bits(self):
        """The caller uses `added == 0` to decide whether the fast path worked
        at all and whether to fall back to the ~40 s `TAR 0..N` scan. Fails if
        it starts returning the total -- a firmware whose format is not
        recognised would look like a success on the second attempt."""
        r = _reader()
        first = r._parse_map_bl(MAP_BL)
        assert first == len(r.layout.bit_to_pos)
        assert r._parse_map_bl(MAP_BL) == 0

    def test_a_bit_name_starting_with_a_digit_is_read(self):
        """Pickup elements are named after their ANSI device number, so
        digit-initial names are not an edge case -- they are most of a
        protection Relay Word.

        The damage is quiet: `discover_via_map_bl` still returns a positive
        count from the rows that did survive, so the caller does NOT fall back
        to the slow `TAR 0..N` scan, and the partial layout is written to the
        per-FID cache (`glv/link.py:299`). Those bits are then rediscovered one
        round-trip at a time, and `read_all_active` never sees them at all.

        Was an xfail; the token pattern now starts with a digit."""
        r = _reader()
        r._parse_map_bl(b"3006h   50P1 50P2 51P 27P1 * * * *\r\n")
        assert r.layout.bit_to_pos["50P1"] == (2, 7)
        assert r.layout.row_to_names[2][2] == "51P"


    def test_a_digit_name_at_the_lsb_does_not_shift_the_whole_row(self):
        """The quieter half of the same bug, and the dangerous one.

        The scan walks the line from the right. A rejected token was SKIPPED
        while nothing had been collected yet -- a deliberate allowance for the
        `TARGET char[488]` noise a real relay puts at the start. So a
        digit-initial name in the LAST slot was stepped over, the scan kept
        going left to reach eight, and swallowed the literal word `TARGET`
        as if it were a bit. The row came back as
        ['TARGET', 'TRIP', 'Z1T', ...]: every real bit one position off, and
        `TRIP` read from bit 6 instead of bit 7.

        Nothing announced it. `discover_via_map_bl` returned a healthy count,
        the caller skipped the slow `TAR 0..N` fallback, the layout went into
        the per-FID cache, and the GLV painted a protection diagram with
        another bit's state.

        Fails if the token pattern goes back to requiring a leading letter."""
        r = _reader()
        r._parse_map_bl(
            b"3006h   TARGET   TRIP Z1T Z2T Z3T Z4T LT01 LT02 50P1\r\n")
        assert r.layout.row_to_names[2] == [
            "TRIP", "Z1T", "Z2T", "Z3T", "Z4T", "LT01", "LT02", "50P1"]
        assert r.layout.bit_to_pos["TRIP"] == (2, 7)
        assert r.layout.bit_to_pos["50P1"] == (2, 0)
        assert "TARGET" not in r.layout.bit_to_pos

    def test_the_noise_prefix_is_still_skipped(self):
        """The allowance the skip existed for must survive the fix: a real
        `MAP 1 TARGET BL` line carries `TARGET char[488]` before the names.

        Fails if the fix were done by removing the skip instead."""
        r = _reader()
        r._parse_map_bl(
            b"3006h   TARGET char[488]   TRIP Z1T Z2T Z3T 51T 67P1 79RS 50P1\r\n")
        assert r.layout.row_to_names[2] == [
            "TRIP", "Z1T", "Z2T", "Z3T", "51T", "67P1", "79RS", "50P1"]


# -----------------------------------------------------------------------------
# TAR <row> / TAR <bit>
# -----------------------------------------------------------------------------

class TestParseTar:

    def test_the_header_is_the_eight_token_line_that_is_not_all_bits(self):
        """`TAR` prints a name row and a value row, both eight wide, and the
        only thing telling them apart is that values are `0`/`1`. Fails if that
        heuristic goes -- the value row would be read as names, and a relay
        where every bit happens to be 0 would map eight bits called `0`."""
        assert _reader()._parse_tar_header(TAR_ROW) == [
            "TRIP", "TRIPLED", "IN101", "IN102", "*", "*", "*", "*"]

    def test_the_echo_and_the_blank_lines_are_skipped(self):
        """The reply opens with the echoed command and a blank line. Fails if
        the 8-token filter loosens: `=>>TAR 5` would be taken as a header."""
        assert _reader()._parse_tar_header(b"=>>TAR 5\r\n\r\n=>>") is None

    def test_a_reply_with_no_recognisable_row_is_none(self):
        """`None` is what `discover_bits` reads as 'this bit does not exist',
        which is how a SELOGIC equation name or a display point gets filed
        under `not_findable` instead of being asked for forever. Fails if a
        sentinel list is returned instead."""
        assert _reader()._parse_tar_header(b"Invalid Command\r\n=>>") is None

    def test_a_row_yields_both_the_names_and_the_values(self):
        """This is what lets one round trip read EIGHT bits instead of one --
        `TAR <bit>` and `TAR <row>` return the same thing. Fails if the value
        row stops being captured: `read_via_tar_rows` would fall back to a
        round trip per bit, ~200 ms each on a 3xx."""
        header, values = tr.AsciiTargetReader._parse_tar_row(TAR_ROW)
        assert header[:4] == ["TRIP", "TRIPLED", "IN101", "IN102"]
        assert values == [b"1", b"0", b"1", b"0", b"0", b"0", b"0", b"0"]

    def test_a_truncated_reply_with_only_a_header_is_none(self):
        """The relay pauses between the two rows and the quiet period can close
        the read early. `None` is what triggers the retry with a longer window
        in `read_via_tar_rows`. Fails if a header alone starts being accepted
        -- the retry would go and the bits would read as None instead."""
        assert tr.AsciiTargetReader._parse_tar_row(
            b"=>>TAR 5\r\nTRIP TRIPLED IN101 IN102 * * * *\r\n") is None

    def test_a_single_bit_is_looked_up_by_position_in_the_header(self):
        """`TAR <bit>` returns the whole row, so the answer is the value at the
        name's index. Fails if the index is taken from the wrong row -- the
        answer would be a real bit's value under the wrong name."""
        r = _reader()
        assert r._parse_tar_value(TAR_ROW, "TRIP") == 1
        assert r._parse_tar_value(TAR_ROW, "TRIPLED") == 0
        assert r._parse_tar_value(TAR_ROW, "IN101") == 1

    def test_a_name_absent_from_the_header_reads_none_not_zero(self):
        """`None` means 'unknown' and `0` means 'the relay says off' -- on the
        diagram those are grey and white, and conflating them would show a
        signal as confirmed de-energised when it was never read.

        Fails if the `except ValueError` starts returning 0."""
        assert _reader()._parse_tar_value(TAR_ROW, "NAO_EXISTE") is None

    def test_the_names_are_upper_cased(self):
        """`read_via_tar_rows` looks a bit up as `name.upper()`, so the header
        has to be stored the same way. Fails if either side stops normalising
        -- a lower-case name in a GLE would never resolve."""
        assert _reader()._parse_tar_header(
            b"trip tripled in101 in102 * * * *\r\n")[0] == "TRIP"


# -----------------------------------------------------------------------------
# The per-FID cache
# -----------------------------------------------------------------------------

class TestLayoutCache:

    def test_the_cache_file_is_named_after_the_sanitised_fid(self, tmp_path):
        """A FID is a free-form firmware string with slashes and spaces in it.
        Fails if the sanitisation goes -- the path would escape the cache
        directory."""
        p = tr.AsciiTargetReader.cache_path_for(
            "SEL-411L-R128-V0-Z003003-D20220101", cache_dir=tmp_path)
        assert p == tmp_path / "SEL-411L-R128-V0-Z003003-D20220101.json"
        assert tr.AsciiTargetReader.cache_path_for(
            "a/b c", cache_dir=tmp_path).name == "a_b_c.json"

    def test_an_empty_fid_still_produces_a_path(self, tmp_path):
        """A relay whose FID could not be read must not crash the connection.
        Fails if the `or "unknown"` fallback goes."""
        assert tr.AsciiTargetReader.cache_path_for(
            "", cache_dir=tmp_path).name == "unknown.json"

    def test_a_saved_layout_reloads_with_its_types_restored(self, tmp_path):
        """JSON has no tuples and no integer keys, so `load_cache` rebuilds
        both. Fails if either conversion goes: `row_idx` would be a string and
        every lookup into the byte array would raise.

        This is what saves ~90 s on every reconnection to a known firmware."""
        a = _reader()
        a._parse_map_bl(MAP_BL)
        a.layout.not_findable.add("naoexiste")
        path = tmp_path / "fid.json"
        a.save_cache(path, fid="FID-1", devid="DEV")

        b = _reader()
        assert b.load_cache(path, fid="FID-1") is True
        assert b.layout.bit_to_pos == a.layout.bit_to_pos
        assert b.layout.row_to_names == a.layout.row_to_names
        assert all(isinstance(k, int) for k in b.layout.row_to_names)
        assert all(isinstance(v, tuple) for v in b.layout.bit_to_pos.values())

    def test_the_not_findable_set_comes_back_upper_cased(self, tmp_path):
        """`read_via_tar_rows` consults it as `name.upper()`, so `load_cache`
        normalises on the way in even though `save_cache` writes whatever was
        in the set.

        Fails if the `.upper()` in the loader goes: a bit already known not to
        exist -- a SELOGIC equation name, a display point -- would be
        rediscovered on every single poll, one round trip each."""
        a = _reader()
        a.layout.not_findable.add("naoexiste")
        path = tmp_path / "fid.json"
        a.save_cache(path)
        assert json.loads(path.read_text())["not_findable"] == ["naoexiste"]

        b = _reader()
        b.load_cache(path)
        assert b.layout.not_findable == {"NAOEXISTE"}

    def test_a_cache_from_another_firmware_is_refused(self, tmp_path):
        """The Relay Word layout is a property of the FIRMWARE. Loading another
        one's map would put every bit on the wrong byte and report nothing.
        Fails if the FID check goes."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        path = tmp_path / "fid.json"
        r.save_cache(path, fid="FID-1")
        assert _reader().load_cache(path, fid="FID-2") is False

    def test_a_cache_with_no_recorded_fid_is_accepted(self, tmp_path):
        """`if fid and payload.get("fid")` -- an older cache written before the
        FID was known is still usable. Fails if the check becomes
        unconditional: every such cache would be discarded and re-discovered."""
        r = _reader()
        r._parse_map_bl(MAP_BL)
        path = tmp_path / "fid.json"
        r.save_cache(path, fid="")
        assert _reader().load_cache(path, fid="FID-1") is True

    def test_a_cache_of_the_wrong_version_is_refused(self, tmp_path):
        """`CACHE_VERSION` is the escape hatch for a layout format change.
        Fails if the check goes -- an old file would be loaded under the new
        reader's assumptions."""
        path = tmp_path / "fid.json"
        path.write_text(json.dumps({"version": tr.CACHE_VERSION + 1,
                                    "bit_to_pos": {}, "row_to_names": {}}))
        assert _reader().load_cache(path) is False

    def test_a_missing_or_corrupt_cache_is_a_false_not_a_crash(self, tmp_path):
        """A half-written file survives a `kill -9` during discovery. Fails if
        `JSONDecodeError` stops being caught -- the connection would fail
        instead of falling back to rediscovery."""
        assert _reader().load_cache(tmp_path / "nope.json") is False
        bad = tmp_path / "bad.json"
        bad.write_text('{"version": 1, "bit_to_pos"')
        assert _reader().load_cache(bad) is False

    def test_saving_creates_the_directory(self, tmp_path):
        """`cache/` is gitignored and may not exist on a fresh checkout. Fails
        if the `mkdir(parents=True)` goes."""
        path = tmp_path / "novo" / "sub" / "fid.json"
        _reader().save_cache(path, fid="F")
        assert path.is_file()

    def test_an_empty_layout_starts_empty(self):
        """`TargetLayout`'s three containers are per-instance defaults. Fails
        if any of them becomes a shared mutable default -- two relays would
        write into one map, which is the same bug class as a module-level
        session singleton."""
        a, b = tr.TargetLayout(), tr.TargetLayout()
        a.bit_to_pos["X"] = (0, 0)
        a.row_to_names[0] = ["X"] * 8
        a.not_findable.add("Y")
        assert (b.bit_to_pos, b.row_to_names, b.not_findable) == ({}, {}, set())
