"""
Settings Compare (Comparador de Ajustes): side-by-side diff of SEL relay
settings extracted from RDBs.

User flow:

  1. Pick one or more RDBs (fresh uploads or already extracted in `rdbs/`).
  2. Choose up to 7 relays of the SAME family (3xx/4xx/7xx).
  3. Choose settings groups (tabs: L1, S1, A1, etc.).
  4. Read the diff per tab; each variable shows a per-row verdict:
     EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT.

Shares the visual style with `vb_updater.py` and `vlan_mapper.py`. State per
process (singleton); the user clicks "<- Menu" to go back.

    model.py    the comparison: N relays of one family, variable by variable
    state.py    the per-visitor RDB registry and the payloads it builds
    handler.py  the routes
    templates/  index.html
"""

from __future__ import annotations

from pacct.paths import SETTINGS_COMPARE_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (SETTINGS_COMPARE_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# Re-exports, and they have to come last: `handler` imports `load_template`
# back out of this module, so the name must already be bound when the line
# below runs. The same shape `vlan_mapper` has -- this tool's route tests name
# the package itself (`from pacct.web.settings_compare import
# build_settings_compare_handler`, `settings_compare.INDEX_HTML`), so those two
# stay reachable here.
from pacct.web.settings_compare.handler import (  # noqa: E402
    INDEX_HTML,
    build_settings_compare_handler,
)

__all__ = [
    "INDEX_HTML",
    "build_settings_compare_handler",
    "load_template",
]
