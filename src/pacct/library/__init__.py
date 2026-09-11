"""The visitor's project files: the platform half of Arquivos do Projeto.

    model.py     the library itself -- dedup by sha256, knowing no HTTP
    derived.py   what a tool GENERATED, entering the same library
    client.py    the `SelLibrary` runtime tag, injected into every page

This is not a tool and it does not live under `web/`. Seven tools reach it for
their picker, `web/mount.py` answers `/library` out of it and `web/session.py`
injects its runtime into every page and adopts every output through it -- a
module the dispatcher and the session layer both depend on is platform, not a
screen. The screen it does have is an ordinary tool with no special standing,
`web/files/`, and that package is imported by the mount table and by nobody
else.

Nothing here imports `pacct.web`, in either direction of the tree:
`tests/test_tool_layering.py` asserts it. That is the whole content of the
split -- the library outlives whichever framework serves it.

The names below are re-exported so a tool writes one import line
(`from pacct import library as filelib`) and then `filelib.KIND_RDB`,
`filelib.library_for(...)`, the way it always did. `derived` and `client` are
NOT re-exported: only `web/session.py` reaches those, it reaches them by
module, and a picker has no business holding either.
"""

from __future__ import annotations

from pacct.library.model import (
    EXTENSIONS,
    KIND_RDB,
    KIND_SCD,
    KIND_XLSX,
    LIBRARY_KEY,
    RDB_MAX_BYTES,
    SCD_MAX_BYTES,
    FileEntry,
    FileLibrary,
    display_name_for,
    files_dir,
    kind_for,
    library_for,
    library_response,
    max_bytes_for,
    path_for,
    scd_path_for,
)

__all__ = [
    "EXTENSIONS",
    "KIND_RDB",
    "KIND_SCD",
    "KIND_XLSX",
    "LIBRARY_KEY",
    "RDB_MAX_BYTES",
    "SCD_MAX_BYTES",
    "FileEntry",
    "FileLibrary",
    "display_name_for",
    "files_dir",
    "kind_for",
    "library_for",
    "library_response",
    "max_bytes_for",
    "path_for",
    "scd_path_for",
]
