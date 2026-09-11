"""GLE.xml in, port comments out -- and back again, in bytes.

Two halves that are inverses of each other. The reader turns a `.gle` into a
list of `PortInstance`, one per port of each exportable element; the writer
takes the comments the user changed and replaces exactly those, in the
original bytes, leaving every other byte where it was. `diff_updates_against_gle`
sits between them and drops whatever already matches.

Pure `Path`/`bytes` in, dataclasses and `bytes` out. Nothing here knows there
is a web layer, a session or a spreadsheet: the xlsx that carries these
comments to the user and back is `export.py`'s business, and the routes are
`handler.py`'s. The one thing it does reach for outside itself is
`rdb_write.xml_text_escape` -- see `_build_comment_node`.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from sellib.gle import parse_gle

from pacct.web.rdb_write import xml_text_escape

_logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Extractor: GLE.xml -> [PortInstance]
# -----------------------------------------------------------------------------

# GLE XML types with a `physical_instance_name` that we want to export:
#   - SYMBOL (IO variables, Relay Word bits): 1 in + 1 out, no fixed labels.
#   - 4xx/7xx stateful blocks: PLT, ALT, PCNDTIMER, PCN, AST, PSV, LATCH,
#     TIMER, COUNTER. Port labels come from the relay_model JSON.
# Pure gates (AND/OR/NOT/EQ/...) are left out -- they have no editable
# instance name and their port comments carry no commissioning meaning.
_EXPORTABLE_XML_TYPES = frozenset({
    "SYMBOL",
    "PLT", "ALT", "PCNDTIMER", "PCN", "AST", "PSV",
    "LATCH", "TIMER", "COUNTER",
})


@dataclass(frozen=True)
class PortInstance:
    """One concrete port (in a GLE element) with its fixed label and the
    user's free-form comment."""
    page: str
    element_id: str
    xml_type: str        # type of the <element>: SYMBOL, PLT, LATCH, ...
    name: str            # physical_instance_name (ex.: VB203, _PLT06, TMB1A)
    side: str            # "input" or "output"
    port_index: int      # the <port>'s `index` attribute (0, 1, 2, ...)
    label: str           # fixed pin label (S/R/Q/in/PU/...) or "" if absent
    comment: str         # free text from the user; "" if <comment/> is empty


def extract_port_instances_from_gle(
    gle_path: Path, relay_model=None,
) -> list[PortInstance]:
    """Read a GLE.xml and return a list of PortInstance, one per port of
    each exportable element.

    GLE convention: inside each `<logic_element>` there are (in order) two
    `<ports>` blocks -- the 1st is the input side (left), the 2nd is the
    output side (right). Each `<port>` has a numeric `index` (0, 1, 2).

    If `relay_model` is given, the pin labels (S/R/Q/...) are resolved via
    `relay_model.port_label`. Without the model, label stays "".
    """
    out: list[PortInstance] = []
    try:
        root = parse_gle(gle_path)
    except (OSError, ET.ParseError, UnicodeDecodeError) as e:
        _logger.warning("erro lendo GLE %s: %s", gle_path, e)
        return out

    for page in root.iter("page"):
        page_name = (page.attrib.get("name") or "").strip()
        for el in page.iter("element"):
            xml_type = (el.attrib.get("type") or "").strip()
            if xml_type not in _EXPORTABLE_XML_TYPES:
                continue
            element_id = (el.attrib.get("id") or "").strip()
            le = el.find("logic_element")
            if le is None:
                continue
            name = (le.attrib.get("physical_instance_name") or "").strip()
            if not name:
                continue
            port_groups = le.findall("ports")
            for grp_idx, pg in enumerate(port_groups[:2]):
                side = "input" if grp_idx == 0 else "output"
                for port_el in pg.findall("port"):
                    try:
                        idx = int(port_el.attrib.get("index", "0"))
                    except ValueError:
                        idx = 0
                    cmt_el = port_el.find("comment")
                    cmt = (cmt_el.text or "").strip() if cmt_el is not None else ""
                    label = ""
                    if relay_model is not None:
                        lbl = relay_model.port_label(xml_type, side, idx)
                        if lbl is not None:
                            label = lbl
                    out.append(PortInstance(
                        page=page_name, element_id=element_id,
                        xml_type=xml_type, name=name, side=side,
                        port_index=idx, label=label, comment=cmt,
                    ))
    return out


# -----------------------------------------------------------------------------
# Writer: edits the GLE comments in bytes (keeps the OLE stream size)
# -----------------------------------------------------------------------------

# Matches an <element id="N" type="TIPO" ...> ... </element>. We use named
# groups so as not to depend on indices (adding `type` shifts the numeric
# ones, which has already been a source of bugs).
# Non-greedy on the body: `<element>` does not nest, and `</logic_element>`
# (which is INSIDE) does not match a literal `</element>` -- so the first
# `</element>` found is always the outer block's.
_GLE_ELEMENT_RE = re.compile(
    rb'(?P<open><element\s+id="(?P<id>\d+)"\s+type="(?P<type>[^"]+)"[^>]*>)'
    rb'(?P<body>.*?)'
    rb'(?P<close></element>)',
    re.DOTALL,
)

# Matches a <ports>...</ports> block OR <ports/> (self-closing) inside the
# logic_element. Also non-greedy: `<ports>` does not nest.
_PORTS_BLOCK_RE = re.compile(
    rb'<ports\s*/>|<ports\b[^>]*>.*?</ports>',
    re.DOTALL,
)

# Inside a <ports>...</ports>, matches the comment of <port index="N"> (N
# arrives by substituting {idx}). Two comment shapes in the GLE:
#   <comment />                  (self-closing, empty)
#   <comment>TEXT</comment>      (with or without text)
# group(1) = the port opening + the opening of <comment;
# group(2) = the comment body;
# group(3) = the port closing.
def _port_by_index_re(idx: int) -> re.Pattern[bytes]:
    pat = (
        rb'(<port\s+index="' + str(int(idx)).encode("ascii") + rb'"[^>]*>\s*<comment)'
        rb'(\s*/>|\s*>[^<]*</comment>)'
        rb'(\s*</port>)'
    )
    return re.compile(pat, re.DOTALL)


def _build_comment_node(new_comment: str) -> bytes:
    """Build the new <comment...> node as latin-1 bytes.
    Empty -> self-closing ` />`; non-empty -> `>TEXT</comment>`.
    """
    if not new_comment:
        return b" />"
    text_bytes = xml_text_escape(new_comment).encode("latin-1", errors="replace")
    return b">" + text_bytes + b"</comment>"


def _set_port_comment(
    ports_block: bytes, port_index: int, new_comment: str,
) -> tuple[bytes, bool]:
    """In a <ports>...</ports>, replace the <port index=port_index> comment.
    Returns (new_bytes, was_updated). False when the block is self-closing
    (no port) or has no port with that index."""
    replacement = _build_comment_node(new_comment)
    pat = _port_by_index_re(port_index)

    def _sub(m: re.Match) -> bytes:
        return m.group(1) + replacement + m.group(3)

    new_block, n = pat.subn(_sub, ports_block, count=1)
    return new_block, n > 0


# updates: { element_id -> { (side, port_index) -> new_comment } }
PortUpdates = dict[str, dict[tuple[str, int], str]]


def update_port_comments_in_gle_bytes(
    raw: bytes, updates: PortUpdates,
) -> tuple[bytes, dict]:
    """Update the comments of specific ports by (element_id, side,
    port_index) in a GLE.

    `updates`: { element_id -> { (side, port_index) -> new_comment } }.
    `side` ∈ {"input","output"}. Empty string = force <comment /> self-closing.

    Returns (new_bytes, stats). stats has the keys:
      - elements_touched: # of elements with at least one port touched
      - ports_updated:    # total of port-comments replaced
      - ports_skipped:    update requests that did not find the expected port
      - elements_missing: ids in `updates` that were not found in the GLE
    """
    stats = {"elements_touched": 0, "ports_updated": 0,
             "ports_skipped": 0, "elements_missing": 0}
    seen_ids: set[str] = set()

    def replace_element(m: re.Match) -> bytes:
        eid = m.group("id").decode("ascii")
        if eid not in updates:
            return m.group(0)
        seen_ids.add(eid)
        upd = updates[eid]
        head = m.group("open")
        body = m.group("body")
        tail = m.group("close")

        # Locate the <ports>...</ports> blocks (at most 2: input and output).
        port_matches = list(_PORTS_BLOCK_RE.finditer(body))
        # Group updates by side to process them block-by-block.
        by_side: dict[str, dict[int, str]] = {"input": {}, "output": {}}
        for (side, idx), new_cmt in upd.items():
            if side in by_side:
                by_side[side][idx] = new_cmt

        out_parts: list[bytes] = []
        last_end = 0
        touched_here = 0
        for i, pm in enumerate(port_matches[:2]):
            out_parts.append(body[last_end:pm.start()])
            side = "input" if i == 0 else "output"
            side_updates = by_side.get(side, {})
            if not side_updates:
                out_parts.append(pm.group(0))
            else:
                new_block = pm.group(0)
                for idx, new_cmt in side_updates.items():
                    new_block, ok = _set_port_comment(new_block, idx, new_cmt)
                    if ok:
                        stats["ports_updated"] += 1
                        touched_here += 1
                    else:
                        stats["ports_skipped"] += 1
                out_parts.append(new_block)
            last_end = pm.end()
        out_parts.append(body[last_end:])

        # Updates aimed at a side whose <ports> does not exist -> skipped.
        for i in range(len(port_matches), 2):
            side = "input" if i == 0 else "output"
            stats["ports_skipped"] += len(by_side.get(side, {}))

        if touched_here:
            stats["elements_touched"] += 1
        return head + b"".join(out_parts) + tail

    new_raw = _GLE_ELEMENT_RE.sub(replace_element, raw)
    stats["elements_missing"] = len(set(updates.keys()) - seen_ids)
    return new_raw, stats


# -----------------------------------------------------------------------------
# Filter: what the user actually changed
# -----------------------------------------------------------------------------

def diff_updates_against_gle(
    gle_path: Path, updates: PortUpdates, relay_model=None,
) -> PortUpdates:
    """Filter updates, dropping (side, port_index) already equal in the GLE."""
    if not updates:
        return {}
    current: dict[str, dict[tuple[str, int], str]] = {}
    for p in extract_port_instances_from_gle(gle_path, relay_model=relay_model):
        current.setdefault(p.element_id, {})[(p.side, p.port_index)] = p.comment

    out: PortUpdates = {}
    for eid, ports in updates.items():
        if eid not in current:
            # element_id does not exist in the current GLE -- kept so the
            # writer can count it as missing.
            out[eid] = dict(ports)
            continue
        cur = current[eid]
        delta: dict[tuple[str, int], str] = {}
        for key, new_cmt in ports.items():
            if cur.get(key, "") != new_cmt:
                delta[key] = new_cmt
        if delta:
            out[eid] = delta
    return out
