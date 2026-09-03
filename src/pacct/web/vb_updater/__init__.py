"""
VB Updater: cross-matches Virtual Bit descriptions between the RDB's GLE
(the output port comment of the VBxxx SYMBOL) and the SCD (`desc` attribute
of the <ExtRef intAddr="VBxxx"> under the matching IED).

Flow:
  1. The user uploads an RDB and an SCD.
  2. The app cross-matches RDB <-> SCD using `selfiles.match`.
  3. For each matched pair it shows a GLE selector + a "Verify GLE comments"
     button.
  4. Clicking the button opens a dedicated page with the comparison table
     (VBxxx | GLE comment | SCD desc); empty cells show
     "<Without description>".

    templates/  landing.html and compare.html
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from selfiles import match as matcher
from selfiles import rdb as rdb_loader
from selfiles.gle import parse_gle
from selfiles.rdb import RdbInfo
from selfiles.scl import read as scd_loader

from pacct.paths import VB_UPDATER_TEMPLATES_DIR, is_within
from pacct.web import rdb_write
from pacct.web.project_files import library as filelib
from pacct.web.rdb_write import (
    resolve_gle_stream_path,
)
from pacct.web.rdb_write import (
    with_suffix_before_ext as _with_suffix_before_ext,
)
from pacct.web.session import SessionHandler
from pacct.web.xlsx_names import sanitize_sheet_name as _sanitize_sheet_name

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (VB_UPDATER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# Generous upload ceiling (used only by /import-descriptions, which takes
# the xlsx back -- the RDB and the SCD themselves come from the library).
_SCD_MAX_BYTES = 200 * 1024 * 1024

# We only consider numeric VBs (GOOSE virtual bits). Math variables like
# VBY, VBZ, VBRMS, etc. are left out.
_VB_NUMERIC_RE = re.compile(r"^VB(\d+)$", re.IGNORECASE)

# Directory where the uploaded SCDs are saved. It lives in cache/ so it is
# gitignored automatically.
# Uploads and outputs live in cache/sessions/<sid>/ (see pacct.web.session).

_EMPTY_LABEL = "&lt;Without description&gt;"

# Comment used to rename VBs that show up in the SCD with an empty `desc`
# (i.e. declared as an ExtRef but with no description). Convention:
# "reserva" (spare) -- they mark a VB foreseen but not used.
_RESERVA_LABEL = "reserva"


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
                m = _VB_NUMERIC_RE.match(name)
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


def extract_vb_descriptions_from_scd_ied(scd_path: Path, ied_name: str) -> dict[str, str]:
    """Read an SCD and return {VBxxx: extref_desc} for the given IED.

    Looks for every <ExtRef> inside <IED name=ied_name> whose `intAddr`
    matches VBnnn. Maps VBnnn -> the first non-empty `desc` found (several
    ExtRefs can reference the same intAddr).
    """
    out: dict[str, str] = {}
    try:
        tree = ET.parse(str(scd_path))
    except (OSError, ET.ParseError) as e:
        _logger.warning("erro lendo SCD %s: %s", scd_path, e)
        return out

    root = tree.getroot()
    target_ied = None
    # _iter_local ignores the namespace, so 'IED' matches '{ns}IED'.
    for el in scd_loader._iter_local(root, "IED"):
        if el.attrib.get("name") == ied_name:
            target_ied = el
            break
    if target_ied is None:
        _logger.info("IED %r nao encontrado no SCD %s", ied_name, scd_path)
        return out

    for ext in scd_loader._iter_local(target_ied, "ExtRef"):
        addr = (ext.attrib.get("intAddr") or "").strip()
        m = _VB_NUMERIC_RE.match(addr)
        if not m:
            continue
        key = f"VB{int(m.group(1))}"
        desc = (ext.attrib.get("desc") or "").strip()
        existing = out.get(key, "")
        if existing and not desc:
            continue
        if desc or key not in out:
            out[key] = desc
    return out


# ExtRef fields describing the GOOSE "signature". Order used to compose the
# Signal: <iedName>/<srcLDInst>/<srcLNClass>/<srcCBName>.<ldInst>.<prefix><lnClass><lnInst>.<doName>.<daName>
_EXTREF_FIELDS = (
    "iedName", "srcLDInst", "srcLNClass", "srcCBName",
    "ldInst", "prefix", "lnClass", "lnInst", "doName", "daName",
)


def _format_extref_signal(attrs: dict[str, str]) -> str:
    """Build the Signal string out of an ExtRef's attributes.

    Returns "" when there is no `iedName` -- those ExtRefs are placeholders
    (intAddr defined but with no real subscription).
    """
    ied = (attrs.get("iedName") or "").strip()
    if not ied:
        return ""
    src_ld = (attrs.get("srcLDInst") or "").strip()
    src_ln = (attrs.get("srcLNClass") or "").strip()
    src_cb = (attrs.get("srcCBName") or "").strip()
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
    """Read an SCD and return a list of dicts {vb, signal, desc} -- one row
    per VBxxx found in the ExtRefs of the given IED.

    If one VB shows up in several ExtRefs (typical: 1 placeholder + 1 with a
    subscription), we prefer the one with `iedName` filled in (the real
    signal). If none has it, we keep the first (empty signal).
    """
    rows_by_vb: dict[str, dict] = {}
    try:
        tree = ET.parse(str(scd_path))
    except (OSError, ET.ParseError) as e:
        _logger.warning("erro lendo SCD %s: %s", scd_path, e)
        return []

    root = tree.getroot()
    target_ied = None
    for el in scd_loader._iter_local(root, "IED"):
        if el.attrib.get("name") == ied_name:
            target_ied = el
            break
    if target_ied is None:
        return []

    for ext in scd_loader._iter_local(target_ied, "ExtRef"):
        addr = (ext.attrib.get("intAddr") or "").strip()
        m = _VB_NUMERIC_RE.match(addr)
        if not m:
            continue
        vb = f"VB{int(m.group(1))}"
        attrs = {k: ext.attrib.get(k, "") for k in _EXTREF_FIELDS}
        signal = _format_extref_signal(attrs)
        desc = (ext.attrib.get("desc") or "").strip()
        row = {"vb": vb, "signal": signal, "desc": desc}
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


def _substitute_vb_comments_in_gle_bytes(
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
        # Encode in latin-1 to match the rest of the GLE (Quickset writes
        # latin-1 even though it declares utf-8 in the header).
        new_text_bytes = new_text.encode("latin-1", errors="replace")
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


def _update_scd_extrefs_for_ied(
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


# -----------------------------------------------------------------------------
# Orchestrators: copy file + apply substitutions + write `_comments_updated`
# -----------------------------------------------------------------------------

def _gle_stream_path(extract_dir: Path, relay_name: str, gle_name: str,
                     gle_fs_path: Path) -> list[str]:
    """The GLE's stream path inside the OLE, with the optimistic guess.

    When the extracted file is not under `extract_dir` -- which does not
    happen for a GLE coming from `RdbInfo` -- it builds the path out of the
    names, which is the layout every RDB in the corpus uses.
    """
    return resolve_gle_stream_path(
        extract_dir, gle_fs_path,
        fallback=["Relays", relay_name, "Misc", f"{gle_name}.gle"],
    )


def _new_comments_from_scd(scd_path: Path, ied_name: str) -> tuple[dict, int, int]:
    """The descriptions the SCD sends to the GLE, plus the report's two
    counts.

    A VB that exists as an ExtRef but with an empty `desc` becomes "reserva":
    it is a slot foreseen and unused, and leaving the old comment there would
    be worse -- it would describe a signal that no longer exists.
    """
    new_comments: dict[str, str] = {}
    reserva = 0
    with_desc = 0
    for vb, desc in extract_vb_descriptions_from_scd_ied(scd_path, ied_name).items():
        if desc:
            new_comments[vb] = desc
            with_desc += 1
        else:
            new_comments[vb] = _RESERVA_LABEL
            reserva += 1
    return new_comments, with_desc, reserva


def _apply_stats(sub_stats: dict, with_desc: int, reserva: int,
                 original_size: int) -> dict:
    return {
        "instances_updated": sub_stats["updated"],
        "vbs_not_found_in_gle": sub_stats["skipped"],
        "vbs_in_gle_not_in_scd": sub_stats["untouched"],
        "vbs_in_scd_with_desc": with_desc,
        "vbs_in_scd_renamed_to_reserva": reserva,
        "reserva_label": _RESERVA_LABEL,
        "original_stream_bytes": original_size,
    }


def update_rdb_with_scd_descs(
    *,
    rdb_path: Path,
    extract_dir: Path,
    relay_name: str,
    gle_name: str,
    gle_fs_path: Path,
    scd_path: Path,
    ied_name: str,
    output_path: Path,
    job=None,
) -> dict:
    """Produce `output_path` (a copy of the RDB) with the selected GLE
    updated: the VBnnn port comments replaced by the SCD's `desc`.

    One read pass, then one write. `rdb_write.write_streams` chooses between
    rewriting the stream in place (when the size matches) and rebuilding the
    container (when it does not), and writes atomically. Before, the stream
    had to fit the original size and the excess was bought by collapsing
    whitespace across the whole `.gle` -- see `pacct/web/rdb_write.py`.

    With `ok: False`, no file was written.
    """
    new_comments, with_desc, reserva = _new_comments_from_scd(scd_path, ied_name)
    if not new_comments:
        return {
            "ok": False,
            "error": f"SCD nao tem nenhum ExtRef VBnnn para o IED {ied_name!r}.",
        }

    original = Path(gle_fs_path).read_bytes()
    updated, sub_stats = _substitute_vb_comments_in_gle_bytes(original, new_comments)
    stream_parts = _gle_stream_path(extract_dir, relay_name, gle_name, gle_fs_path)

    try:
        method = rdb_write.write_streams(
            rdb_path, output_path, {tuple(stream_parts): updated}, job=job)
    except rdb_write.RdbWriteError as e:
        return {"ok": False, "error": str(e),
                "stats": _apply_stats(sub_stats, with_desc, reserva, len(original))}

    return {
        "ok": True,
        "output_path": str(output_path),
        "method": method,
        "stream": "/".join(stream_parts),
        "stats": _apply_stats(sub_stats, with_desc, reserva, len(original)),
    }


def update_rdb_with_scd_descs_batch(
    *,
    rdb_path: Path,
    extract_dir: Path,
    scd_path: Path,
    selections: list[dict],
    output_path: Path,
    job=None,
) -> dict:
    """Apply `update_rdb_with_scd_descs` to several relays in a single RDB.

    `selections` is a list of dicts {relay, ied, gle_name, gle_fs_path}.

    All-or-nothing, and that is the change that matters here. Before, each
    selection was written into the output RDB as soon as it was ready: if the
    third failed, the first two were already in the file, the response still
    said `ok: True` and that half-applied RDB entered the project library
    just like a whole one. Once it leaves here there is no telling the two
    apart.

    Returns {ok, output_path, method, succeeded, failed, results}. With
    `ok: False`, NO file was written.
    """
    results: list[dict] = []
    streams: dict[tuple[str, ...], bytes] = {}

    if job:
        job.stage("Conferindo as seleções", 10)
    for sel in selections:
        relay = sel["relay"]
        ied = sel["ied"]
        gle_name = sel["gle_name"]
        gle_fs_path = Path(sel["gle_fs_path"])
        entry_result: dict = {"relay": relay, "ied": ied, "gle": gle_name}

        new_comments, with_desc, reserva = _new_comments_from_scd(scd_path, ied)
        if not new_comments:
            entry_result["ok"] = False
            entry_result["error"] = f"SCD sem ExtRef VBnnn para IED {ied!r}."
            results.append(entry_result)
            continue

        if not gle_fs_path.is_file():
            entry_result["ok"] = False
            entry_result["error"] = f"GLE nao encontrado: {gle_fs_path.name}"
            results.append(entry_result)
            continue

        stream_parts = tuple(_gle_stream_path(extract_dir, relay, gle_name,
                                              gle_fs_path))
        original = gle_fs_path.read_bytes()
        updated, sub_stats = _substitute_vb_comments_in_gle_bytes(
            original, new_comments)
        streams[stream_parts] = updated
        entry_result["ok"] = True
        entry_result["stats"] = _apply_stats(sub_stats, with_desc, reserva,
                                             len(original))
        results.append(entry_result)

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    if failed or not streams:
        return {
            "ok": False,
            "error": (f"{failed} de {len(results)} seleção(ões) falharam; "
                      "nenhum RDB foi gravado."
                      if failed else "Nenhuma seleção produziu alteração."),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }

    try:
        method = rdb_write.write_streams(rdb_path, output_path, streams, job=job)
    except rdb_write.RdbWriteError as e:
        return {"ok": False, "error": str(e), "succeeded": succeeded,
                "failed": failed, "results": results}

    return {
        "ok": True,
        "output_path": str(output_path),
        "method": method,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def update_scd_with_gle_comments(
    *,
    scd_path: Path,
    ied_name: str,
    gle_path: Path,
    output_path: Path,
) -> dict:
    """Produce `output_path` (a copy of the SCD) with the <ExtRef desc=> of
    the IED `ied_name` updated from the GLE's VBnnn port comments.

    When the same VB appears several times in the GLE with different
    comments, it uses the FIRST non-empty comment found (and adds a warning).
    """
    gle_map = extract_vb_instances_from_gle(gle_path)

    new_descs: dict[str, str] = {}
    inconsistent: list[str] = []
    for vb, insts in gle_map.items():
        first_non_empty = ""
        all_comments: set[str] = set()
        for inst in insts:
            if inst.comment:
                if not first_non_empty:
                    first_non_empty = inst.comment
                all_comments.add(inst.comment)
        if first_non_empty:
            new_descs[vb] = first_non_empty
        if len(all_comments) > 1:
            inconsistent.append(vb)

    if not new_descs:
        return {"ok": False, "error": "GLE nao tem nenhum comment nao vazio para VBs."}

    scd_bytes = scd_path.read_bytes()
    updated, stats = _update_scd_extrefs_for_ied(scd_bytes, ied_name, new_descs)
    if stats["updated"] + stats["inserted"] == 0:
        return {
            "ok": False,
            "error": f"IED {ied_name!r} nao encontrado no SCD ou sem ExtRef VBnnn.",
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(updated)

    return {
        "ok": True,
        "output_path": str(output_path),
        "stats": {
            "extrefs_updated": stats["updated"],
            "extrefs_inserted_desc": stats["inserted"],
            "vbs_unchanged_no_match": stats["untouched"],
            "vbs_in_gle_with_comment": len(new_descs),
            "vbs_with_inconsistent_gle_comments": inconsistent,
            "original_bytes": len(scd_bytes),
            "output_bytes": len(updated),
        },
    }


# -----------------------------------------------------------------------------
# Excel I/O: export descriptions to xlsx, parse edited xlsx back
# -----------------------------------------------------------------------------

# Excel sheet names: max 31 chars, no `:\\/?*[]`. We sanitise them to avoid
# openpyxl errors when saving.

# Header expected on rows 1 and 3 of the sheet. The marker in A1 identifies
# the file as coming from the export (and not some random xlsx).
_XLSX_IED_MARKER = "IED:"
_XLSX_HEADER_VB = "VB"
_XLSX_HEADER_SIGNAL = "Signal"
_XLSX_HEADER_DESC = "Description"


def build_vb_descriptions_xlsx(
    *, scd_path: Path, ied_names: list[str], rdb_by_ied: dict[str, str] | None = None,
) -> bytes:
    """Build an .xlsx (bytes) with one sheet per IED in `ied_names`.

    Structure per sheet:
      A1: "IED:"           B1: <ied_name>            (marker + identification)
      A2: "Relay:"         B2: <rdb_relay_name>      (only if rdb_by_ied given)
      Row 3 blank
      Row 4: headers       ["VB", "Signal", "Description"]
      Row 5+: data

    The importer reads the IED from B1 (not from the sheet name), so the user
    can rename/reorder sheets without breaking the re-import.
    """
    # Local import: openpyxl is an optional dependency used only here.
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    # Remove the default sheet; we create our own.
    default = wb.active
    wb.remove(default)

    used: set[str] = set()
    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = PatternFill("solid", fgColor="FF1F6FEB")
    meta_font = Font(bold=True)

    for ied in ied_names:
        rows = extract_vb_extref_rows_from_scd_ied(scd_path, ied)
        ws = wb.create_sheet(title=_sanitize_sheet_name(ied, used))
        ws["A1"] = _XLSX_IED_MARKER
        ws["B1"] = ied
        ws["A1"].font = meta_font
        rdb_name = (rdb_by_ied or {}).get(ied)
        if rdb_name:
            ws["A2"] = "Relay:"
            ws["B2"] = rdb_name
            ws["A2"].font = meta_font
        # Header on row 4.
        for col, val in enumerate(
            (_XLSX_HEADER_VB, _XLSX_HEADER_SIGNAL, _XLSX_HEADER_DESC), start=1,
        ):
            c = ws.cell(row=4, column=col, value=val)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="left")
        for i, row in enumerate(rows, start=5):
            ws.cell(row=i, column=1, value=row["vb"])
            ws.cell(row=i, column=2, value=row["signal"])
            ws.cell(row=i, column=3, value=row["desc"])
        # Crude auto-width (openpyxl has no native auto-fit).
        widths = {1: 10, 2: 90, 3: 60}
        for col_idx, w in widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = w
        # Freeze pane below the header.
        ws.freeze_panes = "A5"

    if not wb.sheetnames:
        # openpyxl requires at least one sheet in order to save.
        wb.create_sheet(title="empty")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_vb_descriptions_xlsx(xlsx_bytes: bytes) -> dict[str, dict[str, str]]:
    """Read an xlsx built by `build_vb_descriptions_xlsx` (or edited) and
    return {ied_name: {VBxxx: new_description}}.

    Rules:
      - Each sheet's IED is read from B1 (not from the title). Sheets with no
        "IED:" marker in A1, or an empty B1, are ignored.
      - After the header row (4), rows with an empty VB or an empty
        description are skipped (empty = do not touch).
      - VBs are normalised to VB{int(n)} (case-insensitive).
      - If the same VB appears twice on one sheet, the last description wins.
    """
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(filename=BytesIO(xlsx_bytes), data_only=True, read_only=True)
    out: dict[str, dict[str, str]] = {}
    try:
        for ws in wb.worksheets:
            # Check the A1 marker and read the IED from B1.
            a1 = ws.cell(row=1, column=1).value
            b1 = ws.cell(row=1, column=2).value
            if not isinstance(a1, str) or a1.strip() != _XLSX_IED_MARKER:
                continue
            ied = (str(b1).strip() if b1 is not None else "")
            if not ied:
                continue
            per_ied: dict[str, str] = out.setdefault(ied, {})
            # Iterate from row 5 (data).
            for row in ws.iter_rows(min_row=5, max_col=3, values_only=True):
                if row is None:
                    continue
                vb_val, _signal, desc_val = (row + (None, None, None))[:3]
                if vb_val is None:
                    continue
                vb_str = str(vb_val).strip()
                m = _VB_NUMERIC_RE.match(vb_str)
                if not m:
                    continue
                key = f"VB{int(m.group(1))}"
                if desc_val is None:
                    continue
                desc = str(desc_val).strip()
                if not desc:
                    continue
                per_ied[key] = desc
    finally:
        wb.close()
    # Drop IEDs with no valid desc at all.
    return {k: v for k, v in out.items() if v}


def update_scd_with_descriptions_multi(
    *, scd_path: Path, descriptions_by_ied: dict[str, dict[str, str]],
    output_path: Path,
) -> dict:
    """Apply new descriptions to several IEDs in a single SCD and write to
    `output_path`. Reuses `_update_scd_extrefs_for_ied` per IED.

    Returns a dict with per_ied stats and totals.
    """
    raw = scd_path.read_bytes()
    per_ied_stats: dict[str, dict] = {}
    skipped_no_ied: list[str] = []
    totals = {"updated": 0, "inserted": 0, "untouched": 0}

    for ied, vb_map in descriptions_by_ied.items():
        if not vb_map:
            continue
        new_raw, stats = _update_scd_extrefs_for_ied(raw, ied, vb_map)
        if stats["updated"] + stats["inserted"] == 0 and stats["untouched"] == 0:
            # IED not found, or with no VBnnn ExtRef.
            skipped_no_ied.append(ied)
            continue
        raw = new_raw
        per_ied_stats[ied] = stats
        totals["updated"] += stats["updated"]
        totals["inserted"] += stats["inserted"]
        totals["untouched"] += stats["untouched"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    return {
        "ok": True,
        "output_path": str(output_path),
        "per_ied": per_ied_stats,
        "skipped_ieds": skipped_no_ied,
        "totals": totals,
        "output_bytes": len(raw),
    }


# -----------------------------------------------------------------------------
# Session state (module-global, one instance per process)
# -----------------------------------------------------------------------------

@dataclass
class _SessionState:
    rdb: RdbInfo | None = None
    scd_path: Path | None = None
    scd_name: str | None = None
    match_report: matcher.MatchReport | None = None


def _maybe_match(st: _SessionState) -> None:
    """Run the cross-match if RDB and SCD exist. Call under the session lock."""
    if st.rdb is None or st.scd_path is None:
        st.match_report = None
        return
    extract_dir = st.rdb.extract_dir
    try:
        st.match_report = matcher.compare_relays_to_scd(
            st.rdb.relays, extract_dir, st.scd_path,
        )
    except Exception as e:
        _logger.exception("falha no cross-match: %s", e)
        st.match_report = None


def _state_payload(st: _SessionState) -> dict:
    d = {
        "has_rdb": st.rdb is not None,
        "has_scd": st.scd_path is not None,
        "rdb_name": st.rdb.display_name if st.rdb else None,
        "scd_name": st.scd_name,
        "matches": [],
        "unmatched_rdb": [],
        "unmatched_scd": [],
    }
    if st.match_report is not None:
        d["matches"] = [m.to_dict() for m in st.match_report.matched]
        d["unmatched_rdb"] = [u.to_dict() for u in st.match_report.unmatched_rdb]
        d["unmatched_scd"] = [u.to_dict() for u in st.match_report.unmatched_scd]
    # GLEs per relay (to populate each row's <select>)
    if st.rdb is not None:
        gles_by_relay: dict[str, list[str]] = {}
        for r in st.rdb.relays:
            gles_by_relay[r.name] = [g.name for g in r.gles]
        d["gles_by_relay"] = gles_by_relay
    else:
        d["gles_by_relay"] = {}
    return d


# -----------------------------------------------------------------------------
# Comparison page renderer
# -----------------------------------------------------------------------------

def _render_compare_page(rdb_relay: str, ied_name: str, gle_name: str,
                         gle_path: Path, scd_path: Path) -> str:
    """Build the full HTML of the comparison page.

    One row per VB instance in the GLE. If a VB appears N times in the GLE,
    N rows are produced (all compared against the same SCD desc). If it does
    not appear in the GLE but exists in the SCD, it becomes 1 row with an
    empty GLE.
    """
    gle_map = extract_vb_instances_from_gle(gle_path)
    scd_map = extract_vb_descriptions_from_scd_ied(scd_path, ied_name)

    all_vbs = sorted(
        set(gle_map.keys()) | set(scd_map.keys()),
        key=lambda k: int(k[2:]),
    )

    rows = []
    equal_count = 0
    diff_count = 0

    def _row(vb: str, gle_comment: str, scd_desc: str, loc: str) -> str:
        is_diff = (gle_comment != scd_desc)
        nonlocal equal_count, diff_count
        if is_diff:
            diff_count += 1
        else:
            equal_count += 1
        g_html = escape(gle_comment) if gle_comment else _EMPTY_LABEL
        s_html = escape(scd_desc) if scd_desc else _EMPTY_LABEL
        g_cls = "cell" + ("" if gle_comment else " empty")
        s_cls = "cell" + ("" if scd_desc else " empty")
        loc_html = f'<div class="loc">{escape(loc)}</div>' if loc else ""
        cls = ' class="diff"' if is_diff else ""
        return (
            f'<tr{cls}>'
            f'<td class="vb">{escape(vb)}{loc_html}</td>'
            f'<td class="{g_cls}">{g_html}</td>'
            f'<td class="{s_cls}">{s_html}</td>'
            f'</tr>'
        )

    for vb in all_vbs:
        instances = gle_map.get(vb, [])
        scd_desc = scd_map.get(vb, "")
        if not instances:
            # The VB exists in the SCD but not in the GLE.
            rows.append(_row(vb, "", scd_desc, "(ausente no GLE)"))
            continue
        for inst in instances:
            parts = [p for p in (inst.page, f"#{inst.element_id}" if inst.element_id else "") if p]
            loc = " / ".join(parts)
            rows.append(_row(vb, inst.comment, scd_desc, loc))

    total_rows = equal_count + diff_count
    summary = (
        f'<span class="ok">{equal_count} iguais</span> &nbsp; '
        f'<span class="warn">{diff_count} divergentes</span> &nbsp; '
        f'<span class="muted">{total_rows} instancia(s) &middot; '
        f'{len(all_vbs)} VB(s) unico(s)</span>'
    )

    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="3" class="muted" style="text-align:center;padding:24px">'
        'Nenhum VB encontrado nos dois lados.</td></tr>'
    )

    return COMPARE_HTML_TEMPLATE.format(
        rdb_relay=escape(rdb_relay),
        ied_name=escape(ied_name),
        gle_name=escape(gle_name),
        summary=summary,
        rows=body_rows,
    )


# -----------------------------------------------------------------------------
# HTML
# -----------------------------------------------------------------------------

LANDING_HTML = load_template("landing.html")

# The numbered navigation is the same on the nine screens -- lives in theme.py.


COMPARE_HTML_TEMPLATE = load_template("compare.html")



# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_vb_updater_handler(logger: logging.Logger, sessions) -> type:
    """Return the VB Updater handler class.

    Opens no server: serving is done by the single dispatcher of
    `pacct.web.mount`, which mounts this handler at `/vb-updater/`. State and
    uploads are per session (`self.sess()` / `self.sdir()`), not per process.
    """

    class Handler(SessionHandler):
        session_key = "vb-updater"
        state_factory = _SessionState
        server_sessions = sessions

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/vb-state":
                # Sentinel the home/dashboard uses to detect that this
                # tool is up.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                self._send_json(200, _state_payload(self.sess()))
                return
            if path == "/download":
                # Serves a file generated by /apply as a download. Sandboxed
                # to the session's own directories to prevent
                # path traversal.
                file_param = (qs.get("file") or [""])[0]
                if not file_param:
                    self._send(400, "missing 'file' param", "text/plain")
                    return
                target = Path(file_param).resolve()
                # `rdbs` is gone: uploads now go to the content cache,
                # which is shared -- letting it into the sandbox would hand
                # one visitor the derived file generated by another.
                if not is_within(target, (self.sdir("out"), self.sdir("scd"))):
                    self._send(403, "path outside allowed roots", "text/plain")
                    return
                if not target.is_file():
                    self._send(404, "file not found", "text/plain")
                    return
                data = target.read_bytes()
                ext = target.suffix.lower()
                if ext == ".rdb":
                    ctype = "application/octet-stream"
                elif ext == ".xlsx":
                    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                else:
                    ctype = "application/xml"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{target.name}"',
                )
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/compare":
                relay = (qs.get("relay") or [""])[0]
                ied = (qs.get("ied") or [""])[0]
                gle = (qs.get("gle") or [""])[0]
                with self.session.lock:
                    st = self.sess()
                    rdb = st.rdb
                    scd_path = st.scd_path
                if rdb is None or scd_path is None:
                    self._send(409, "RDB ou SCD nao carregado", "text/plain")
                    return
                entry = rdb_loader.find_gle(rdb, relay, gle)
                if entry is None or not entry.fs_path.is_file():
                    self._send(404, f"GLE {gle!r} nao encontrado para o rele {relay!r}",
                               "text/plain")
                    return
                try:
                    html = _render_compare_page(
                        rdb_relay=relay, ied_name=ied, gle_name=gle,
                        gle_path=entry.fs_path, scd_path=scd_path,
                    )
                except Exception as e:
                    logger.exception("falha renderizando comparacao: %s", e)
                    self._send(500, f"falha: {e}", "text/plain")
                    return
                self._send(200, html, "text/html; charset=utf-8")
                return

            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0

            if path == "/apply":
                job = self.job()
                job.stage("Aplicando alteracoes", 10)
                body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(body or b"{}")
                    direction = str(payload.get("direction", "")).strip()
                    relay = str(payload.get("relay", "")).strip()
                    ied = str(payload.get("ied", "")).strip()
                    gle = str(payload.get("gle", "")).strip()
                except (json.JSONDecodeError, TypeError):
                    self._send_json(400, {"error": "bad request"})
                    return
                if direction not in ("scd-to-gle", "gle-to-scd"):
                    self._send_json(400, {"error": "direction invalida"})
                    return
                with self.session.lock:
                    st = self.sess()
                    rdb = st.rdb
                    scd_path = st.scd_path
                    # The DISPLAY name, not the file's: the library keeps
                    # the SCD as `<sha12>.scd`, so deriving the output from
                    # the path named the user's file
                    # "72586aeda11e_comments_updated.scd".
                    scd_label = Path(st.scd_name or (scd_path.name if scd_path
                                                     else "arquivo.scd"))
                if rdb is None or scd_path is None:
                    self._send_json(409, {"error": "RDB ou SCD nao carregado"})
                    return
                entry = rdb_loader.find_gle(rdb, relay, gle)
                if entry is None or not entry.fs_path.is_file():
                    self._send_json(404, {"error": f"GLE nao encontrado: {relay}/{gle}"})
                    return
                try:
                    if direction == "scd-to-gle":
                        # The source RDB lives in the content cache, which
                        # is shared; the derived output belongs to this
                        # session (and /download only serves its dirs).
                        out_path = self.sdir("out") / _with_suffix_before_ext(
                            Path(rdb.display_name), "_comments_updated",
                        ).name
                        result = update_rdb_with_scd_descs(
                            rdb_path=rdb.rdb_path,
                            extract_dir=rdb.extract_dir,
                            relay_name=relay,
                            gle_name=gle,
                            gle_fs_path=entry.fs_path,
                            scd_path=scd_path,
                            ied_name=ied,
                            output_path=out_path,
                            job=self.job(),
                        )
                    else:  # gle-to-scd
                        # The source SCD can now also come from the project
                        # library (shared with the other tools in the same
                        # session); the derived output belongs to this tool
                        # (and /download only serves its directories).
                        out_path = self.sdir("out") / _with_suffix_before_ext(
                            scd_label, "_comments_updated",
                        ).name
                        result = update_scd_with_gle_comments(
                            scd_path=scd_path,
                            ied_name=ied,
                            gle_path=entry.fs_path,
                            output_path=out_path,
                        )
                except Exception as e:
                    logger.exception("falha aplicando %s: %s", direction, e)
                    self._send_json(500, {"error": str(e)})
                    return
                if result.get("ok"):
                    out_abs = Path(result["output_path"]).resolve()
                    # A corrected RDB or SCD is input for the other tools,
                    # so it enters the project library instead of dying in
                    # this tab's download link.
                    result["project_file"] = self.publish_output(
                        out_abs, "VB Updater", job=self.job(), logger=logger)
                    result["download_url"] = self.mount_prefix + "/download?file=" + quote(str(out_abs), safe="")
                    result["output_name"] = out_abs.name
                    logger.info("[vb-updater] apply %s -> %s", direction, out_abs.name)
                    self._send_json(200, result)
                else:
                    self._send_json(422, result)
                return

            if path == "/apply-batch":
                body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(body or b"{}")
                    selections = payload.get("selections", [])
                except (json.JSONDecodeError, TypeError):
                    self._send_json(400, {"error": "bad request"})
                    return
                if not isinstance(selections, list) or not selections:
                    self._send_json(400, {"error": "selections vazia"})
                    return
                with self.session.lock:
                    st = self.sess()
                    rdb = st.rdb
                    scd_path = st.scd_path
                if rdb is None or scd_path is None:
                    self._send_json(409, {"error": "RDB ou SCD nao carregado"})
                    return

                resolved: list[dict] = []
                invalid: list[dict] = []
                for sel in selections:
                    if not isinstance(sel, dict):
                        invalid.append({"error": "selecao invalida", "raw": str(sel)})
                        continue
                    relay = str(sel.get("relay", "")).strip()
                    ied = str(sel.get("ied", "")).strip()
                    gle = str(sel.get("gle", "")).strip()
                    if not (relay and ied and gle):
                        invalid.append({
                            "relay": relay, "ied": ied, "gle": gle,
                            "error": "campo obrigatorio ausente",
                        })
                        continue
                    entry = rdb_loader.find_gle(rdb, relay, gle)
                    if entry is None or not entry.fs_path.is_file():
                        invalid.append({
                            "relay": relay, "ied": ied, "gle": gle,
                            "error": "GLE nao encontrado",
                        })
                        continue
                    resolved.append({
                        "relay": relay, "ied": ied,
                        "gle_name": gle, "gle_fs_path": entry.fs_path,
                    })

                if not resolved:
                    self._send_json(422, {
                        "error": "nenhuma selecao valida",
                        "invalid": invalid,
                    })
                    return

                out_path = self.sdir("out") / _with_suffix_before_ext(
                    Path(rdb.display_name), "_batch_comments_updated",
                ).name
                try:
                    result = update_rdb_with_scd_descs_batch(
                        rdb_path=rdb.rdb_path,
                        extract_dir=rdb.extract_dir,
                        scd_path=scd_path,
                        selections=resolved,
                        output_path=out_path,
                        job=self.job(),
                    )
                except Exception as e:
                    logger.exception("falha aplicando batch: %s", e)
                    self._send_json(500, {"error": str(e)})
                    return

                if not result.get("ok"):
                    # All-or-nothing: no selection was written, so there is
                    # no file to download nor to publish into the library.
                    # The per-selection `results` go along so the screen can
                    # say WHICH one failed.
                    if invalid:
                        result["invalid"] = invalid
                    logger.warning("[vb-updater] apply-batch abortado: %s",
                                   result.get("error"))
                    self._send_json(422, result)
                    return

                out_abs = Path(result["output_path"]).resolve()
                result["project_file"] = self.publish_output(
                    out_abs, "VB Updater", job=self.job(), logger=logger)
                result["download_url"] = self.mount_prefix + "/download?file=" + quote(str(out_abs), safe="")
                result["output_name"] = out_abs.name
                if invalid:
                    result["invalid"] = invalid
                logger.info(
                    "[vb-updater] apply-batch (%d ok, %d fail) -> %s",
                    result.get("succeeded", 0), result.get("failed", 0),
                    out_abs.name,
                )
                self._send_json(200, result)
                return

            if path == "/export-descriptions":
                body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(body or b"{}")
                    selections = payload.get("selections", [])
                except (json.JSONDecodeError, TypeError):
                    self._send_json(400, {"error": "bad request"})
                    return
                if not isinstance(selections, list) or not selections:
                    self._send_json(400, {"error": "selections vazia"})
                    return
                with self.session.lock:
                    st = self.sess()
                    scd_path = st.scd_path
                    scd_label = Path(st.scd_name or (scd_path.name if scd_path
                                                     else "arquivo.scd"))
                if scd_path is None:
                    self._send_json(409, {"error": "SCD nao carregado"})
                    return
                # Collect IEDs preserving order and deduplicating. Also
                # maps IED -> relay name (RDB) to show on the sheet.
                ied_names: list[str] = []
                seen: set[str] = set()
                rdb_by_ied: dict[str, str] = {}
                for sel in selections:
                    if not isinstance(sel, dict):
                        continue
                    ied = str(sel.get("ied", "")).strip()
                    relay = str(sel.get("relay", "")).strip()
                    if not ied or ied in seen:
                        continue
                    seen.add(ied)
                    ied_names.append(ied)
                    if relay:
                        rdb_by_ied[ied] = relay
                if not ied_names:
                    self._send_json(400, {"error": "nenhum IED valido na selecao"})
                    return
                try:
                    xlsx_bytes = build_vb_descriptions_xlsx(
                        scd_path=scd_path, ied_names=ied_names,
                        rdb_by_ied=rdb_by_ied,
                    )
                except Exception as e:
                    logger.exception("falha gerando xlsx: %s", e)
                    self._send_json(500, {"error": str(e)})
                    return
                # Saves into the session's SCD dir (the /download sandbox).
                out_name = _with_suffix_before_ext(
                    scd_label, "_descriptions",
                ).with_suffix(".xlsx").name
                out_path = (self.sdir("scd") / out_name).resolve()
                try:
                    out_path.write_bytes(xlsx_bytes)
                except OSError as e:
                    self._send_json(500, {"error": f"falha ao salvar xlsx: {e}"})
                    return
                logger.info(
                    "[vb-updater] export-descriptions: %d IEDs -> %s",
                    len(ied_names), out_name,
                )
                self._send_json(200, {
                    "ok": True,
                    "output_name": out_name,
                    "project_file": self.publish_output(
                        out_path, "VB Updater", job=self.job(), logger=logger),
                    "download_url": self.mount_prefix + "/download?file=" + quote(str(out_path), safe=""),
                    "ied_count": len(ied_names),
                })
                return

            if path == "/import-descriptions":
                if length <= 0:
                    self._send_json(400, {"error": "empty upload"})
                    return
                if length > _SCD_MAX_BYTES:
                    self._send_json(413, {"error": "arquivo grande demais"})
                    return
                with self.session.lock:
                    st = self.sess()
                    scd_path = st.scd_path
                    scd_label = Path(st.scd_name or (scd_path.name if scd_path
                                                     else "arquivo.scd"))
                if scd_path is None:
                    self._send_json(409, {"error": "SCD nao carregado"})
                    return
                # Selected IEDs (optional filter): accepts the header
                # X-Selected-Ieds with a JSON list (URL-encoded). JSON and
                # not "a,b" because the client's encodeURIComponent escapes a
                # literal comma inside a name exactly like the separator:
                # after the unquote the two are indistinguishable and an IED
                # called "A,B" would become two.
                raw_sel = self.headers.get("X-Selected-Ieds", "")
                selected_ieds: set[str] | None = None
                if raw_sel:
                    try:
                        parsed_sel = json.loads(unquote(raw_sel))
                        if isinstance(parsed_sel, list):
                            selected_ieds = {
                                s.strip() for s in parsed_sel
                                if isinstance(s, str) and s.strip()
                            }
                    except Exception:
                        selected_ieds = None
                xlsx_bytes = self.rfile.read(length)
                try:
                    parsed = parse_vb_descriptions_xlsx(xlsx_bytes)
                except Exception as e:
                    logger.exception("falha parseando xlsx: %s", e)
                    self._send_json(422, {"error": f"xlsx invalido: {e}"})
                    return
                if not parsed:
                    self._send_json(422, {
                        "error": "nenhuma descricao valida encontrada no xlsx "
                                 "(verifique que A1='IED:' e B1=<nome do IED>)",
                    })
                    return
                # Filter by the selected ones, if any.
                ignored: list[str] = []
                if selected_ieds is not None:
                    filtered = {}
                    for ied, vb_map in parsed.items():
                        if ied in selected_ieds:
                            filtered[ied] = vb_map
                        else:
                            ignored.append(ied)
                    parsed = filtered
                if not parsed:
                    self._send_json(422, {
                        "error": "nenhum IED do xlsx esta na selecao atual",
                        "ignored_ieds": ignored,
                    })
                    return
                out_path = self.sdir("out") / _with_suffix_before_ext(
                    scd_label, "_descriptions_imported").name
                try:
                    result = update_scd_with_descriptions_multi(
                        scd_path=scd_path,
                        descriptions_by_ied=parsed,
                        output_path=out_path,
                    )
                except Exception as e:
                    logger.exception("falha aplicando descricoes: %s", e)
                    self._send_json(500, {"error": str(e)})
                    return
                out_abs = Path(result["output_path"]).resolve()
                result["project_file"] = self.publish_output(
                    out_abs, "VB Updater", job=self.job(), logger=logger)
                result["download_url"] = self.mount_prefix + "/download?file=" + quote(str(out_abs), safe="")
                result["output_name"] = out_abs.name
                if ignored:
                    result["ignored_ieds"] = ignored
                logger.info(
                    "[vb-updater] import-descriptions: %d IEDs aplicados -> %s",
                    len(parsed), out_abs.name,
                )
                self._send_json(200, result)
                return

            if path in ("/select-rdb", "/select-scd"):
                # The body of the old /rdb-upload or /scd-upload, from
                # `process_upload`/`load_scd` onward: the file was already
                # received and extracted/validated in /files/.
                want = (filelib.KIND_RDB if path == "/select-rdb"
                        else filelib.KIND_SCD)
                body = self._read_json_body()
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != want:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                job = self.job()
                with self.session.lock:
                    st = self.sess()
                    if want == filelib.KIND_RDB:
                        st.rdb = entry.rdb
                        logger.info("[vb-updater] RDB '%s' (%s) escolhido; "
                                    "%d relé(s) com GLE",
                                    entry.display_name, entry.short_sha,
                                    len(entry.rdb.relays))
                    else:
                        st.scd_path = entry.scd_path
                        st.scd_name = entry.display_name
                        logger.info("[vb-updater] SCD '%s' (%s) escolhido",
                                    entry.display_name, entry.short_sha)
                    # The RDB x SCD cross-match only happens when both
                    # exist; `_maybe_match` already knows that.
                    job.stage("Cruzando RDB com SCD", 60)
                    _maybe_match(st)
                job.finish("Arquivo carregado")
                self._send_json(200, _state_payload(self.sess()))
                return

            self._send(404, "not found", "text/plain")

    return Handler
