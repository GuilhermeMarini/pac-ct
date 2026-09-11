"""The three things this tool writes, and the one it reads back.

Four entry points and two mechanisms. `update_rdb_with_scd_descs` and
`update_rdb_with_scd_descs_batch` push the SCD's descriptions into a GLE inside
an RDB, through `rdb_write.write_streams` and nowhere else;
`update_scd_with_gle_comments` and `update_scd_with_descriptions_multi` push
descriptions the other way, into a copy of the SCD. Between the two directions
sits the spreadsheet: `build_vb_descriptions_xlsx` writes one sheet per IED and
`parse_vb_descriptions_xlsx` reads that same sheet back once the user has edited
the Description column.

Both halves of the spreadsheet live here together, for the reason
`gle_exporter/export.py` gives about its own: the markers and the column numbers
below are the format, and splitting them across two modules would be one format
written down twice. The workbook the import route takes back is one this tool
produced -- deliberately not a project input and never behind the library
picker -- which is what the `IED:` marker in A1 exists to say.

Both RDB writers read first and write once. Every stream is resolved and its new
bytes computed before `write_streams` is called, and the batch answers
`ok: False` with no file written at all if any one selection failed: a partial
apply is a failed apply, and a half-applied RDB in the project library is
indistinguishable from a whole one.

`update_scd_with_descriptions_multi` is the one write here that is not atomic,
and that is inherited rather than chosen -- see `docs/BACKLOG.md`. It was moved
across this split exactly as it was.
"""

from __future__ import annotations

from pathlib import Path

from py61850.scl import SclDocument

from pacct.paths import atomic_write_bytes
from pacct.web import rdb_write
from pacct.web.rdb_write import resolve_gle_stream_path
from pacct.web.vb_updater.model import (
    MESSAGE_QUALITY_LABEL,
    RESERVA_LABEL,
    VB_NUMERIC_RE,
    extract_vb_instances_from_gle,
    new_comments_from_scd,
    substitute_vb_comments_in_gle_bytes,
    update_scd_extrefs_for_ied,
    vb_rows_from_doc,
)
from pacct.web.xlsx_names import sanitize_sheet_name as _sanitize_sheet_name


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


def _apply_stats(sub_stats: dict, with_desc: int, reserva: int, quality: int,
                 original_size: int) -> dict:
    return {
        "instances_updated": sub_stats["updated"],
        "vbs_not_found_in_gle": sub_stats["skipped"],
        "vbs_in_gle_not_in_scd": sub_stats["untouched"],
        "vbs_in_scd_with_desc": with_desc,
        "vbs_in_scd_renamed_to_reserva": reserva,
        "reserva_label": RESERVA_LABEL,
        # How many of this IED's VBs carry a GOOSE subscription's health.
        # Overlaps `vbs_in_scd_with_desc` on purpose: the ones that already
        # have a description keep it, and are still message quality.
        "vbs_message_quality": quality,
        "message_quality_label": MESSAGE_QUALITY_LABEL,
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
    new_comments, with_desc, reserva, quality = new_comments_from_scd(
        scd_path, ied_name)
    if not new_comments:
        return {
            "ok": False,
            "error": f"SCD nao tem nenhum ExtRef VBnnn para o IED {ied_name!r}.",
        }

    original = Path(gle_fs_path).read_bytes()
    updated, sub_stats = substitute_vb_comments_in_gle_bytes(original, new_comments)
    stream_parts = _gle_stream_path(extract_dir, relay_name, gle_name, gle_fs_path)

    try:
        method = rdb_write.write_streams(
            rdb_path, output_path, {tuple(stream_parts): updated}, job=job)
    except rdb_write.RdbWriteError as e:
        return {"ok": False, "error": str(e),
                "stats": _apply_stats(sub_stats, with_desc, reserva, quality,
                                      len(original))}

    return {
        "ok": True,
        "output_path": str(output_path),
        "method": method,
        "stream": "/".join(stream_parts),
        "stats": _apply_stats(sub_stats, with_desc, reserva, quality,
                              len(original)),
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

        new_comments, with_desc, reserva, quality = new_comments_from_scd(
            scd_path, ied)
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
        updated, sub_stats = substitute_vb_comments_in_gle_bytes(
            original, new_comments)
        streams[stream_parts] = updated
        entry_result["ok"] = True
        entry_result["stats"] = _apply_stats(sub_stats, with_desc, reserva,
                                             quality, len(original))
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
    updated, stats = update_scd_extrefs_for_ied(scd_bytes, ied_name, new_descs)
    if stats["updated"] + stats["inserted"] == 0:
        return {
            "ok": False,
            "error": f"IED {ied_name!r} nao encontrado no SCD ou sem ExtRef VBnnn.",
        }

    # Atomic, like every RDB write in this project: the corrected SCD is
    # adopted into the project library right after, and a truncated one there
    # is indistinguishable from a whole one.
    atomic_write_bytes(output_path, updated)

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
    # A fresh `Workbook()` always has one sheet, but `active` is
    # Optional in the stubs -- every sheet here is added by name.
    default = wb.active
    if default is not None:
        wb.remove(default)

    used: set[str] = set()
    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = PatternFill("solid", fgColor="FF1F6FEB")
    meta_font = Font(bold=True)

    # One parse for every sheet. This used to be one `ET.parse` per IED, and
    # a real substation SCD is 22 MB and 27 IEDs -- see `SclDocument`, which
    # exists for exactly this, and which also caches each IED's model so the
    # second sheet of the same device walks nothing. `None` (an unreadable
    # file) gives empty sheets and a log line, the behaviour a per-IED read
    # already had.
    doc = SclDocument.load(scd_path)

    for ied in ied_names:
        rows = [] if doc is None else vb_rows_from_doc(doc, ied, scd_path)
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
                m = VB_NUMERIC_RE.match(vb_str)
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
    `output_path`. Reuses `update_scd_extrefs_for_ied` per IED.

    Returns a dict with per_ied stats and totals.
    """
    raw = scd_path.read_bytes()
    per_ied_stats: dict[str, dict] = {}
    skipped_no_ied: list[str] = []
    totals = {"updated": 0, "inserted": 0, "untouched": 0}

    for ied, vb_map in descriptions_by_ied.items():
        if not vb_map:
            continue
        new_raw, stats = update_scd_extrefs_for_ied(raw, ied, vb_map)
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
