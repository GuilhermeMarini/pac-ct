"""Arquivos do Projeto (`/files/`): the screen over `pacct.library`.

    handler.py   the /files/ routes
    templates/   library.html

An ordinary tool with no special standing, and that is the point of the split:
the mount table imports this package and nothing else does. Everything the
other seven tools, the dispatcher and the session layer actually depend on
moved to `pacct.library` -- the model, the way a tool's output enters the same
library, and the runtime tag injected into every page.

What stays here is one screen's worth of HTTP: the only drop zone in the
application. Before it each tool had its own upload panel, so the same 40-140
MB RDB was transferred once per tool and two uploads of the same SCD were two
files. The library is one per session now, and the tools choose from within it.
"""

from __future__ import annotations

from pacct.paths import FILES_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (FILES_TEMPLATES_DIR / name).read_text(encoding="utf-8")
