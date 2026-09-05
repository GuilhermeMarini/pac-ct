"""One visitor's project files, keyed by the sha256 of their content.

Two uploads of the same bytes are the same file, so the key is the content and
never the name: `projeto.rdb` from two different substations coexist, and the
same substation sent twice does not.

This module knows nothing about HTTP -- `handler.py` serves the routes. It
also holds no lock of its own: callers hold `Session.lock`, the way every
other tool already guards its own state.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from selfiles.rdb import RdbInfo, short_sha

# The key the library lives under in `Session.data`. It is deliberately the
# SAME for every tool: that is what makes the library one library.
LIBRARY_KEY = "files"

KIND_RDB = "rdb"
KIND_SCD = "scd"
# Output only, never an upload: `kind_for` -- which is what `/upload`
# validates against -- does NOT know `.xlsx`, on purpose. A spreadsheet is not
# a project input; it enters the library only so the files a tool produced can
# be found and downloaded from one place. No picker asks `?kind=xlsx`, so it
# never shows up inside a tool.
KIND_XLSX = "xlsx"

# The single definition of the ceilings. They used to be copied into six tool
# modules, and the copies had already drifted apart.
RDB_MAX_BYTES = 500 * 1024 * 1024
SCD_MAX_BYTES = 200 * 1024 * 1024

_EXTENSIONS = {".rdb": KIND_RDB, ".scd": KIND_SCD, ".xml": KIND_SCD}

# C0 and DEL. CR and LF are the two that matter -- they reach a response
# header -- and the rest are dropped with them because none of them is a
# character in anyone's filename.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def kind_for(filename: str) -> str | None:
    """The file's kind, by extension. None means it is not a project file."""
    return _EXTENSIONS.get(Path(filename or "").suffix.lower())


def max_bytes_for(kind: str) -> int:
    return RDB_MAX_BYTES if kind == KIND_RDB else SCD_MAX_BYTES


def display_name_for(raw: str, fallback: str) -> str:
    """The name to SHOW for a file, which is not the name to store it under.

    A file is stored under its sha256 (`path_for`), so the name it arrived
    with is only ever displayed, logged, or put in a `Content-Disposition`.
    That is why this keeps the accents: `subestação.scd` used to go through
    `selfiles`' `rdb.sanitize_name`, whose allowlist is `[^A-Za-z0-9._\\- ]`,
    and reached every screen as `subesta__o.scd` -- while `/files/download`
    encoded it RFC 5987 *because these names carry accents*, with none left
    to carry. That function is right where it is still used: `dnp_map/export`
    builds a real filesystem path with it.

    What is dropped is what is not part of a name. `X-Filename` carries
    whatever the client put in it, and the name does not stop at the screen:
    the VB Updater derives its output filename from the SCD's
    (`scd_label`). So a directory goes -- backslash included, which is not a
    separator here but is what a Windows browser used to prepend -- and so do
    the control characters, which have no business in a header or a log line.
    `fallback` covers what that leaves empty, `"."` among it: `Path(".").name`
    is empty and `with_name` on it raises.
    """
    name = (raw or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()
    return name if Path(name).name else fallback



def path_for(files_dir: Path, sha256: str, suffix: str = ".scd") -> Path:
    """Where this content lives in the session's `files/`. The name comes from
    the hash, never from the upload: two files called `sub.scd` with different
    content must be able to sit side by side."""
    if not suffix.startswith("."):
        suffix = "." + suffix
    return Path(files_dir) / f"{sha256[:12]}{suffix}"


def scd_path_for(files_dir: Path, sha256: str) -> Path:
    """`path_for` with the SCD suffix. Kept as its own name because that is
    what the upload path reads as."""
    return path_for(files_dir, sha256, ".scd")


@dataclass
class FileEntry:
    """One file in the project."""

    sha256: str
    kind: str
    display_name: str
    size: int
    uploaded_at: float = field(default_factory=time.time)
    # One line for the listing: "12 relés" / "31 IEDs".
    detail: str = ""
    # kind == KIND_RDB: the extraction, already done at upload time.
    rdb: RdbInfo | None = None
    # Everything that is NOT an RDB: the file inside this session's `files/`.
    # An RDB has no path here on purpose -- its bytes live in the shared
    # content cache (`cache/rdb/<sha256>/source.rdb`), never in the session.
    path: Path | None = None
    # Empty for a file the visitor uploaded; the tool's name for one a tool
    # generated (see `derived.adopt`). It is a field of its own and NOT folded
    # into `detail`: `detail` says what is inside the file ("27 IED(s)"),
    # provenance is a different question, and the two are shown differently --
    # a chip in the listing, a suffix in the picker's line.
    origin: str = ""

    @property
    def short_sha(self) -> str:
        return short_sha(self.sha256)

    @property
    def scd_path(self) -> Path | None:
        """The old name of `path`, from when only SCDs had one. Kept because
        five tools read `entry.scd_path`, and an SCD is still the only thing
        any of them wants a path to."""
        return self.path if self.kind == KIND_SCD else None

    @property
    def generated(self) -> bool:
        return bool(self.origin)

    def require_scd_path(self) -> Path:
        """The file on disk, for a caller that has already established this is
        an SCD entry. The mirror of `require_rdb`, and true for the same
        reason: `handler._build_scd_entry` sets `path` to the file it just
        moved into the library, and `derived.adopt` to the one it copied."""
        path = self.scd_path
        if path is None:
            raise ValueError(
                f"entrada '{self.display_name}' ({self.short_sha}) e do tipo "
                f"{self.kind!r} mas nao tem arquivo em disco"
            )
        return path

    def require_rdb(self) -> RdbInfo:
        """The extraction, for a caller that has already established this is
        an RDB entry.

        `rdb` is Optional because the field is, for the SCDs and spreadsheets
        that share this class -- but an entry with `kind == KIND_RDB` always
        carries one: both places that build one (`handler._build_rdb_entry`
        and `derived._rdb_entry`) pass the `RdbInfo` that
        `rdb.process_upload` just returned, and neither can produce the kind
        without it. Four tools read `entry.rdb` straight after checking the
        kind; this is that invariant written down once, where the field is,
        instead of four narrowing branches that can never be taken.
        """
        if self.rdb is None:
            raise ValueError(
                f"entrada '{self.display_name}' ({self.short_sha}) e do tipo "
                f"{self.kind!r} mas nao tem extracao"
            )
        return self.rdb

    def file_path(self) -> Path | None:
        """The bytes on disk, whatever the kind -- what `/download` serves."""
        if self.kind == KIND_RDB:
            return self.rdb.rdb_path if self.rdb is not None else None
        return self.path

    def to_json(self) -> dict:
        """What the browser is allowed to see. Neither the RdbInfo nor the
        on-disk path crosses: a page has no use for either, and the path is
        inside another visitor's sandbox as far as it is concerned."""
        return {
            "sha256": self.sha256,
            "short_sha": self.short_sha,
            "kind": self.kind,
            "name": self.display_name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "detail": self.detail,
            "origin": self.origin,
            "generated": self.generated,
        }


class FileLibrary:
    """A visitor's files, in arrival order, without repeats."""

    def __init__(self) -> None:
        self.entries: dict[str, FileEntry] = {}

    def get(self, sha256: str) -> FileEntry | None:
        return self.entries.get(sha256)

    def list(self, kind: str | None = None) -> list[FileEntry]:
        return [e for e in self.entries.values()
                if kind is None or e.kind == kind]

    def add(self, entry: FileEntry) -> tuple[FileEntry, bool]:
        """Store `entry`; return `(entry, already_there)`.

        Idempotent on purpose, so a caller that hashed, then did slow work
        outside the lock, can add unconditionally. The FIRST upload's name is
        the one that stays: renaming an entry under a tool that is already
        showing it on screen is worse than ignoring the second name.
        """
        existing = self.entries.get(entry.sha256)
        if existing is not None:
            return existing, True
        self.entries[entry.sha256] = entry
        return entry, False

    def remove(self, sha256: str) -> FileEntry | None:
        """Drop the entry; return what left, or None.

        An SCD's file goes with it -- it belongs to this session. An RDB's
        extraction (`cache/rdb/<sha256>/`) does NOT: it has no owner, is
        shared between visitors and across restarts, and is pruned by age in
        `rdb_cache.sweep()`.
        """
        entry = self.entries.pop(sha256, None)
        if entry is not None and entry.path is not None:
            try:
                Path(entry.path).unlink(missing_ok=True)
            except OSError:
                pass
        return entry


def library_for(sessions, session) -> FileLibrary:
    """This visitor's library. A function taking the session, never a
    module-level singleton: a singleton is per PROCESS, and the library is per
    visitor (see the docstring of `pacct/web/session.py`)."""
    return sessions.state(session, LIBRARY_KEY, FileLibrary)


def files_dir(session) -> Path:
    """The library's directory: `cache/sessions/<sid>/files/`.

    `Session.subdir`, and NOT `SessionHandler.sdir` -- the second one prefixes
    the calling tool's `session_key`, which would give every tool a different
    directory and undo the whole point of a shared library.
    """
    return session.subdir("files")
