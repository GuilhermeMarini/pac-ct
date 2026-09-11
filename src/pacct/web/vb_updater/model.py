"""What a GLE and an SCD say about a Virtual Bit, and the bytes that change one.

Two reads and two writes, and none of the four knows there is a web layer. The
reads turn a `.gle` into `{VBnnn: [GleVbInstance, ...]}` and an SCD's IED into a
`ScdVbMap` plus the ExtRef rows the spreadsheet and the comparison page are
built from -- through `py61850.scl`, which is where the standard half of an SCL
file is modelled. The writes take the descriptions the other side asked for and
replace exactly those, in the original bytes, leaving every other byte where it
was.

Pure `Path`/`bytes` in; dataclasses, dicts and `bytes` out. The spreadsheet that
carries these descriptions to the user and back is `export.py`'s business, the
RDB and SCD files it writes are too, the comparison page is `render.py`'s and
the routes are `handler.py`'s. The one thing reached for outside this module is
`rdb_write.xml_text_escape` -- one import of a function whose own docstring asks
to be the only definition of itself, which is the same single import
`gle_exporter/model.py` makes and for the same reason.

The SCD writer here edits serialised bytes with a regex rather than a document
through an edit model, which is a divergence from the reference implementation
and is written up in this phase's commit rather than absorbed. It was moved into
this module unchanged; nothing about it was introduced or decided here.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from py61850.scl import ExtRef, Ied, SclDocument
from sellib.gle import parse_gle
from sellib.scl.read import sel_goose_rx_status

from pacct.web.rdb_write import xml_text_escape

_logger = logging.getLogger(__name__)


# We only consider numeric VBs (GOOSE virtual bits). Math variables like
# VBY, VBZ, VBRMS, etc. are left out.
VB_NUMERIC_RE = re.compile(r"^VB(\d+)$", re.IGNORECASE)

# Comment used to rename VBs that show up in the SCD with an empty `desc`
# (i.e. declared as an ExtRef but with no description). Convention:
# "reserva" (spare) -- they mark a VB foreseen but not used.
RESERVA_LABEL = "reserva"

# A VB named by a GOOSE subscription's `pubRxStatus` does not carry a signal
# out of the dataset: it goes to 1 when the publisher stops arriving. It is
# the health of the subscription, and SEL Architect declares which VB gets it
# (see `sellib.scl.read.GooseRxStatus`).
#
# Two things follow, and both are why it cannot be left to look like an
# ordinary VB. Its ExtRef has no `doName`/`daName` -- there is no data
# attribute to point at -- so the Signal column used to render it as
# `IED/CFG/LLN0/GoSB00....`, four bare dots and no explanation. And when its
# `desc` is empty it used to be renamed "reserva", calling a live GOOSE
# supervision bit a spare (5 of the 202 in `samples/substation_demo.scd`).
#
# English, like the spreadsheet's own headers, and the same string on the web
# pages so the two never disagree about what one row is.
MESSAGE_QUALITY_LABEL = "Message Quality"


# -----------------------------------------------------------------------------
# Extractors
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class GleVbInstance:
    """One occurrence of a VBnnn SYMBOL in the GLE."""
    page: str          # name of the <page> holding the element
    element_id: str    # id of the <element type="SYMBOL"> wrapping the logic_element
    comment: str       # comment of the first non-empty <port> ("" if there is none)


def extract_vb_instances_from_gle(gle_path: Path) -> dict[str, list[GleVbInstance]]:
    """Read a GLE.xml and return {VBnnn: [GleVbInstance, ...]}.

    The same VB can appear several times in the diagram (on different pages,
    or even within one page); each occurrence yields an instance.

    Only takes SYMBOLs whose `physical_instance_name` matches `VB\\d+`. Each
    instance's comment is read from the first non-empty <port>. If no port
    has a comment, the instance is still recorded with `comment=""`.
    """
    out: dict[str, list[GleVbInstance]] = {}
    try:
        # `parse_gle` already handles mixed encoding (utf-8 declared but
        # latin-1 content) -- a common bug in GLEs exported by QuickSet.
        root = parse_gle(gle_path)
    except (OSError, ET.ParseError, UnicodeDecodeError) as e:
        _logger.warning("erro lendo GLE %s: %s", gle_path, e)
        return out

    # We iterate per page to preserve each SYMBOL's location.
    for page in root.iter("page"):
        page_name = (page.attrib.get("name") or "").strip()
        for el in page.iter("element"):
            if el.attrib.get("type") != "SYMBOL":
                continue
            elem_id = (el.attrib.get("id") or "").strip()
            for logic_el in el.iter("logic_element"):
                if logic_el.attrib.get("type") != "SYMBOL":
                    continue
                name = (logic_el.attrib.get("physical_instance_name") or "").strip()
                m = VB_NUMERIC_RE.match(name)
                if not m:
                    continue
                key = f"VB{int(m.group(1))}"
                # Find the first <port> with a non-empty comment (any index).
                # A GLE can have two <ports> blocks in a row -- the second is
                # the one carrying the data.
                port_comment = ""
                for port in logic_el.iter("port"):
                    comment_el = port.find("comment")
                    if comment_el is not None and (comment_el.text or "").strip():
                        port_comment = (comment_el.text or "").strip()
                        break
                out.setdefault(key, []).append(GleVbInstance(
                    page=page_name, element_id=elem_id, comment=port_comment,
                ))
    return out


@dataclass(frozen=True)
class ScdVbMap:
    """What one IED's SCD says about its VBs, from a single parse.

    `descs` is what it always was: {VBxxx: extref_desc}, "" when the ExtRef
    carries none. `quality` names the subset that receives a GOOSE
    subscription's health rather than a signal -- see
    `MESSAGE_QUALITY_LABEL`.
    """
    descs: dict[str, str]
    quality: frozenset[str]


def _find_ied(doc: SclDocument, ied_name: str, scd_path: Path) -> Ied | None:
    """The resolved IED, or None with a log line."""
    ied = doc.ied(ied_name)
    if ied is None:
        _logger.info("IED %r nao encontrado no SCD %s", ied_name, scd_path)
    return ied


# The SCL attribute names this module's Signal string is built from, and the
# `py61850.scl.ExtRef` field each comes off. The model reports every attribute
# as the file spells it, so this is a rename and never a reinterpretation --
# it is here only because `_format_extref_signal` is written against the SCL
# spellings, which is what an engineer comparing against the file reads.
_EXTREF_ATTR_FIELDS = {
    "iedName": "ied_name",
    "srcLDInst": "src_ld_inst",
    "srcLNClass": "src_ln_class",
    "srcCBName": "src_cb_name",
    "ldInst": "ld_inst",
    "prefix": "prefix",
    "lnClass": "ln_class",
    "lnInst": "ln_inst",
    "doName": "do_name",
    "daName": "da_name",
}


def _extref_attrs(ext: ExtRef) -> dict[str, str]:
    """One model ExtRef -> the `{SCL attribute: value}` dict the Signal is
    formatted from. An attribute the file omits is "" here, as it was when
    this read `element.attrib.get(k, "")`."""
    return {scl: getattr(ext, field) or ""
            for scl, field in _EXTREF_ATTR_FIELDS.items()}


def _quality_vbs(doc: SclDocument, ied_name: str) -> frozenset[str]:
    """The IED's VBs that receive a GOOSE subscription's health.

    Comes from `sellib`, which reads them off the `pubRxStatus` SEL Architect
    declares -- never off the shape of the ExtRef. `pubRxStatus` also names
    remote bits (5 of the 202 in `samples/substation_demo.scd` are `RBnn`);
    those fall outside `VB_NUMERIC_RE` and stay outside this tool, exactly
    as they were before.

    Normalised like every other VB key in this module: an SCD that writes
    `pubRxStatus="VB050"` against `intAddr="VB50"` must still line up.
    """
    out: set[str] = set()
    for bit in sel_goose_rx_status(doc).get(ied_name, {}):
        m = VB_NUMERIC_RE.match(bit)
        if m:
            out.add(f"VB{int(m.group(1))}")
    return frozenset(out)


def _vb_map_from_doc(
    doc: SclDocument, ied_name: str, scd_path: Path,
) -> ScdVbMap:
    target_ied = _find_ied(doc, ied_name, scd_path)
    if target_ied is None:
        return ScdVbMap(descs={}, quality=frozenset())
    health = _quality_vbs(doc, ied_name)
    out: dict[str, str] = {}
    quality: set[str] = set()
    for ext in target_ied.ext_refs():
        addr = (ext.int_addr or "").strip()
        m = VB_NUMERIC_RE.match(addr)
        if not m:
            continue
        key = f"VB{int(m.group(1))}"
        if key in health:
            quality.add(key)
        desc = (ext.desc or "").strip()
        existing = out.get(key, "")
        if existing and not desc:
            continue
        if desc or key not in out:
            out[key] = desc
    return ScdVbMap(descs=out, quality=frozenset(quality))


def extract_vb_map_from_scd_ied(scd_path: Path, ied_name: str) -> ScdVbMap:
    """Read an SCD once and return this IED's VB descriptions plus the VBs
    that carry a GOOSE subscription's health.

    Looks for every <ExtRef> inside <IED name=ied_name> whose `intAddr`
    matches VBnnn. Maps VBnnn -> the first non-empty `desc` found (several
    ExtRefs can reference the same intAddr).
    """
    doc = SclDocument.load(scd_path)
    if doc is None:
        return ScdVbMap(descs={}, quality=frozenset())
    return _vb_map_from_doc(doc, ied_name, scd_path)


def extract_vb_descriptions_from_scd_ied(scd_path: Path, ied_name: str) -> dict[str, str]:
    """Read an SCD and return {VBxxx: extref_desc} for the given IED.

    The descriptions alone; `extract_vb_map_from_scd_ied` is the same parse
    with the message-quality VBs alongside.
    """
    return extract_vb_map_from_scd_ied(scd_path, ied_name).descs


# ExtRef fields describing the GOOSE "signature". Order used to compose the
# Signal: <iedName>/<srcLDInst>/<srcLNClass>/<srcCBName>.<ldInst>.<prefix><lnClass><lnInst>.<doName>.<daName>
_EXTREF_FIELDS = (
    "iedName", "srcLDInst", "srcLNClass", "srcCBName",
    "ldInst", "prefix", "lnClass", "lnInst", "doName", "daName",
)


def _format_extref_signal(attrs: dict[str, str], *, quality: bool = False) -> str:
    """Build the Signal string out of an ExtRef's attributes.

    Returns "" when there is no `iedName` -- those ExtRefs are placeholders
    (intAddr defined but with no real subscription).

    A message-quality VB names a control block and nothing inside it, so the
    signature stops at the control block and says what it is. The old string
    ran the full template over the absent halves and produced
    `IED/CFG/LLN0/GoSB00....` -- four bare dots standing in for a logical
    node, a data object and a data attribute that do not exist.
    """
    ied = (attrs.get("iedName") or "").strip()
    if not ied:
        return ""
    src_ld = (attrs.get("srcLDInst") or "").strip()
    src_ln = (attrs.get("srcLNClass") or "").strip()
    src_cb = (attrs.get("srcCBName") or "").strip()
    if quality:
        return f"{MESSAGE_QUALITY_LABEL} — {ied}/{src_ld}/{src_ln}/{src_cb}"
    ld = (attrs.get("ldInst") or "").strip()
    prefix = (attrs.get("prefix") or "").strip()
    ln_cls = (attrs.get("lnClass") or "").strip()
    ln_inst = (attrs.get("lnInst") or "").strip()
    do = (attrs.get("doName") or "").strip()
    da = (attrs.get("daName") or "").strip()
    # `prefix` is optional in SCL -- only joined to lnClass+lnInst if present.
    ln_token = f"{prefix}{ln_cls}{ln_inst}"
    return f"{ied}/{src_ld}/{src_ln}/{src_cb}.{ld}.{ln_token}.{do}.{da}"


def extract_vb_extref_rows_from_scd_ied(
    scd_path: Path, ied_name: str,
) -> list[dict]:
    """Read an SCD and return a list of dicts {vb, signal, desc, quality} --
    one row per VBxxx found in the ExtRefs of the given IED.

    If one VB shows up in several ExtRefs (typical: 1 placeholder + 1 with a
    subscription), we prefer the one with `iedName` filled in (the real
    signal). If none has it, we keep the first (empty signal).

    `quality` is True for a VB that receives a GOOSE subscription's health
    (`pubRxStatus`); its Signal says so instead of naming a data attribute
    that does not exist.
    """
    doc = SclDocument.load(scd_path)
    if doc is None:
        return []
    return vb_rows_from_doc(doc, ied_name, scd_path)


def vb_rows_from_doc(
    doc: SclDocument, ied_name: str, scd_path: Path,
) -> list[dict]:
    rows_by_vb: dict[str, dict] = {}
    target_ied = _find_ied(doc, ied_name, scd_path)
    if target_ied is None:
        return []

    health = _quality_vbs(doc, ied_name)

    for ext in target_ied.ext_refs():
        addr = (ext.int_addr or "").strip()
        m = VB_NUMERIC_RE.match(addr)
        if not m:
            continue
        vb = f"VB{int(m.group(1))}"
        quality = vb in health
        signal = _format_extref_signal(_extref_attrs(ext), quality=quality)
        desc = (ext.desc or "").strip()
        row = {"vb": vb, "signal": signal, "desc": desc, "quality": quality}
        existing = rows_by_vb.get(vb)
        if existing is None:
            rows_by_vb[vb] = row
            continue
        # Prefer the row with the signal filled in (a real subscription).
        if not existing["signal"] and signal:
            rows_by_vb[vb] = row
    return sorted(rows_by_vb.values(), key=lambda r: int(r["vb"][2:]))


# -----------------------------------------------------------------------------
# Writers: GLE (inside the RDB) and SCD
# -----------------------------------------------------------------------------

# Matches a <logic_element type="SYMBOL" ... physical_instance_name="VBnnn" ...>
# ... </logic_element> in bytes. group(1) = attributes (to get the name),
# group(2) = body (between the tags).
_GLE_VB_BLOCK_RE = re.compile(
    rb'(<logic_element\s+type="SYMBOL"[^>]*physical_instance_name="VB(\d+)"[^>]*>)'
    rb'(.*?)'
    rb'(</logic_element>)',
    re.DOTALL,
)

# Matches <port ...> ... <comment>TEXT</comment> ... </port> inside a
# logic_element block. Limited to 1 substitution per block (the first port).
_GLE_PORT_COMMENT_RE = re.compile(
    rb'(<port\b[^>]*>\s*<comment>)([^<]*)(</comment>\s*</port>)',
    re.DOTALL,
)

# Matches <ExtRef ... intAddr="VBnnn" ... /> inside an IED. group(1) = the
# attributes before intAddr, group(2) = the VB number, group(3) = the ones
# after. IMPORTANT: the character class has to allow `/` -- `desc` values in
# the field routinely carry '/' (e.g. "50/62BF LT1 UPC1"); excluding `/` here
# would make the regex silently ignore those ExtRefs.
_SCD_EXTREF_RE = re.compile(
    rb'<ExtRef\b(?P<attrs>[^>]*?intAddr="VB(?P<num>\d+)"[^>]*?)(?P<close>/?>)',
    re.DOTALL,
)

_SCD_DESC_ATTR_RE = re.compile(rb'desc="[^"]*"')
_SCD_INTADDR_ATTR_RE = re.compile(rb'intAddr="VB\d+"')


def _xml_attr_escape(s: str) -> str:
    """Escape special characters for use inside an XML "..." attribute."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace('"', "&quot;"))


def substitute_vb_comments_in_gle_bytes(
    raw: bytes, new_comments: dict[str, str],
) -> tuple[bytes, dict[str, int]]:
    """Update the comment of the FIRST port of each VBnnn SYMBOL in the GLE.

    Returns (new_bytes, stats). `stats` has the keys:
      - "updated":   number of VBnnn instances replaced
      - "skipped":   VBnnn in `new_comments` with no port-comment found
      - "untouched": VBnnn in the GLE but with no entry in `new_comments`
    """
    stats = {"updated": 0, "skipped": 0, "untouched": 0}

    def replace_block(m: re.Match) -> bytes:
        head = m.group(1)
        num_str = m.group(2).decode("ascii")
        body = m.group(3)
        tail = m.group(4)
        key = f"VB{int(num_str)}"
        new_text = new_comments.get(key)
        if new_text is None:
            stats["untouched"] += 1
            return m.group(0)
        # ESCAPED, then encoded latin-1 to match the rest of the GLE
        # (QuickSet writes latin-1 even though it declares utf-8 in the
        # header). The text is an SCD's `ExtRef desc`, which ElementTree hands
        # over already unescaped -- so a signal called "50/62BF & LT1" wrote a
        # bare `&` into `<comment>` and the GLE stopped being well-formed.
        # Both siblings already did this: `update_scd_extrefs_for_ied` below
        # (`_xml_attr_escape`) and the GLE Exporter's own comment writer. This
        # was the one of the three that did not.
        new_text_bytes = xml_text_escape(new_text).encode(
            "latin-1", errors="replace")
        new_body, count = _GLE_PORT_COMMENT_RE.subn(
            lambda pm: pm.group(1) + new_text_bytes + pm.group(3),
            body,
            count=1,
        )
        if count == 0:
            stats["skipped"] += 1
            return m.group(0)
        stats["updated"] += 1
        return head + new_body + tail

    new_raw = _GLE_VB_BLOCK_RE.sub(replace_block, raw)
    return new_raw, stats


def update_scd_extrefs_for_ied(
    raw: bytes, ied_name: str, new_descs: dict[str, str],
) -> tuple[bytes, dict[str, int]]:
    """Update the `desc` attribute of each <ExtRef intAddr="VBnnn"> inside
    the IED `ied_name`. If the ExtRef has no `desc=`, insert it right after
    `intAddr=`.

    Returns (new_bytes, stats). The bytes can change size (an SCD is plain
    XML, with no constraint).
    """
    stats = {"updated": 0, "inserted": 0, "untouched": 0}

    # Find the specific IED's block.
    ied_re = re.compile(
        rb'<IED\b[^>]*name="' + re.escape(ied_name.encode("utf-8")) + rb'"[^>]*>',
    )
    open_match = ied_re.search(raw)
    if not open_match:
        return raw, stats
    # The matching </IED>: we search from open_match onward. Since the SCD
    # has no nested IEDs, the next `</IED>` is enough.
    close_idx = raw.find(b"</IED>", open_match.end())
    if close_idx == -1:
        return raw, stats
    inner_start = open_match.end()
    inner_end = close_idx

    def replace_extref(em: re.Match) -> bytes:
        full = em.group(0)
        num_str = em.group("num").decode("ascii")
        key = f"VB{int(num_str)}"
        new = new_descs.get(key)
        if not new:
            stats["untouched"] += 1
            return full
        new_attr = ('desc="' + _xml_attr_escape(new) + '"').encode("utf-8")
        if _SCD_DESC_ATTR_RE.search(full):
            new_full, n = _SCD_DESC_ATTR_RE.subn(new_attr, full, count=1)
            if n > 0:
                stats["updated"] += 1
                return new_full
        # Insere apos intAddr="..."
        def insert(im: re.Match) -> bytes:
            return im.group(0) + b" " + new_attr
        new_full, n = _SCD_INTADDR_ATTR_RE.subn(insert, full, count=1)
        if n > 0:
            stats["inserted"] += 1
            return new_full
        return full  # caso degenerado

    inner_new = _SCD_EXTREF_RE.sub(replace_extref, raw[inner_start:inner_end])
    return raw[:inner_start] + inner_new + raw[inner_end:], stats


def new_comments_from_scd(
    scd_path: Path, ied_name: str,
) -> tuple[dict, int, int, int]:
    """The descriptions the SCD sends to the GLE, plus the report's counts.

    A VB that exists as an ExtRef but with an empty `desc` becomes "reserva":
    it is a slot foreseen and unused, and leaving the old comment there would
    be worse -- it would describe a signal that no longer exists.

    Unless it is a message-quality VB, which is the opposite case: it is in
    use, just not carrying a signal, so it gets `MESSAGE_QUALITY_LABEL`
    instead. 5 of the 202 in `samples/substation_demo.scd` have no `desc` and
    were being labelled spare.

    A `desc` the engineer wrote always wins, message-quality or not -- the
    other 197 keep "FALHA GOOSE LT2 UPC1" and the marker never overwrites it.
    The returned `quality` count therefore spans both branches: it says how
    many of this IED's VBs are message quality, not how many were relabelled.
    """
    new_comments: dict[str, str] = {}
    reserva = 0
    with_desc = 0
    quality = 0
    vb_map = extract_vb_map_from_scd_ied(scd_path, ied_name)
    for vb, desc in vb_map.descs.items():
        is_quality = vb in vb_map.quality
        if is_quality:
            quality += 1
        if desc:
            new_comments[vb] = desc
            with_desc += 1
        elif is_quality:
            new_comments[vb] = MESSAGE_QUALITY_LABEL
        else:
            new_comments[vb] = RESERVA_LABEL
            reserva += 1
    return new_comments, with_desc, reserva, quality
