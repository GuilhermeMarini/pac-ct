"""VLAN Mapper: from an SCD, list the VLANs each relay (IED) needs enabled
on its switch port.

Output: HTML page with one row per IED, columns =
  IED | tipo/desc | IP | VLANs (chips) | #RX / #TX

Flow:
  1. The user uploads the SCD.
  2. The app parses it and shows the table.
  3. The "<- Menu" button in the header goes home (same convention as the
     VB Updater).

Shares the page conventions with the VB Updater, but is its own tool so that
the session state stays simple (SCD only here, no RDB and no matcher).

    model.py    the IED -> VLANs computation and the payload it answers with
    handler.py  the routes
    templates/  landing.html
"""

from __future__ import annotations

from pacct.paths import VLAN_MAPPER_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (VLAN_MAPPER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# Re-exports, and they have to come last: `handler` imports `load_template`
# back out of this module, so the name must already be bound when the line
# below runs. `dnp_map` and `gle_tabs` re-export nothing and their tests reach
# into `.handler` directly; this tool's route tests name the package itself
# (`from pacct.web.vlan_mapper import build_vlan_mapper_handler`,
# `vlan_mapper.LANDING_HTML`) and `tests/test_scd.py` names
# `compute_ied_vlan_rows`, so those three stay reachable here.
from pacct.web.vlan_mapper.handler import (  # noqa: E402
    LANDING_HTML,
    build_vlan_mapper_handler,
)
from pacct.web.vlan_mapper.model import compute_ied_vlan_rows  # noqa: E402

__all__ = [
    "LANDING_HTML",
    "build_vlan_mapper_handler",
    "compute_ied_vlan_rows",
    "load_template",
]
