"""Write a Compound File (MS-CFB v3), so an RDB stream can change size.

``olefile.write_stream`` replaces a stream only with data of exactly the same
length. That is enough to rearrange a DNP map -- moving values conserves bytes
-- and not enough to fill a free slot, which grows the file and has no slack to
grow into. This module rebuilds the whole container instead.

Layout written, in this fixed order, which is what makes every sector number
derivable without a second pass over the data:

    [header 512B][FAT][DIFAT][directory][miniFAT][mini stream][streams]

``rebuild()`` verifies its own output before returning: it reopens the result
with ``olefile`` and compares every stream against the source, except the ones
that were meant to change. A bug in this writer has to surface as a failed
export, never as a silently corrupt relay settings file.
"""

from __future__ import annotations

import os
import struct
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import olefile

SECTOR_SIZE = 512
MINI_SECTOR_SIZE = 64
MINI_CUTOFF = 4096
DIR_ENTRY_SIZE = 128
DIR_PER_SECTOR = SECTOR_SIZE // DIR_ENTRY_SIZE           # 4
FAT_PER_SECTOR = SECTOR_SIZE // 4                        # 128
DIFAT_PER_SECTOR = FAT_PER_SECTOR - 1                    # 127
DIFAT_IN_HEADER = 109

DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
NOSTREAM = 0xFFFFFFFF

_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Entry types (MS-CFB 2.6.1)
_TYPE_STORAGE = 1
_TYPE_STREAM = 2
_TYPE_ROOT = 5
# Node colours (MS-CFB 2.6.1): 0x00 red, 0x01 black.
_COLOR_RED = 0
_COLOR_BLACK = 1


class OleRebuildError(Exception):
    """Refusing to write, or refusing to hand over what was written."""


@dataclass
class Entry:
    """One node of the tree to write. ``read`` is called once, lazily."""

    name: str
    is_storage: bool
    size: int
    read: Callable[[], bytes] | None
    children: list[Entry] = field(default_factory=list)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _sibling_order(entries: list[Entry]) -> list[Entry]:
    """Canonical CFB sibling order: shorter names first, then case-insensitive."""
    return sorted(entries, key=lambda e: (len(e.name), e.name.upper()))


def _flatten(root_children: list[Entry]) -> list[Entry]:
    """Depth-first list of every node, root excluded."""
    out: list[Entry] = []

    def walk(nodes: list[Entry]) -> None:
        for n in _sibling_order(nodes):
            out.append(n)
            if n.is_storage:
                walk(n.children)

    walk(root_children)
    return out


def _build_tree(ids: list[int]) -> tuple[int, dict[int, tuple[int, int, int]]]:
    """Balanced red-black tree over an already-sorted sibling list.

    Returns ``(root_id, {id: (left, right, colour)})``.

    The midpoint construction gives a tree whose leaves all sit at depth
    ``h`` or ``h - 1``, so painting exactly the deepest level red and
    everything else black is a valid colouring: the red nodes are leaves (no
    red-red edge is possible) and every root-to-NIL path crosses the same
    ``h`` black nodes. An all-black tree is *not* valid -- with two siblings
    the root would have one NIL path of one black node and one path of two --
    and readers that check the invariant would be right to reject it.
    """
    links: dict[int, tuple[int, int, int]] = {}
    depth: dict[int, int] = {}

    def build(lo: int, hi: int, d: int) -> int:
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        node = ids[mid]
        depth[node] = d
        left = build(lo, mid - 1, d + 1)
        right = build(mid + 1, hi, d + 1)
        links[node] = (left, right, _COLOR_BLACK)
        return node

    root = build(0, len(ids) - 1, 0)
    if depth:
        deepest = max(depth.values())
        if deepest:                      # a lone root node stays black
            for node, d in depth.items():
                if d == deepest:
                    left, right, _ = links[node]
                    links[node] = (left, right, _COLOR_RED)
    return root, links


def write_ole(dst: Path, root_children: list[Entry]) -> None:
    """Write a complete Compound File containing ``root_children``."""
    dst = Path(dst)
    nodes = _flatten(root_children)
    for n in nodes:
        if len(n.name) > 31:
            raise OleRebuildError(
                f"nome longo demais para o formato OLE (máx 31): {n.name!r}")

    # Id 0 is always the Root Entry; the rest follow the depth-first order.
    ids = {id(n): i + 1 for i, n in enumerate(nodes)}

    mini_nodes = [n for n in nodes
                  if not n.is_storage and 0 < n.size < MINI_CUTOFF]
    reg_nodes = [n for n in nodes
                 if not n.is_storage and n.size >= MINI_CUTOFF]

    mini_sectors = sum(_ceil_div(n.size, MINI_SECTOR_SIZE) for n in mini_nodes)
    ministream_bytes = mini_sectors * MINI_SECTOR_SIZE
    ministream_sectors = _ceil_div(ministream_bytes, SECTOR_SIZE)
    dir_sectors = _ceil_div(len(nodes) + 1, DIR_PER_SECTOR)
    minifat_sectors = _ceil_div(mini_sectors * 4, SECTOR_SIZE)
    data_sectors = sum(_ceil_div(n.size, SECTOR_SIZE) for n in reg_nodes)

    fat_sectors = 0
    difat_sectors = 0
    for _ in range(16):                      # converges in 2-3 rounds
        total = (fat_sectors + difat_sectors + dir_sectors
                 + minifat_sectors + ministream_sectors + data_sectors)
        new_fat = _ceil_div(total, FAT_PER_SECTOR)
        new_difat = (0 if new_fat <= DIFAT_IN_HEADER
                     else _ceil_div(new_fat - DIFAT_IN_HEADER, DIFAT_PER_SECTOR))
        if (new_fat, new_difat) == (fat_sectors, difat_sectors):
            break
        fat_sectors, difat_sectors = new_fat, new_difat
    else:
        raise OleRebuildError("não consegui dimensionar a FAT")

    # Sector numbering, in layout order.
    cur = 0
    fat_start = cur
    cur += fat_sectors
    difat_start = cur
    cur += difat_sectors
    dir_start = cur
    cur += dir_sectors
    minifat_start = cur
    cur += minifat_sectors
    ministream_start = cur
    cur += ministream_sectors
    data_start = cur
    cur += data_sectors

    fat = [FREESECT] * (fat_sectors * FAT_PER_SECTOR)

    def chain(start: int, count: int, mark: int | None = None) -> None:
        """Link ``count`` consecutive sectors from ``start`` in the FAT."""
        for i in range(count):
            if mark is not None:
                fat[start + i] = mark
            else:
                fat[start + i] = (start + i + 1 if i < count - 1
                                  else ENDOFCHAIN)

    chain(fat_start, fat_sectors, FATSECT)
    chain(difat_start, difat_sectors, DIFSECT)
    chain(dir_start, dir_sectors)
    chain(minifat_start, minifat_sectors)
    chain(ministream_start, ministream_sectors)

    # Every regular stream gets a chain of its own.
    stream_start: dict[int, int] = {}
    cursor = data_start
    for n in reg_nodes:
        count = _ceil_div(n.size, SECTOR_SIZE)
        stream_start[id(n)] = cursor
        chain(cursor, count)
        cursor += count

    # Mini FAT: one chain per small stream, counted in mini sectors.
    minifat = [FREESECT] * (minifat_sectors * FAT_PER_SECTOR)
    mini_start: dict[int, int] = {}
    mcursor = 0
    for n in mini_nodes:
        count = _ceil_div(n.size, MINI_SECTOR_SIZE)
        mini_start[id(n)] = mcursor
        for i in range(count):
            minifat[mcursor + i] = (mcursor + i + 1 if i < count - 1
                                    else ENDOFCHAIN)
        mcursor += count

    dir_bytes = _directory_bytes(
        nodes, ids, root_children, stream_start, mini_start,
        ministream_start if mini_sectors else ENDOFCHAIN, ministream_bytes,
    )

    with dst.open("wb") as fh:
        fh.write(_header_bytes(
            fat_sectors=fat_sectors, fat_start=fat_start,
            difat_sectors=difat_sectors, difat_start=difat_start,
            dir_start=dir_start,
            minifat_sectors=minifat_sectors,
            minifat_start=minifat_start if minifat_sectors else ENDOFCHAIN,
        ))
        _write_uint32_sectors(fh, fat)
        _write_difat_sectors(fh, fat_sectors, fat_start,
                             difat_sectors, difat_start)
        _write_directory(fh, dir_bytes, dir_sectors)
        _write_uint32_sectors(fh, minifat)
        _write_ministream(fh, mini_nodes, ministream_sectors)
        for n in reg_nodes:
            data = n.read() if n.read else b""
            if len(data) != n.size:
                raise OleRebuildError(
                    f"{n.name}: tamanho declarado {n.size}, lido {len(data)}")
            _write_padded(fh, data, _ceil_div(n.size, SECTOR_SIZE))


# MS-CFB 2.6.1: an unused directory entry is all zeros, except the three
# sibling/child pointers, which are NOSTREAM. Zero padding would leave them at
# 0x00000000 -- the Root Entry's SID, not "no sibling". These are the exact
# 128 bytes QuickSet writes in its own trailing slots.
_UNALLOCATED_DIR_ENTRY = (
    b"\x00" * 68
    + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM)
    + b"\x00" * 48
)
assert len(_UNALLOCATED_DIR_ENTRY) == DIR_ENTRY_SIZE


def _write_directory(fh, dir_bytes: bytes, sectors: int) -> None:
    """Write the directory, filling the last sector with unallocated entries."""
    slack = sectors * SECTOR_SIZE - len(dir_bytes)
    if slack < 0 or slack % DIR_ENTRY_SIZE:
        raise OleRebuildError("diretório não cabe nos setores reservados")
    fh.write(dir_bytes)
    fh.write(_UNALLOCATED_DIR_ENTRY * (slack // DIR_ENTRY_SIZE))


def _write_padded(fh, data: bytes, sectors: int) -> None:
    fh.write(data)
    pad = sectors * SECTOR_SIZE - len(data)
    if pad < 0:
        raise OleRebuildError("dados maiores que os setores reservados")
    if pad:
        fh.write(b"\x00" * pad)


def _write_uint32_sectors(fh, values: list[int]) -> None:
    if values:
        fh.write(struct.pack(f"<{len(values)}I", *values))


def _write_difat_sectors(fh, fat_sectors: int, fat_start: int,
                         difat_sectors: int, difat_start: int) -> None:
    """DIFAT sectors hold FAT sector numbers 110 and up, 127 per sector."""
    if not difat_sectors:
        return
    remaining = [fat_start + i for i in range(DIFAT_IN_HEADER, fat_sectors)]
    for s in range(difat_sectors):
        chunk = remaining[s * DIFAT_PER_SECTOR:(s + 1) * DIFAT_PER_SECTOR]
        chunk = chunk + [FREESECT] * (DIFAT_PER_SECTOR - len(chunk))
        # The last slot points at the next DIFAT *sector number*, not at its
        # index in the DIFAT chain.
        nxt = ENDOFCHAIN if s == difat_sectors - 1 else difat_start + s + 1
        fh.write(struct.pack(f"<{DIFAT_PER_SECTOR}I", *chunk))
        fh.write(struct.pack("<I", nxt))


def _write_ministream(fh, mini_nodes: list[Entry], sectors: int) -> None:
    """The mini stream: every small stream, each padded to 64 bytes."""
    buf = bytearray()
    for n in mini_nodes:
        data = n.read() if n.read else b""
        if len(data) != n.size:
            raise OleRebuildError(
                f"{n.name}: tamanho declarado {n.size}, lido {len(data)}")
        buf += data
        pad = (_ceil_div(len(data), MINI_SECTOR_SIZE) * MINI_SECTOR_SIZE
               - len(data))
        buf += b"\x00" * pad
    _write_padded(fh, bytes(buf), sectors)


def _dir_entry(name: str, etype: int, left: int, right: int, colour: int,
               child: int, start: int, size: int) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > 64:
        raise OleRebuildError(f"nome longo demais para o formato OLE: {name!r}")
    return b"".join((
        encoded.ljust(64, b"\x00"),
        struct.pack("<H", len(encoded)),
        struct.pack("<BB", etype, colour),
        struct.pack("<III", left, right, child),
        b"\x00" * 16,                    # CLSID
        struct.pack("<I", 0),            # state bits
        b"\x00" * 16,                    # creation + modified time
        struct.pack("<I", start),
        struct.pack("<Q", size),
    ))


def _directory_bytes(nodes, ids, root_children, stream_start, mini_start,
                     ministream_start: int, ministream_size: int) -> bytes:
    def child_of(children: list[Entry]):
        ordered = [ids[id(c)] for c in _sibling_order(children)]
        return _build_tree(ordered)

    root_child, links = child_of(root_children)
    all_links: dict[int, tuple[int, int, int]] = dict(links)
    child_id: dict[int, int] = {}
    for n in nodes:
        if n.is_storage:
            c, sub = child_of(n.children)
            child_id[ids[id(n)]] = c
            all_links.update(sub)

    out = [_dir_entry("Root Entry", _TYPE_ROOT, NOSTREAM, NOSTREAM,
                      _COLOR_BLACK, root_child,
                      ministream_start, ministream_size)]
    for n in nodes:
        i = ids[id(n)]
        left, right, colour = all_links.get(i, (NOSTREAM, NOSTREAM,
                                                _COLOR_BLACK))
        if n.is_storage:
            out.append(_dir_entry(n.name, _TYPE_STORAGE, left, right, colour,
                                  child_id.get(i, NOSTREAM), 0, 0))
        elif n.size == 0:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right, colour,
                                  NOSTREAM, ENDOFCHAIN, 0))
        elif n.size < MINI_CUTOFF:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right, colour,
                                  NOSTREAM, mini_start[id(n)], n.size))
        else:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right, colour,
                                  NOSTREAM, stream_start[id(n)], n.size))
    return b"".join(out)


def _header_bytes(*, fat_sectors: int, fat_start: int, difat_sectors: int,
                  difat_start: int, dir_start: int, minifat_sectors: int,
                  minifat_start: int) -> bytes:
    difat_head = [fat_start + i
                  for i in range(min(fat_sectors, DIFAT_IN_HEADER))]
    difat_head += [FREESECT] * (DIFAT_IN_HEADER - len(difat_head))
    return b"".join((
        _SIGNATURE,
        b"\x00" * 16,                       # CLSID
        struct.pack("<HH", 0x003E, 3),      # minor, major version
        struct.pack("<H", 0xFFFE),          # byte order, little endian
        struct.pack("<HH", 9, 6),           # sector shift 512, mini shift 64
        b"\x00" * 6,                        # reserved
        struct.pack("<I", 0),               # num directory sectors (v3: 0)
        struct.pack("<I", fat_sectors),
        struct.pack("<I", dir_start),
        struct.pack("<I", 0),               # transaction signature
        struct.pack("<I", MINI_CUTOFF),
        struct.pack("<I", minifat_start),
        struct.pack("<I", minifat_sectors),
        struct.pack("<I", difat_start if difat_sectors else ENDOFCHAIN),
        struct.pack("<I", difat_sectors),
        struct.pack(f"<{DIFAT_IN_HEADER}I", *difat_head),
    ))


def _given(data: bytes) -> Callable[[], bytes]:
    """A reader that hands back bytes already in memory."""
    return lambda: data


def _from_stream(ole: olefile.OleFileIO, path) -> Callable[[], bytes]:
    """A reader that pulls one stream out of the open source, when asked."""
    return lambda: ole.openstream(path).read()


def _tree_from_ole(ole: olefile.OleFileIO,
                   replacements: dict[tuple[str, ...], bytes]) -> list[Entry]:
    """Mirror an open OLE as an Entry tree, applying replacements by path."""
    roots: list[Entry] = []
    index: dict[tuple[str, ...], Entry] = {}

    def ensure_storage(path: tuple[str, ...]) -> Entry:
        node = index.get(path)
        if node is not None:
            return node
        node = Entry(name=path[-1], is_storage=True, size=0, read=None)
        index[path] = node
        if len(path) == 1:
            roots.append(node)
        else:
            ensure_storage(path[:-1]).children.append(node)
        return node

    for raw in ole.listdir(streams=False, storages=True):
        ensure_storage(tuple(raw))

    for raw in ole.listdir(streams=True, storages=False):
        path = tuple(raw)
        node: Entry
        if path in replacements:
            data = replacements[path]
            node = Entry(name=path[-1], is_storage=False, size=len(data),
                         read=_given(data))
        else:
            node = Entry(name=path[-1], is_storage=False,
                         size=ole.get_size(raw),
                         read=_from_stream(ole, raw))
        index[path] = node
        if len(path) == 1:
            roots.append(node)
        else:
            ensure_storage(path[:-1]).children.append(node)
    return roots


def _is_same_file(a: Path, b: Path) -> bool:
    """True when two paths name the same file, existing or not."""
    if a.resolve() == b.resolve():
        return True
    try:
        return a.samefile(b)
    except OSError:               # one of them does not exist
        return False


def rebuild(src: Path, dst: Path,
            replacements: dict[tuple[str, ...], bytes]) -> None:
    """Write ``dst`` as ``src`` with ``replacements`` applied, any size.

    The container is built in a temporary file beside ``dst``, verified there,
    and only then moved onto ``dst`` -- so a failure anywhere leaves ``dst``
    exactly as it was, whether that is absent or an older RDB. On any mismatch
    the temporary is removed and ``OleRebuildError`` is raised: a half-right
    RDB is worse than none.
    """
    src, dst = Path(src), Path(dst)
    if _is_same_file(src, dst):
        raise OleRebuildError(
            "não dá para reconstruir sobre o próprio arquivo de origem: "
            f"{src}")

    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                    prefix=f".{dst.name}.", suffix=".ole-tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        ole = olefile.OleFileIO(str(src))
        try:
            present = {tuple(e)
                       for e in ole.listdir(streams=True, storages=False)}
            missing = [p for p in replacements if p not in present]
            if missing:
                raise OleRebuildError(
                    "stream inexistente no RDB: "
                    + ", ".join("/".join(p) for p in missing))
            write_ole(tmp, _tree_from_ole(ole, replacements))
        finally:
            ole.close()
        _verify(src, tmp, replacements)
        # mkstemp opens at 0600; the finished RDB is an ordinary file.
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _verify(src: Path, dst: Path,
            replacements: dict[tuple[str, ...], bytes]) -> None:
    a = olefile.OleFileIO(str(src))
    # The output is ours, so it has no excuse for a structural defect: read it
    # back under the strictest setting olefile still parses with.
    b = olefile.OleFileIO(str(dst), raise_defects=olefile.DEFECT_INCORRECT)
    try:
        ea = sorted(tuple(e) for e in a.listdir(streams=True, storages=True))
        eb = sorted(tuple(e) for e in b.listdir(streams=True, storages=True))
        if ea != eb:
            raise OleRebuildError(
                "a árvore do RDB reconstruído não bate com a do original")
        for raw in a.listdir(streams=True, storages=False):
            path = tuple(raw)
            got = b.openstream(raw).read()
            want = replacements.get(path)
            if want is None:
                want = a.openstream(raw).read()
            if got != want:
                raise OleRebuildError(
                    f"stream divergente após reconstrução: {'/'.join(path)}")
    finally:
        a.close()
        b.close()
