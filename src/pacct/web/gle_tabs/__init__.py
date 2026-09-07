"""Organizador de Abas GLE: reorder and rename a GLE's pages.

Each `<page>` under `<pages>` in `Relays/<relay>/Misc/GL*.gle` is one tab in
AcSELerator QuickSet's Graphical Logic Editor. QuickSet offers no way to move
one, so the tabs of a real project sit in whatever order they were drawn in.

Reordering cannot change what the relay does: the compiled SELOGIC lives in
numbered slots (`SET02`, `LT03`) bound to each element's
`physical_instance_number`, never to a page's position, and a page name appears
in no other stream of the RDB.
"""

from __future__ import annotations

from pacct.paths import GLE_TABS_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (GLE_TABS_TEMPLATES_DIR / name).read_text(encoding="utf-8")
