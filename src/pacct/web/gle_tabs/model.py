"""The whole of the GLE tab surgery: bytes in, bytes out, no HTTP.

Locating a page by regex would work until a `</page>` turned up inside an XML
comment or a CDATA section, and the blast radius there is a whole page rather
than one comment. `expat` gives the real boundaries: `CurrentByteIndex` at the
start element is the byte of `<page`, and at the end element the byte of
`</page`.

The encoding is overridden at the parser rather than swapped in the bytes.
QuickSet writes `encoding="utf-8"` over latin-1 content, and `sellib.gle`'s
`parse_gle` fixes that by rewriting the declaration -- which lengthens the file
by 5 bytes and would shift every offset after the prolog. Overriding at
`ParserCreate` leaves the offsets as the original file's.
"""

from __future__ import annotations

import xml.parsers.expat as expat
from collections import Counter
from dataclasses import dataclass

from sellib.scl._xmlsafe import DtdNotAllowed, reject_dtd_in_bytes

# Measured across the 3.111 page names of the 215 `.gle` in the local corpus:
# none is longer, 67 sit exactly here, and several are visibly truncated
# ("52- CMD DE FECHAMENT", "L-R/Lib.Int/Mod.Test"). SEL does not document it.
MAX_NAME = 20

_WS = b" \t\r\n"


class GleTabsError(ValueError):
    """The edit is not one this module will make. Nothing was written."""


@dataclass(frozen=True)
class PageSpan:
    """One tab, and exactly where it lives in the file's bytes."""

    index: int          # position in the ORIGINAL file, 0-based
    name: str           # unescaped, as expat hands it back
    description: str
    elements: int       # <element> count, from the same pass
    start: int          # byte offset of "<page"
    end: int            # byte offset just past "</page>"
    name_start: int     # byte offsets of the name attribute's VALUE,
    name_end: int       # inside the start tag


def _start_tag_end(raw: bytes, start: int) -> int:
    """Byte just past the `>` closing the start tag that begins at `start`.

    Quote-aware: `>` is legal UNESCAPED inside an attribute value, so scanning
    for the first one would stop inside `description="A > B"`.
    """
    i, quote = start + 1, 0
    while i < len(raw):
        c = raw[i]
        if quote:
            if c == quote:
                quote = 0
        elif c in (0x22, 0x27):     # " '
            quote = c
        elif c == 0x3E:             # >
            return i + 1
        i += 1
    raise GleTabsError("<page> sem fechamento")


def _name_attr_span(raw: bytes, start: int, tag_end: int) -> tuple[int, int]:
    """Byte range of the VALUE of `name` inside a `<page ...>` start tag.

    Walks the attributes rather than searching for `name=`, which could match
    inside another attribute's value.
    """
    i = start + len(b"<page")
    while i < tag_end:
        while i < tag_end and raw[i] in _WS:
            i += 1
        j = i
        while j < tag_end and raw[j] not in b"= \t\r\n/>":
            j += 1
        attr = raw[i:j]
        while j < tag_end and raw[j] in _WS:
            j += 1
        if j >= tag_end or raw[j] != 0x3D:      # not `attr=`
            i = j + 1
            continue
        j += 1
        while j < tag_end and raw[j] in _WS:
            j += 1
        if j >= tag_end or raw[j] not in (0x22, 0x27):
            raise GleTabsError("atributo de <page> sem aspas")
        close = raw.index(bytes([raw[j]]), j + 1)
        if attr == b"name":
            return j + 1, close
        i = close + 1
    raise GleTabsError("<page> sem atributo name")


def read_pages(raw: bytes) -> list[PageSpan]:
    """Every tab in the GLE, in file order, with its exact byte span."""
    # A GLE arrives inside an RDB somebody uploaded, so it is no more trusted
    # than an SCD -- the same guard `sellib.gle.parse_gle` applies.
    try:
        reject_dtd_in_bytes(raw)
    except DtdNotAllowed as exc:
        # _xmlsafe is a private module (leading underscore). This module's
        # callers should only have to know GleTabsError.
        raise GleTabsError(f"GLE contém DOCTYPE proibido: {exc}") from exc

    spans: list[PageSpan] = []
    cur: dict = {}
    depth = [0]

    def start(name: str, attrs: dict) -> None:
        if name == "page" and not cur:
            begin = parser.CurrentByteIndex
            tag_end = _start_tag_end(raw, begin)
            nstart, nend = _name_attr_span(raw, begin, tag_end)
            cur.update(start=begin, name=attrs.get("name", ""),
                       description=attrs.get("description", ""),
                       elements=0, name_start=nstart, name_end=nend)
        if cur:
            depth[0] += 1
            if name == "element":
                cur["elements"] += 1

    def end(name: str) -> None:
        if not cur:
            return
        depth[0] -= 1
        if depth[0] == 0 and name == "page":
            close = raw.index(b">", parser.CurrentByteIndex) + 1
            spans.append(PageSpan(
                index=len(spans), name=cur["name"],
                description=cur["description"], elements=cur["elements"],
                start=cur["start"], end=close,
                name_start=cur["name_start"], name_end=cur["name_end"],
            ))
            cur.clear()

    parser = expat.ParserCreate(encoding="iso-8859-1")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        raise GleTabsError(f"GLE ilegível: {exc}") from exc
    return spans


def escape_attr(s: str) -> str:
    """Escape a string for an XML ATTRIBUTE value.

    Not `rdb_write.xml_text_escape`, which is documented for TEXT content and
    deliberately leaves `"` alone -- correct between tags, wrong inside
    `name="..."`, where an unescaped quote closes the attribute and produces a
    malformed GLE that goes into the output RDB and then into the project
    library, where nothing distinguishes it from a good file. That is the same
    failure `xml_text_escape`'s own docstring was written about.
    """
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _validate_name(name: str) -> None:
    if not name.strip():
        raise GleTabsError("o nome da aba não pode ficar vazio")
    if len(name) > MAX_NAME:
        raise GleTabsError(
            f"“{name}” tem {len(name)} caracteres; o limite é {MAX_NAME}. "
            "O limite foi MEDIDO nos 3.111 nomes de página do acervo (nenhum "
            "passa disso, e vários estão truncados exatamente nele) — a SEL "
            "não o documenta."
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise GleTabsError(f"“{name}” tem caractere de controle")
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        raise GleTabsError(
            f"“{name}” tem caractere que o GLE não guarda: o arquivo é "
            "latin-1, mesmo declarando utf-8"
        ) from None


def validate_edit(spans: list[PageSpan], *, order: list[int],
                  names: dict[int, str]) -> None:
    """Raise `GleTabsError` unless this edit is one `apply_page_edits` will make."""
    n = len(spans)
    if sorted(order) != list(range(n)):
        raise GleTabsError(
            f"ordem inválida: esperava uma permutação das {n} abas. "
            "Uma aba não pode ser removida por omissão."
        )
    for idx, new in names.items():
        if not 0 <= idx < n:
            raise GleTabsError(f"a página {idx} não existe neste GLE")
        _validate_name(new)

    # A rename must not CREATE a duplicate. Duplicates that were already in
    # the file are tolerated: real files ship with them, and refusing one
    # would reject a file for a state this tool did not cause.
    # `glv/gle_pages.py:safe_page_id` documents what collisions cost.
    before = Counter(s.name for s in spans)
    after = Counter(names.get(s.index, s.name) for s in spans)
    for name, count in after.items():
        if count > 1 and count > before.get(name, 0):
            raise GleTabsError(f"já existe uma aba chamada “{name}”")


def apply_page_edits(raw: bytes, *, order: list[int],
                     names: dict[int, str]) -> tuple[bytes, dict]:
    """`raw` with its pages reordered and renamed. Returns (bytes, stats).

    `order` is in terms of ORIGINAL indices: `order[i]` is the page that ends
    up at position `i`. `names` maps an original index to its new name.

    The separators between pages -- indentation, and any comment sitting
    between two of them -- stay at their positions rather than travelling with
    a page, so the file's shape survives a reorder.

    Re-parses its own output before returning. `rdb_write` verifies the OLE
    container and never the XML inside a stream, so this is the only place a
    mis-spliced page can still be caught.
    """
    spans = read_pages(raw)
    if not spans:
        raise GleTabsError("o GLE não tem nenhuma página")
    validate_edit(spans, order=order, names=names)

    # Rename first, each page on its own bytes, so the permutation below only
    # has to move finished blocks around.
    bodies: list[bytes] = []
    renamed = 0
    for sp in spans:
        new = names.get(sp.index)
        if new is None or new == sp.name:
            bodies.append(raw[sp.start:sp.end])
            continue
        bodies.append(raw[sp.start:sp.name_start]
                      + escape_attr(new).encode("latin-1")
                      + raw[sp.name_end:sp.end])
        renamed += 1

    out: list[bytes] = [raw[:spans[0].start]]
    for pos, src in enumerate(order):
        out.append(bodies[src])
        if pos + 1 < len(spans):
            # The separator that originally FOLLOWED this position.
            out.append(raw[spans[pos].end:spans[pos + 1].start])
    out.append(raw[spans[-1].end:])
    result = b"".join(out)

    want = [names.get(i, spans[i].name) for i in order]
    got = [s.name for s in read_pages(result)]
    if got != want:
        raise GleTabsError(
            f"a edição não conferiu depois de aplicada: esperava {want}, "
            f"o arquivo ficou com {got}. Nada foi gravado."
        )
    moved = sum(1 for pos, src in enumerate(order) if pos != src)
    return result, {"moved": moved, "renamed": renamed}
