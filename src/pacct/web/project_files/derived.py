"""What a tool generated, entering the visitor's project.

Until now an output was a dead end: the VB Updater wrote a corrected SCD, the
DNP Map Editor wrote a corrected RDB, and the only way to feed either into the
next tool was to download it and upload it again -- a 140 MB round trip through
the substation's network to move a file between two tabs of the same server.

`adopt()` is the other half of `handler.py:_do_upload`. It takes the file a
tool just wrote and puts it in the same library an upload lands in, by the same
rules: keyed by the sha256 of the content, RDB extracted into the shared
content cache, SCD copied into the session's `files/`. From there every
picker sees it, because a picker only ever asks `/library`.

Three kinds go in. RDB and SCD are project inputs and become selectable. XLSX
is NOT selectable by anything -- no tool asks `/library?kind=xlsx` -- and is
kept only so the spreadsheets a tool produced can be found and downloaded later
from one place instead of from the tab that happened to produce them.

Adoption must never break the export that triggered it: everything here is
best-effort and reports failure as a string. A tool that could not put its
output in the project still wrote the output, and its own download link still
works.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from sellib import rdb as rdb_loader
from sellib.scl import read as scd_loader

from pacct.web.project_files import library

# The output-only kind. Deliberately absent from `library.kind_for`, which is
# what `_do_upload` validates against: a spreadsheet is not something a visitor
# uploads INTO the project, it is something the project produced.
KIND_XLSX = library.KIND_XLSX


def kind_for_output(filename: str) -> str | None:
    """The kind of a generated file, which is `kind_for` plus `.xlsx`."""
    kind = library.kind_for(filename)
    if kind is not None:
        return kind
    return KIND_XLSX if Path(filename or "").suffix.lower() == ".xlsx" else None


def adopt(sessions, session, path, *, origin: str,
          logger: logging.Logger | None = None,
          job=None) -> tuple[library.FileEntry | None, bool, str]:
    """Put the file at `path` into this visitor's project.

    Returns `(entry, already_there, error)`. `entry` is None only when the
    file could not be adopted, and then `error` says why -- the caller logs it
    and carries on. `origin` is the tool's name -- a chip in the listing and a
    suffix in every picker's line, so a generated file is never mistaken for
    something the visitor sent.

    The RDB path goes through `rdb_loader.process_upload`, exactly like an
    upload: the derived bytes are new content, so they get their own entry in
    `cache/rdb/<sha256>/` and their own extraction. That extraction is what
    makes the file usable by another tool at all -- without it there is nothing
    for `RdbInfo` to hand over -- and it is the same work the visitor would
    have paid for by re-uploading the file by hand.
    """
    src = Path(path)
    name = src.name
    kind = kind_for_output(name)
    if kind is None:
        return None, False, f"tipo não guardado no projeto: {name}"
    # Hashed and copied from DISK, never loaded whole. `process_upload`'s
    # docstring says it exists for callers that ALREADY hold the bytes; this
    # one holds a path, and an exported RDB is 40-140 MB -- reading it in only
    # to hand it to a `BytesIO` kept that size resident twice over, on the
    # same threaded server that may be serving another download.
    try:
        size = src.stat().st_size
        sha = rdb_loader.sha256_file(src)
    except OSError as e:
        return None, False, f"não foi possível ler {name}: {e}"
    if not size:
        return None, False, f"{name} está vazio"
    lib = library.library_for(sessions, session)
    with session.lock:
        existing = lib.get(sha)
    if existing is not None:
        # Same bytes already in the project -- typically the same export run
        # twice with nothing changed in between. Nothing to extract, nothing
        # to copy, and the FIRST name stays (see `FileLibrary.add`).
        return existing, True, ""

    if job is not None:
        job.stage("Adicionando ao projeto", 96)

    try:
        if kind == library.KIND_RDB:
            entry = _rdb_entry(src, size, name, sha, origin)
        elif kind == library.KIND_SCD:
            entry = _scd_entry(src, size, name, sha, origin, session)
        else:
            entry = _plain_entry(src, size, name, sha, origin, session, kind)
    except Exception as e:                       # noqa: BLE001 - best effort
        if logger is not None:
            logger.warning("[files] '%s' não entrou no projeto: %s", name, e)
        return None, False, str(e)

    with session.lock:
        entry, duplicate = lib.add(entry)
    if logger is not None:
        logger.info("[files] %s '%s' (%s) gerado por %s entrou no projeto",
                    entry.kind.upper(), entry.display_name, entry.short_sha,
                    origin)
    return entry, duplicate, ""


def _copy_atomic(src: Path, dst: Path) -> None:
    """Copy `src` onto `dst` so a reader never sees a half-written file.

    Same temp-then-`os.replace` the RDB writer and the SCD upload use. It
    matters here for the same reason it matters there: the file is named after
    its own sha256, so a truncated one looks exactly like the real one to
    every later lookup.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                    prefix=f".{dst.name}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _rdb_entry(src: Path, size: int, name: str, sha: str, origin: str):
    with open(src, "rb") as fh:
        info = rdb_loader.process_upload_stream(fh, size, name)
    # The name to SHOW, over the one `sellib` sanitized. It goes on the
    # `RdbInfo` and not only on the entry because five screens read it from
    # there (`glv/handler.py:184`, `settings_compare:131`, `vb_updater:1113`,
    # `gle_exporter:844`, `dnp_map/handler.py:649`) -- and the field is
    # documented as "the name THIS upload carried", which is exactly what
    # this is. What `sellib` keeps is the cache's own record in
    # `meta.json`, which no screen reads.
    info.display_name = library.display_name_for(name, "arquivo.rdb")
    return library.FileEntry(
        sha256=sha, kind=library.KIND_RDB, display_name=info.display_name,
        size=size, detail=f"{len(info.relays)} IED(s)",
        rdb=info, origin=origin,
    )


def _scd_entry(src: Path, size: int, name: str, sha: str, origin: str, session):
    target = library.path_for(library.files_dir(session), sha, ".scd")
    _copy_atomic(src, target)
    try:
        ieds = scd_loader.load_scd(target)
    except Exception:
        # A generated SCD that does not parse is this server's bug, not the
        # visitor's, and hiding the file would hide the evidence. It enters
        # the project without a count; whichever tool selects it will fail
        # loudly and say why.
        ieds = []
    base = f"{len(ieds)} IED(s)" if ieds else ""
    return library.FileEntry(
        sha256=sha, kind=library.KIND_SCD,
        display_name=library.display_name_for(name, "arquivo.scd"),
        size=size, detail=base, path=target,
        origin=origin,
    )


def _plain_entry(src: Path, size: int, name: str, sha: str, origin: str,
                 session, kind: str):
    suffix = Path(name).suffix.lower() or ".bin"
    target = library.path_for(library.files_dir(session), sha, suffix)
    _copy_atomic(src, target)
    return library.FileEntry(
        sha256=sha, kind=kind,
        display_name=library.display_name_for(name, f"arquivo{suffix}"),
        size=size, detail="", path=target, origin=origin,
    )
