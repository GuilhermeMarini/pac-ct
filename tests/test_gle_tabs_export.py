"""Turning staged tab edits into one RDB.

Two passes on purpose. The first only reads and computes; only if every GLE
succeeds does the second write. A half-applied RDB is indistinguishable from a
whole one once it leaves here -- and it goes straight into the project library.
"""

from __future__ import annotations

from pacct.web.gle_tabs import export, model, state
from tests import gle_fixtures as fx
from tests.web_harness import fake_rdb


def _rdb(tmp_path):
    return fake_rdb(tmp_path, {
        "QPC1_TR1": {"GL1.gle": fx.TABS_GLE, "GL2.gle": fx.TABS_GLE},
    })


def test_it_computes_the_new_bytes_for_every_staged_gle(tmp_path):
    info = _rdb(tmp_path)
    key = info.sha256[:12]
    edits = {
        (key, "QPC1_TR1", "GL1.gle"): state.PageEdit([1, 0, 2, 3, 4, 5], {}),
        (key, "QPC1_TR1", "GL2.gle"): state.PageEdit([0, 1, 2, 3, 4, 5],
                                                     {0: "NOVA"}),
    }
    streams, results = export.compute_streams(info, edits)
    assert len(streams) == 2
    assert all(r["ok"] for r in results)
    assert ("Relays", "QPC1_TR1", "GL1.gle") in streams
    # the reorder landed
    moved = model.read_pages(streams[("Relays", "QPC1_TR1", "GL1.gle")])
    assert [p.name for p in moved][:2] == ["Entradas Críticas", "Capa"]


def test_a_reorder_keeps_the_stream_size(tmp_path):
    """This is what lets rdb_write patch in place instead of rebuilding."""
    info = _rdb(tmp_path)
    key = info.sha256[:12]
    edits = {(key, "QPC1_TR1", "GL1.gle"):
             state.PageEdit([5, 4, 3, 2, 1, 0], {})}
    streams, _ = export.compute_streams(info, edits)
    assert len(streams[("Relays", "QPC1_TR1", "GL1.gle")]) == len(fx.TABS_GLE)


def test_a_gle_that_left_the_rdb_fails_the_whole_pass(tmp_path):
    info = _rdb(tmp_path)
    key = info.sha256[:12]
    edits = {(key, "QPC1_TR1", "GL9.gle"): state.PageEdit([0], {})}
    streams, results = export.compute_streams(info, edits)
    assert streams == {}
    assert results[0]["ok"] is False
    assert "GL9" in results[0]["error"]


def test_edits_for_another_rdb_are_ignored(tmp_path):
    """The state holds every RDB the visitor has touched; only this one's
    edits belong in this output."""
    info = _rdb(tmp_path)
    edits = {("outrardb", "QPC1_TR1", "GL1.gle"):
             state.PageEdit([1, 0, 2, 3, 4, 5], {})}
    streams, results = export.compute_streams(info, edits)
    assert streams == {}
    assert results == []
