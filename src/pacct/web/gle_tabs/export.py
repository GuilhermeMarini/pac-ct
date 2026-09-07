"""Staged tab edits -> one output RDB.

Two passes, and the order matters. The first only reads: it resolves each
(relay, GLE) to its stream in the OLE and computes the new bytes, touching no
disk. Only if ALL of them succeed does the second write, in one go, through
`rdb_write.write_streams`.

It is all-or-nothing on purpose, for the reason the GLE Exporter learned:
writing each selection as it became ready meant a later failure left the
earlier ones already in the file, with the response still saying ok, and that
half-applied RDB went into the project library looking exactly like a complete
one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sellib import rdb as rdb_loader
from sellib.rdb import RdbInfo

from pacct.web import rdb_write
from pacct.web.gle_tabs import model, state

_logger = logging.getLogger(__name__)

StreamPath = tuple[str, ...]


def compute_streams(
    rdb_info: RdbInfo, edits: dict[state.EditKey, state.PageEdit],
) -> tuple[dict[StreamPath, bytes], list[dict]]:
    """Pass one: the new bytes of every staged GLE of THIS RDB.

    Returns `({}, results)` if any of them failed -- the caller writes nothing.
    Edits keyed to another RDB are not this output's business and are skipped
    without a result row.
    """
    key = rdb_loader.short_sha(rdb_info.sha256)
    streams: dict[StreamPath, bytes] = {}
    results: list[dict] = []
    totals = {"moved": 0, "renamed": 0}

    for (rdb_key, relay, gle), edit in sorted(edits.items()):
        if rdb_key != key:
            continue
        row: dict = {"relay": relay, "gle": gle}
        entry = rdb_loader.find_gle(rdb_info, relay, gle)
        if entry is None or not entry.fs_path.is_file():
            row["ok"] = False
            row["error"] = f"GLE {gle} não está mais em {relay}"
            results.append(row)
            continue
        parts = rdb_write.resolve_gle_stream_path(rdb_info.extract_dir,
                                                  entry.fs_path)
        if not parts:
            row["ok"] = False
            row["error"] = f"stream não localizado no OLE: {entry.rel_path}"
            results.append(row)
            continue
        # The read comes from the EXTRACTED file, not from the OLE: it is the
        # same content, and this way pass one never opens the RDB.
        try:
            new, stats = model.apply_page_edits(
                entry.fs_path.read_bytes(),
                order=edit.order, names=edit.names)
        except (OSError, model.GleTabsError) as exc:
            row["ok"] = False
            row["error"] = str(exc)
            results.append(row)
            continue
        streams[tuple(parts)] = new
        row["ok"] = True
        row["stats"] = stats
        for k in totals:
            totals[k] += stats[k]
        results.append(row)

    if any(not r["ok"] for r in results):
        return {}, results
    for r in results:
        r["totals"] = totals
    return streams, results


def build_output(rdb_info: RdbInfo, edits: dict[state.EditKey, state.PageEdit],
                 output_path: Path, job=None) -> dict:
    """Apply every staged edit and produce `output_path`.

    With `ok: False`, NO file was written and `output_path` does not exist.
    """
    if job:
        job.stage("Conferindo os GLEs alterados", 10)
    streams, results = compute_streams(rdb_info, edits)
    if not streams:
        return {"ok": False, "results": results,
                "succeeded": 0, "failed": len(results),
                "error": (results[0]["error"] if results
                          else "nenhuma aba alterada para gravar")}

    totals = results[0].get("totals", {"moved": 0, "renamed": 0})
    if job:
        job.stage("Gravando o RDB", 55)
    try:
        method = rdb_write.write_streams(rdb_info.rdb_path, output_path,
                                         streams, job=job)
    except rdb_write.RdbWriteError as exc:
        return {"ok": False, "results": results, "succeeded": 0,
                "failed": len(results), "error": str(exc)}
    _logger.info("[gle-tabs] %s: %d GLE(s), %d aba(s) movida(s), "
                 "%d renomeada(s), metodo=%s",
                 output_path.name, len(streams),
                 totals["moved"], totals["renamed"], method)
    return {"ok": True, "output_path": output_path, "results": results,
            "succeeded": len(results), "failed": 0,
            "totals": totals, "method": method}
