"""Writing a valid Compound File, read back by olefile."""

from __future__ import annotations

import struct
from pathlib import Path

import olefile
import pytest

from pacct.parsers import ole_rebuild as ore


def _stream(name: str, data: bytes) -> ore.Entry:
    return ore.Entry(name=name, is_storage=False, size=len(data),
                     read=lambda d=data: d, children=[])


def _storage(name: str, children) -> ore.Entry:
    return ore.Entry(name=name, is_storage=True, size=0, read=None,
                     children=list(children))


def test_writes_a_file_olefile_recognises(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Hello", b"world")])
    assert olefile.isOleFile(str(dst))


def test_a_small_stream_roundtrips_through_the_mini_fat(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Small", b"abc")])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Small"]).read() == b"abc"
        assert ole.get_size(["Small"]) == 3
    finally:
        ole.close()


def test_a_large_stream_roundtrips_through_the_main_fat(tmp_path):
    data = bytes(range(256)) * 400        # 102,400 bytes, well past the cutoff
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Big", data)])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Big"]).read() == data
    finally:
        ole.close()


def test_a_stream_exactly_at_the_cutoff_is_not_mini(tmp_path):
    data = b"x" * 4096
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Edge", data)])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Edge"]).read() == data
    finally:
        ole.close()


def test_an_empty_stream_roundtrips(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Empty", b"")])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Empty"]).read() == b""
    finally:
        ole.close()


def test_nested_storages_roundtrip(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [
        _storage("Relays", [
            _storage("QPC1_LT1_UPC1", [
                _stream("SET_D1.TXT", b"[D1]\r\n"),
                _storage("Misc", [_stream("Cfg.txt", b"[INFO]\r\n")]),
            ]),
        ]),
    ])
    ole = olefile.OleFileIO(str(dst))
    try:
        found = {tuple(e) for e in ole.listdir(streams=True, storages=False)}
        assert ("Relays", "QPC1_LT1_UPC1", "SET_D1.TXT") in found
        assert ("Relays", "QPC1_LT1_UPC1", "Misc", "Cfg.txt") in found
        assert ole.openstream(
            ["Relays", "QPC1_LT1_UPC1", "SET_D1.TXT"]).read() == b"[D1]\r\n"
    finally:
        ole.close()


def test_many_siblings_are_all_reachable(tmp_path):
    """The directory tree is red-black; with 200 siblings, a badly built tree
    loses entries during lookup instead of blowing up."""
    dst = tmp_path / "out.bin"
    names = [f"S{i:03d}" for i in range(200)]
    ore.write_ole(dst, [_stream(n, n.encode()) for n in names])
    ole = olefile.OleFileIO(str(dst))
    try:
        for n in names:
            assert ole.openstream([n]).read() == n.encode()
    finally:
        ole.close()


def test_a_name_too_long_is_refused(tmp_path):
    with pytest.raises(ore.OleRebuildError):
        ore.write_ole(tmp_path / "out.bin", [_stream("x" * 32, b"a")])


def test_rebuild_reproduces_a_file_byte_for_byte_when_nothing_changes(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [
        _storage("Relays", [
            _storage("R1", [_stream("SET_D1.TXT", b"[D1]\r\n" + b"a" * 9000)]),
        ]),
        _stream("Tiny", b"z" * 10),
    ])
    dst = tmp_path / "dst.bin"
    ore.rebuild(src, dst, {})

    a = olefile.OleFileIO(str(src))
    b = olefile.OleFileIO(str(dst))
    try:
        ea = sorted(tuple(e) for e in a.listdir(streams=True, storages=True))
        eb = sorted(tuple(e) for e in b.listdir(streams=True, storages=True))
        assert ea == eb
        for e in a.listdir(streams=True, storages=False):
            assert a.openstream(e).read() == b.openstream(e).read()
    finally:
        a.close()
        b.close()


def test_rebuild_replaces_a_stream_with_a_longer_one(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [
        _storage("Relays", [_storage("R1", [_stream("SET_D1.TXT", b"short")])]),
    ])
    dst = tmp_path / "dst.bin"
    grown = b"a much longer stream than before" * 500
    ore.rebuild(src, dst, {("Relays", "R1", "SET_D1.TXT"): grown})

    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read() == grown
    finally:
        ole.close()


def test_rebuild_refuses_a_replacement_for_a_stream_that_is_not_there(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [_stream("A", b"a")])
    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(src, tmp_path / "dst.bin", {("Nope",): b"x"})


def test_rebuild_deletes_the_output_when_verification_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [_stream("A", b"aaaa")])
    dst = tmp_path / "dst.bin"

    # Sabotage the write: the verifier has to catch it and delete the output.
    real = ore.write_ole

    def sabotaged(path, children):
        real(path, [_stream("A", b"bbbb")])

    monkeypatch.setattr(ore, "write_ole", sabotaged)
    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(src, dst, {})
    assert not dst.exists()


def test_a_stream_big_enough_to_need_a_difat_chain_roundtrips(tmp_path):
    """Past 109 FAT sectors the FAT no longer fits in the header and DIFAT
    sectors appear. Every real RDB is in that range, so a writer only ever
    exercised below it is untested where it matters.

    The fixture has to force at least *two* DIFAT sectors (past 236 FAT
    sectors, so roughly 15.5 MB). With a single one, the last-slot next
    pointer is always ENDOFCHAIN and a wrong chain link cannot show up.
    """
    data = bytes(range(256)) * 62500          # 16,000,000 bytes
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Huge", data)])

    header = dst.open("rb").read(512)
    num_fat = struct.unpack_from("<I", header, 44)[0]
    num_difat = struct.unpack_from("<I", header, 72)[0]
    assert num_fat > 109, "fixture too small to reach the DIFAT"
    assert num_difat >= 2, "fixture too small to chain two DIFAT sectors"

    ole = olefile.OleFileIO(str(dst), raise_defects=olefile.DEFECT_INCORRECT)
    try:
        assert ole.openstream(["Huge"]).read() == data
    finally:
        ole.close()


def _direntries(path):
    """The raw 128-byte directory entries of a file we wrote.

    olefile's public API drops the colour byte, which is exactly what the
    red-black invariants are about, so the directory stream is parsed here.
    """
    ole = olefile.OleFileIO(str(path))
    try:
        raw = ole._open(ole.first_dir_sector, force_FAT=True).read()
    finally:
        ole.close()
    out = []
    for sid in range(len(raw) // 128):
        e = raw[sid * 128:(sid + 1) * 128]
        namelen = struct.unpack_from("<H", e, 64)[0]
        left, right, child = struct.unpack_from("<III", e, 68)
        out.append({
            "sid": sid,
            "name": e[:max(namelen - 2, 0)].decode("utf-16-le"),
            "type": e[66],
            "colour": e[67],
            "left": left,
            "right": right,
            "child": child,
        })
    return out


def _assert_red_black(entries, root_sid):
    """Assert the MS-CFB red-black invariants over one sibling tree."""
    if root_sid == ore.NOSTREAM:
        return
    assert entries[root_sid]["colour"] == ore._COLOR_BLACK, "red root"

    order = []
    black_heights = set()

    def walk(sid, blacks):
        if sid == ore.NOSTREAM:
            black_heights.add(blacks)
            return
        e = entries[sid]
        if e["colour"] == ore._COLOR_RED:
            for kid in (e["left"], e["right"]):
                assert (kid == ore.NOSTREAM
                        or entries[kid]["colour"] == ore._COLOR_BLACK), \
                    f"red-red edge under {e['name']!r}"
        deeper = blacks + (1 if e["colour"] == ore._COLOR_BLACK else 0)
        walk(e["left"], deeper)
        order.append(e["name"])
        walk(e["right"], deeper)

    walk(root_sid, 0)
    assert len(black_heights) == 1, f"black heights differ: {black_heights}"
    assert order == sorted(order, key=lambda n: (len(n), n.upper()))


def test_the_directory_is_a_valid_red_black_tree(tmp_path):
    """Two siblings are already enough to break an all-black tree: one NIL path
    crosses one black node and the other crosses two. olefile ignores the
    colour byte entirely, so nothing else in this file would notice."""
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [
        _storage("Pair", [_stream("A", b"a"), _stream("B", b"b")]),
        _storage("Solo", [_stream("Only", b"o")]),
        _storage("Many", [_stream(f"S{i:03d}", b"x") for i in range(37)]),
        _storage("Mixed", [_stream(f"N{i}" * (i % 5 + 1), b"y")
                           for i in range(12)]),
    ])
    entries = _direntries(dst)
    _assert_red_black(entries, entries[0]["child"])      # the root's children
    for e in entries:
        if e["type"] == ore._TYPE_STORAGE:
            _assert_red_black(entries, e["child"])


def test_unused_directory_slots_point_nowhere(tmp_path):
    """MS-CFB 2.6.1: an unallocated entry is all zeros except its three
    pointers, which are NOSTREAM. Zero padding would leave them at 0, the
    Root Entry's own SID."""
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("A", b"a")])             # 2 of 4 slots used
    unused = [e for e in _direntries(dst) if e["type"] == 0]
    assert unused, "fixture left no unallocated slot"
    for e in unused:
        assert e["name"] == ""
        assert (e["left"], e["right"], e["child"]) == (
            ore.NOSTREAM, ore.NOSTREAM, ore.NOSTREAM)


def test_a_failed_write_leaves_the_destination_untouched(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [_stream("A", b"aaaa")])
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"an older RDB nobody wants truncated")

    def exploding(path, children):
        Path(path).write_bytes(b"\x00" * 2048)           # a partial container
        raise ore.OleRebuildError("estourou no meio da escrita")

    monkeypatch.setattr(ore, "write_ole", exploding)
    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(src, dst, {})

    assert dst.read_bytes() == b"an older RDB nobody wants truncated"
    leftovers = {p.name for p in tmp_path.iterdir()} - {"src.bin", "dst.bin"}
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_rebuilding_onto_the_source_is_refused(tmp_path):
    """The source is read lazily while the output is written, so an in-place
    rebuild can only shred it."""
    path = tmp_path / "same.bin"
    ore.write_ole(path, [_stream("A", b"aaaa"),
                         _stream("B", b"b" * 9000)])
    before = path.read_bytes()

    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(path, path, {})
    assert path.read_bytes() == before

    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(path, tmp_path / "." / "same.bin", {})
    assert path.read_bytes() == before
