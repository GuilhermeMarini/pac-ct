"""
GLE Variable Comment Exporter: extracts the list of SYMBOL instances of each
GLE in an RDB and exports it as Excel for bulk editing. The user edits the
port (input/output) comments and reimports the Excel to produce an updated
RDB.

Flow:
  1. The user uploads an RDB.
  2. The app lists the RDB's relays with a per-relay GLE selection.
  3. The "Exportar Excel" button builds an xlsx with one sheet per selected
     (relay, GLE), holding every SYMBOL instance of that GLE.
  4. The user edits the "Input Comment"/"Output Comment" cells.
  5. The "Importar Excel" button applies the changes to the RDB and
     downloads a new RDB.

Each SYMBOL in the GLE is an "instance" identified by (page, element_id). The
same name (e.g. TMB1A) can appear several times on different pages; each
occurrence is one row in the Excel.

    model.py    a .gle in, its ports out -- and the comments back in, in bytes
    export.py   the xlsx both ways, and the edits applied to the RDB
    state.py    the RDB this visitor has open, and the payload for /state
    handler.py  the routes
    templates/  landing.html
"""

from __future__ import annotations

from pacct.paths import GLE_EXPORTER_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (GLE_EXPORTER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# Re-exports, and they have to come last: `handler` imports `load_template`
# back out of this module, so the name must already be bound when the lines
# below run. The same shape `vlan_mapper` and `settings_compare` have. This
# tool's frozen surface is the widest of the four -- three test files name the
# PACKAGE rather than a module inside it: `tests/test_web_routes_gle_exporter.py`
# wants `build_gle_exporter_handler` and `LANDING_HTML`, `tests/test_gle_bytes.py`
# wants `update_port_comments_in_gle_bytes`, and `tests/test_rdb_write.py`
# wants `apply_xlsx_updates_to_rdb`. Those four stay reachable here.
from pacct.web.gle_exporter.export import apply_xlsx_updates_to_rdb  # noqa: E402
from pacct.web.gle_exporter.handler import (  # noqa: E402
    LANDING_HTML,
    build_gle_exporter_handler,
)
from pacct.web.gle_exporter.model import (  # noqa: E402
    update_port_comments_in_gle_bytes,
)

__all__ = [
    "LANDING_HTML",
    "apply_xlsx_updates_to_rdb",
    "build_gle_exporter_handler",
    "load_template",
    "update_port_comments_in_gle_bytes",
]
