"""What one visitor has staged, per (RDB, relay, GLE).

Separate from `model.py` because that module is pure `bytes -> bytes` and is
the piece that would lift into `sellib` if a second tool ever needed it. This
one knows about sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sellib.rdb import RdbInfo

from pacct.web.gle_tabs import model

# (short sha of the RDB, relay name, GLE filename)
EditKey = tuple[str, str, str]


@dataclass
class PageEdit:
    """One GLE's pending change. `names` holds only names that really differ."""

    order: list[int]
    names: dict[int, str]

    @property
    def is_noop(self) -> bool:
        return not self.names and self.order == sorted(self.order)


@dataclass
class GleTabsState:
    """What one visitor has open and has changed."""

    # short sha -> RdbInfo, as in dnp_map and settings_compare. A cache, not a
    # gate: `SessionHandler.library_entry` is what decides whether a file is
    # the visitor's to open.
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    edits: dict[EditKey, PageEdit] = field(default_factory=dict)


def stage_edit(st: GleTabsState, key: EditKey, spans: list[model.PageSpan],
               *, order: list[int], names: dict[int, str]) -> PageEdit | None:
    """Validate and hold one GLE's edit. Returns None when it changes nothing.

    Raises `model.GleTabsError` without touching `st` if the edit is refused.

    A rename to the name a page already has is dropped rather than stored, so
    an edit that says nothing never reaches `Gerar` and never rewrites a
    stream. Dragging a tab out and back therefore leaves the GLE unmarked.
    """
    model.validate_edit(spans, order=order, names=names)
    real = {i: n for i, n in names.items() if n != spans[i].name}
    edit = PageEdit(order=list(order), names=real)
    if edit.is_noop:
        st.edits.pop(key, None)
        return None
    st.edits[key] = edit
    return edit


def dirty_count(st: GleTabsState, rdb_key: str, lock) -> int:
    """How many of this RDB's GLEs are carrying a staged edit."""
    with lock:
        return sum(1 for (rdb, _, _) in st.edits if rdb == rdb_key)
