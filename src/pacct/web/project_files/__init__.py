"""Arquivos do Projeto: a visitor's library of RDBs and SCDs.

    library.py   the library itself -- dedup by sha256, knowing no HTTP
    handler.py   the /files/ routes
    client.py    the `SelLibrary` runtime, injected into every page
    templates/   library.html

Before this each tool had its own upload panel: the same 40-140 MB RDB was
transferred once per tool, and two uploads of the same SCD were two files.
Here the library is one per session, and the tools choose from within it.
"""

from __future__ import annotations

from pacct.paths import PROJECT_FILES_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (PROJECT_FILES_TEMPLATES_DIR / name).read_text(encoding="utf-8")
