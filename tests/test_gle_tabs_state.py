"""What the Organizador de Abas holds between a drag and the Gerar button."""

from __future__ import annotations

import threading

import pytest

from pacct.web.gle_tabs import model, state
from tests import gle_fixtures as fx

KEY = ("abc123def456", "QPC1_TR1", "GL1.gle")


def _spans():
    return model.read_pages(fx.TABS_GLE)


def test_a_no_op_edit_is_not_staged():
    """Order unchanged and nothing renamed: staging it would make Gerar
    rewrite a stream it has nothing to say about."""
    st = state.GleTabsState()
    assert state.stage_edit(st, KEY, _spans(),
                            order=list(range(6)), names={}) is None
    assert st.edits == {}


def test_a_reorder_is_staged():
    st = state.GleTabsState()
    edit = state.stage_edit(st, KEY, _spans(),
                            order=[1, 0, 2, 3, 4, 5], names={})
    assert edit is not None
    assert st.edits[KEY].order == [1, 0, 2, 3, 4, 5]


def test_a_rename_to_the_same_name_is_dropped_from_the_edit():
    st = state.GleTabsState()
    edit = state.stage_edit(st, KEY, _spans(), order=list(range(6)),
                            names={0: "Capa", 1: "NOVA"})
    assert edit is not None
    assert edit.names == {1: "NOVA"}      # page 0 already had that name


def test_staging_over_a_previous_edit_replaces_it():
    st = state.GleTabsState()
    state.stage_edit(st, KEY, _spans(), order=[1, 0, 2, 3, 4, 5], names={})
    state.stage_edit(st, KEY, _spans(), order=[0, 1, 2, 3, 4, 5],
                     names={2: "X"})
    assert st.edits[KEY].order == [0, 1, 2, 3, 4, 5]
    assert st.edits[KEY].names == {2: "X"}


def test_staging_back_to_the_original_clears_the_edit():
    """Dragging a tab out and back must leave the GLE unmarked, not marked
    with an edit that does nothing."""
    st = state.GleTabsState()
    state.stage_edit(st, KEY, _spans(), order=[1, 0, 2, 3, 4, 5], names={})
    assert state.stage_edit(st, KEY, _spans(),
                            order=list(range(6)), names={}) is None
    assert KEY not in st.edits


def test_an_invalid_edit_is_refused_and_stages_nothing():
    st = state.GleTabsState()
    with pytest.raises(model.GleTabsError):
        state.stage_edit(st, KEY, _spans(), order=[0, 1], names={})
    assert st.edits == {}


def test_dirty_count_is_per_rdb():
    st = state.GleTabsState()
    lock = threading.Lock()
    state.stage_edit(st, KEY, _spans(), order=[1, 0, 2, 3, 4, 5], names={})
    state.stage_edit(st, ("abc123def456", "QPC1_TR1", "GL2.gle"), _spans(),
                     order=[1, 0, 2, 3, 4, 5], names={})
    state.stage_edit(st, ("999999999999", "OUTRO", "GL1.gle"), _spans(),
                     order=[1, 0, 2, 3, 4, 5], names={})
    assert state.dirty_count(st, "abc123def456", lock) == 2
    assert state.dirty_count(st, "999999999999", lock) == 1
    assert state.dirty_count(st, "naotem", lock) == 0
