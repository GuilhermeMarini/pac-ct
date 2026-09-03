"""Which export path gets chosen, and the result of each."""

from __future__ import annotations

import olefile

from pacct.parsers import ole_rebuild as ore
from pacct.web.dnp_map import export as exp
from tests.test_set_dnp import SAMPLE_411L


def _make_rdb(tmp_path):
    """A fake RDB with one relay and two sessions, plus its extraction."""
    rdb = tmp_path / "obra.rdb"
    ore.write_ole(rdb, [
        ore.Entry(name="Relays", is_storage=True, size=0, read=None, children=[
            ore.Entry(name="R1", is_storage=True, size=0, read=None, children=[
                ore.Entry(name="SET_D1.TXT", is_storage=False,
                          size=len(SAMPLE_411L), read=lambda: SAMPLE_411L),
                ore.Entry(name="SET_D2.TXT", is_storage=False,
                          size=len(SAMPLE_411L), read=lambda: SAMPLE_411L),
            ]),
        ]),
    ])
    extract = tmp_path / "extracted"
    (extract / "Relays" / "R1").mkdir(parents=True)
    (extract / "Relays" / "R1" / "SET_D1.TXT").write_bytes(SAMPLE_411L)
    (extract / "Relays" / "R1" / "SET_D2.TXT").write_bytes(SAMPLE_411L)
    return rdb, extract


def test_a_same_length_edit_takes_the_in_place_path(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    # "PSV22" -> "PSV23": same byte count.
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_1": "PSV23"}}}, out)
    assert r.ok
    assert r.method == "in-place"
    ole = olefile.OleFileIO(str(out))
    try:
        got = ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read()
    finally:
        ole.close()
    assert b'BI_1,"PSV23"\x1c\r\n' in got
    assert len(got) == len(SAMPLE_411L)


def test_a_longer_edit_takes_the_rebuild_path(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    # "" -> "IN205": grows five bytes, does not fit.
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_2": "IN205"}}}, out)
    assert r.ok
    assert r.method == "rebuild"
    ole = olefile.OleFileIO(str(out))
    try:
        got = ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read()
        untouched = ole.openstream(["Relays", "R1", "SET_D2.TXT"]).read()
    finally:
        ole.close()
    assert b'BI_2,"IN205"\x1c\r\n' in got
    assert untouched == SAMPLE_411L


def test_a_single_growing_stream_forces_rebuild_for_all_of_them(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    r = exp.export(rdb, extract, {"R1": {
        "D1": {"BI_1": "PSV23"},     # fits
        "D2": {"BI_2": "IN205"},     # does not fit
    }}, out)
    assert r.ok
    assert r.method == "rebuild"
    assert r.streams == 2


def test_no_edits_is_refused(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    r = exp.export(rdb, extract, {}, tmp_path / "out.rdb")
    assert not r.ok
    assert "nenhuma altera" in r.error.lower()


def test_build_streams_returns_the_ole_path_of_each_edited_session(tmp_path):
    _rdb, extract = _make_rdb(tmp_path)
    streams = exp.build_streams(extract, {"R1": {"D1": {"BI_2": "IN205"}}})
    assert list(streams) == [("Relays", "R1", "SET_D1.TXT")]


def test_export_txt_writes_one_file_per_edited_session(tmp_path):
    _rdb, extract = _make_rdb(tmp_path)
    out_dir = tmp_path / "txt"
    paths = exp.export_txt(extract, {"R1": {
        "D1": {"BI_2": "IN205"}, "D2": {"BI_1": "LOP"},
    }}, out_dir)
    assert sorted(p.name for p in paths) == [
        "R1_SET_D1.TXT", "R1_SET_D2.TXT",
    ]
    assert b'BI_2,"IN205"' in (out_dir / "R1_SET_D1.TXT").read_bytes()


def test_a_failed_in_place_write_leaves_no_output_file(tmp_path, monkeypatch):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"

    # Sabotage the second write_stream call: this simulates a crash between
    # two writes, the exact gap a direct in-place write cannot survive.
    real_write_stream = olefile.OleFileIO.write_stream
    calls = {"n": 0}

    def flaky_write_stream(self, stream, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash mid-write")
        return real_write_stream(self, stream, data)

    monkeypatch.setattr(olefile.OleFileIO, "write_stream", flaky_write_stream)

    # Both edits fit exactly, so this stays on the in-place path, and there
    # are two streams to write, so the second call is reachable.
    r = exp.export(rdb, extract, {"R1": {
        "D1": {"BI_1": "PSV23"},
        "D2": {"BI_1": "PSV23"},
    }}, out)

    assert not r.ok
    assert not out.exists()
    leftovers = {p.name for p in tmp_path.iterdir()} - {"obra.rdb", "extracted"}
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_a_failed_in_place_write_does_not_damage_a_pre_existing_output(
        tmp_path, monkeypatch):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    out.write_bytes(b"an older export nobody wants damaged")

    real_write_stream = olefile.OleFileIO.write_stream
    calls = {"n": 0}

    def flaky_write_stream(self, stream, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash mid-write")
        return real_write_stream(self, stream, data)

    monkeypatch.setattr(olefile.OleFileIO, "write_stream", flaky_write_stream)

    r = exp.export(rdb, extract, {"R1": {
        "D1": {"BI_1": "PSV23"},
        "D2": {"BI_1": "PSV23"},
    }}, out)

    assert not r.ok
    assert out.read_bytes() == b"an older export nobody wants damaged"
    leftovers = {p.name for p in tmp_path.iterdir()} - {"obra.rdb", "extracted", "out.rdb"}
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_a_value_that_cannot_be_encoded_fails_the_export_instead_of_raising(
        tmp_path):
    """A value like a pasted en dash is not reachable from the shipped
    ``<input>`` (``handler._do_edit`` now rejects it, see
    ``set_dnp.check_value``), but is reachable via the JSON API, and
    ``build_streams``/``OleFileIO(...)`` used to sit outside any error
    boundary at all -- the request never answered. This is the seam finding
    #1 closes: a bad value must become ``ok=False``, never a stack trace on
    the wire.
    """
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_1": "IN101–spare"}}}, out)
    assert not r.ok
    assert r.error
    assert not out.exists()


def test_export_txt_sanitizes_a_crafted_ole_storage_name(tmp_path):
    """``relay`` comes verbatim from the RDB's OLE storage tree, which
    olefile preserves character for character -- CR/LF included. A crafted
    or corrupt RDB must not be able to turn that into a filename carrying a
    header-injection primitive for ``handler.py``'s ``/download``
    ``Content-Disposition``, nor a path separator that would make
    ``write_bytes`` raise after the RDB itself was already written.
    """
    crafted_relay = "R1\r\nX-Evil: injected"
    rdb = tmp_path / "obra.rdb"
    ore.write_ole(rdb, [
        ore.Entry(name="Relays", is_storage=True, size=0, read=None, children=[
            ore.Entry(name=crafted_relay, is_storage=True, size=0, read=None,
                      children=[
                          ore.Entry(name="SET_D1.TXT", is_storage=False,
                                    size=len(SAMPLE_411L),
                                    read=lambda: SAMPLE_411L),
                      ]),
        ]),
    ])
    extract = tmp_path / "extracted"
    (extract / "Relays" / crafted_relay).mkdir(parents=True)
    (extract / "Relays" / crafted_relay / "SET_D1.TXT").write_bytes(SAMPLE_411L)

    out_dir = tmp_path / "txt"
    written = exp.export_txt(extract, {crafted_relay: {"D1": {"BI_1": "PSV23"}}},
                             out_dir)
    assert len(written) == 1
    name = written[0].name
    assert "\r" not in name and "\n" not in name
    assert written[0].parent == out_dir  # no path separator escaped out_dir


def test_a_successful_in_place_export_still_produces_the_same_bytes(tmp_path):
    """The atomic-rename refactor must not change the happy-path output."""
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_1": "PSV23"}}}, out)
    assert r.ok
    assert r.method == "in-place"
    ole = olefile.OleFileIO(str(out))
    try:
        got = ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read()
        untouched = ole.openstream(["Relays", "R1", "SET_D2.TXT"]).read()
    finally:
        ole.close()
    assert b'BI_1,"PSV23"\x1c\r\n' in got
    assert len(got) == len(SAMPLE_411L)
    assert untouched == SAMPLE_411L
    # No leftover temp files after a clean run either.
    leftovers = {p.name for p in tmp_path.iterdir()} - {"obra.rdb", "extracted", "out.rdb"}
    assert not leftovers, f"temporary files left behind: {leftovers}"
