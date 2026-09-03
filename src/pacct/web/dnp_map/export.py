"""Write an RDB back out with the pending DNP map edits applied.

Two paths, chosen by one blunt rule -- exact size or rebuild:

1. Every edited stream serializes to exactly the byte count it had. Copy the
   RDB and ``olefile.write_stream`` each one. The output is byte-identical
   outside the streams we touched. Pure rearrangement inside a block always
   lands here, since moving values conserves the byte multiset.
2. Anything changed size. Rebuild the whole container via
   ``cfbwrite``, which verifies its own output.

Both live in ``pacct.web.rdb_write`` now -- the VB Updater and the GLE
Exporter write RDBs too, and there must be exactly one answer to "how".

There is deliberately no third path that pads a settings file with whitespace
until it fits. Nobody here knows what AcSELerator QuickSet tolerates, and a
guess about that would be a guess about a protection relay's settings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from selfiles import dnp_map as set_dnp
from selfiles.rdb import sanitize_name

from pacct.web import rdb_write
from pacct.web.dnp_map.model import apply_edits

_logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    ok: bool
    path: Path | None = None
    method: str = ""            # "in-place" | "rebuild"
    streams: int = 0
    error: str = ""


def _sessions_index(extract_dir: Path) -> dict[str, dict[str, set_dnp.DnpSession]]:
    return {
        relay.name: {s.name: s for s in relay.sessions}
        for relay in set_dnp.discover(extract_dir)
    }


def build_streams(extract_dir: Path,
                  edits: dict) -> dict[tuple[str, ...], bytes]:
    """The new bytes of every edited session, keyed by its path in the OLE."""
    index = _sessions_index(Path(extract_dir))
    out: dict[tuple[str, ...], bytes] = {}
    for relay, by_session in sorted(edits.items()):
        for session, changes in sorted(by_session.items()):
            if not changes:
                continue
            found = index.get(relay, {}).get(session)
            if found is None:
                _logger.warning("[dnp-map] session missing from extraction: %s/%s",
                                relay, session)
                continue
            parsed = set_dnp.parse(found.fs_path.read_bytes())
            out[found.stream_parts] = apply_edits(parsed, changes).serialize()
    return out


def export(rdb_path: Path, extract_dir: Path, edits: dict, out_path: Path,
           job=None) -> ExportResult:
    """Produce ``out_path``: the RDB with every pending edit applied.

    Everything below is one try/except. This writes a protection relay's
    settings file, so an exception escaping this function is worse than one
    caught here: it used to leave ``build_streams`` and ``OleFileIO(...)``
    outside any error boundary at all, which meant an HTTP request that
    never answered (the client's progress bar spinning forever) instead of
    an ``ExportResult(ok=False)`` -- reachable today from a non-Latin-1 or
    control-character value that reached ``apply_edits`` some other way than
    ``handler.py``'s own validation, or from the source RDB going missing or
    corrupt between upload and export.
    """
    rdb_path, out_path = Path(rdb_path), Path(out_path)
    try:
        streams = build_streams(extract_dir, edits)
        if not streams:
            return ExportResult(
                ok=False, error="Nenhuma alteração pendente para exportar.")

        if job:
            job.stage("Conferindo os mapas alterados", 10)

        method = rdb_write.write_streams(rdb_path, out_path, streams, job=job)
    except rdb_write.RdbWriteError as e:
        return ExportResult(ok=False, error=str(e))
    except Exception as e:                       # anything build_streams raises
        _logger.exception("[dnp-map] export failed")
        return ExportResult(ok=False, error=f"Falha ao gravar o RDB: {e}")

    if job:
        job.stage("Verificando", 95)
    return ExportResult(ok=True, path=out_path, method=method,
                        streams=len(streams))


def export_txt(extract_dir: Path, edits: dict, out_dir: Path) -> list[Path]:
    """Write the edited SET_D files on their own.

    The plan B: if QuickSet ever refuses a rebuilt RDB, these still import.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = _sessions_index(Path(extract_dir))
    written: list[Path] = []
    for relay, by_session in sorted(edits.items()):
        for session, changes in sorted(by_session.items()):
            found = index.get(relay, {}).get(session)
            if found is None or not changes:
                continue
            parsed = set_dnp.parse(found.fs_path.read_bytes())
            # `relay` comes verbatim from the RDB's OLE storage tree, which
            # olefile preserves character-for-character -- CR/LF included.
            # Run the composed name through the same sanitizer the RDB
            # upload path already trusts, so an untrusted RDB can't turn
            # this into a header-injection primitive when the name later
            # reaches `Content-Disposition` (see `handler.py:_serve_download`),
            # nor a raw path separator that would make `write_bytes` raise
            # after the RDB itself has already been written.
            target = out_dir / sanitize_name(f"{relay}_{found.fs_path.name}")
            target.write_bytes(apply_edits(parsed, changes).serialize())
            written.append(target)
    return written
