"""DNP Map Editor: edits an RDB's SET_D<n> files and re-exports the file.

    model.py    per-session edit state (diffs, not documents)
    export.py   the hybrid export: write_stream when it fits, rebuild when not
    handler.py  the routes
    templates/  landing.html and editor.html

The SET_D parser lives in `selfiles.dnp_map` and the Compound File writer in
`cfbwrite`: neither one knows the web exists.
"""

from __future__ import annotations

from pacct.paths import DNP_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one editor template. Read at import time, like in the GLV."""
    return (DNP_TEMPLATES_DIR / name).read_text(encoding="utf-8")
