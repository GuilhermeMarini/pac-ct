"""The RDB and the SCD this visitor has open, the match between them, and the
payload the routes answer with.

Separate from `model.py` on the seam `gle_exporter`, `gle_tabs` and
`settings_compare` already use: that module is a file in and facts out, and
would lift out of this repository unchanged. This one is per visitor and shapes
a JSON response.

Four fields rather than `gle_exporter`'s one, because this tool needs both files
at once and a report about the pair. `scd_name` sits beside `scd_path` because
the library stores an SCD as `<sha12>.scd`: three routes build an output name
from the name the user knows rather than from the file on disk, after deriving
it from the path once handed someone `72586aeda11e_descriptions.xlsx`.

Session state is per user, so there is no module-level singleton here: the state
is a parameter, and `SessionHandler.state_factory` is what mints one per
visitor. `maybe_match` mutates it and must be called under the session lock; the
routes that do are in `handler.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sellib import match as matcher
from sellib.rdb import RdbInfo

_logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    rdb: RdbInfo | None = None
    scd_path: Path | None = None
    scd_name: str | None = None
    match_report: matcher.MatchReport | None = None


def maybe_match(st: SessionState) -> None:
    """Run the cross-match if RDB and SCD exist. Call under the session lock."""
    if st.rdb is None or st.scd_path is None:
        st.match_report = None
        return
    extract_dir = st.rdb.extract_dir
    try:
        st.match_report = matcher.compare_relays_to_scd(
            st.rdb.relays, extract_dir, st.scd_path,
        )
    except Exception as e:
        _logger.exception("falha no cross-match: %s", e)
        st.match_report = None


def state_payload(st: SessionState) -> dict:
    # The response body: flags, names, lists of match rows and the
    # per-relay GLE map all share it.
    d: dict[str, Any] = {
        "has_rdb": st.rdb is not None,
        "has_scd": st.scd_path is not None,
        "rdb_name": st.rdb.display_name if st.rdb else None,
        "scd_name": st.scd_name,
        "matches": [],
        "unmatched_rdb": [],
        "unmatched_scd": [],
    }
    if st.match_report is not None:
        d["matches"] = [m.to_dict() for m in st.match_report.matched]
        d["unmatched_rdb"] = [u.to_dict() for u in st.match_report.unmatched_rdb]
        d["unmatched_scd"] = [u.to_dict() for u in st.match_report.unmatched_scd]
    # GLEs per relay (to populate each row's <select>)
    if st.rdb is not None:
        gles_by_relay: dict[str, list[str]] = {}
        for r in st.rdb.relays:
            gles_by_relay[r.name] = [g.name for g in r.gles]
        d["gles_by_relay"] = gles_by_relay
    else:
        d["gles_by_relay"] = {}
    return d
