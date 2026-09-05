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
from pathlib import Path

from selfiles import rdb as rdb_loader
from selfiles.scl import read as scd_loader

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
    try:
        data = src.read_bytes()
    except OSError as e:
        return None, False, f"não foi possível ler {name}: {e}"
    if not data:
        return None, False, f"{name} está vazio"

    sha = rdb_loader.sha256_bytes(data)
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
            entry = _rdb_entry(data, name, sha, origin)
        elif kind == library.KIND_SCD:
            entry = _scd_entry(data, name, sha, origin, session)
        else:
            entry = _plain_entry(data, name, sha, origin, session, kind)
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


def _rdb_entry(data: bytes, name: str, sha: str, origin: str):
    info = rdb_loader.process_upload(data, name)
    # The name to SHOW, over the one `selfiles` sanitized. It goes on the
    # `RdbInfo` and not only on the entry because five screens read it from
    # there (`glv/handler.py:184`, `settings_compare:131`, `vb_updater:1113`,
    # `gle_exporter:844`, `dnp_map/handler.py:649`) -- and the field is
    # documented as "the name THIS upload carried", which is exactly what
    # this is. What `selfiles` keeps is the cache's own record in
    # `meta.json`, which no screen reads.
    info.display_name = library.display_name_for(name, "arquivo.rdb")
    return library.FileEntry(
        sha256=sha, kind=library.KIND_RDB, display_name=info.display_name,
        size=len(data), detail=f"{len(info.relays)} IED(s)",
        rdb=info, origin=origin,
    )


def _scd_entry(data: bytes, name: str, sha: str, origin: str, session):
    target = library.path_for(library.files_dir(session), sha, ".scd")
    target.write_bytes(data)
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
        size=len(data), detail=base, path=target,
        origin=origin,
    )


def _plain_entry(data: bytes, name: str, sha: str, origin: str, session,
                 kind: str):
    suffix = Path(name).suffix.lower() or ".bin"
    target = library.path_for(library.files_dir(session), sha, suffix)
    target.write_bytes(data)
    return library.FileEntry(
        sha256=sha, kind=kind,
        display_name=library.display_name_for(name, f"arquivo{suffix}"),
        size=len(data), detail="", path=target, origin=origin,
    )
