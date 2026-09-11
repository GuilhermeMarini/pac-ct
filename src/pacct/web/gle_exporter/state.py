"""The one RDB a visitor has open, and the payload the routes answer with.

Separate from `model.py` on the same grounds `gle_tabs` and `settings_compare`
separate their two halves: that module is a `.gle` in and ports out, and would
lift out of this repository unchanged. This one is per visitor and shapes a
JSON response -- `state_payload` reads `RdbInfo.display_name`, which is the
name to SHOW and is a decision of this application's, not of the format.

Session state is per user, so there is no module-level singleton here: the
state is a parameter, and `SessionHandler.state_factory` is what mints one per
visitor. The routes that call these are in `handler.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sellib.rdb import RdbInfo


@dataclass
class SessionState:
    rdb: RdbInfo | None = None


def state_payload(st: SessionState) -> dict:
    if st.rdb is None:
        return {
            "has_rdb": False,
            "rdb_name": None,
            "relays": [],
        }
    return {
        "has_rdb": True,
        "rdb_name": st.rdb.display_name,
        "relays": [
            {
                "name": r.name,
                "model": r.model,
                "ip": r.ip,
                "gles": [{"name": g.name, "filename": g.filename}
                         for g in r.gles],
            }
            for r in st.rdb.relays
        ],
    }
