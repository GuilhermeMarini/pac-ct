"""The Organizador de Abas GLE's routes.

The tool takes an RDB from the project, stages tab edits per GLE and writes
one updated RDB. What matters at the HTTP layer is that the project's files
are what it offers, that a refused edit stages nothing, and that /download
stays inside this session's own directory.
"""

from __future__ import annotations

from pacct.web.gle_tabs.handler import build_gle_tabs_handler
from tests import gle_fixtures as fx
from tests.web_harness import build, fake_rdb


def _harness(tmp_path):
    h = build(build_gle_tabs_handler, tmp_path)
    info = fake_rdb(tmp_path, {
        "QPC1_TR1": {"GL1.gle": fx.TABS_GLE, "GL2.gle": fx.TABS_GLE},
    })
    h.add_rdb(info)
    return h, info


def test_the_page_comes_back_as_html(tmp_path):
    h, _ = _harness(tmp_path)
    r = h.get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_an_unknown_route_is_404(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/nao-existe").status == 404
    assert h.post("/nao-existe").status == 404


def test_it_lists_the_projects_rdbs(tmp_path):
    h, info = _harness(tmp_path)
    out = h.get("/rdbs").json()
    assert out["ok"] is True
    assert [r["name"] for r in out["rdbs"]] == ["projeto.rdb"]
    assert out["rdbs"][0]["dirty"] == 0


# -- reading the tabs -------------------------------------------------------

def test_it_lists_the_relays_and_their_gles(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    out = h.get(f"/gles?rdb={key}").json()
    assert out["ok"] is True
    assert [r["relay"] for r in out["relays"]] == ["QPC1_TR1"]
    assert [g["gle"] for g in out["relays"][0]["gles"]] == ["GL1.gle", "GL2.gle"]
    assert out["relays"][0]["gles"][0]["pages"] == 6


def test_gles_for_an_unknown_rdb_is_404(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/gles?rdb=naoexiste").status == 404


def test_it_reads_one_gles_tabs(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    out = h.get(f"/pages?rdb={key}&relay=QPC1_TR1&gle=GL1.gle").json()
    assert out["ok"] is True
    assert [p["name"] for p in out["pages"]] == [
        "Capa", "Entradas Críticas", "U>U< I >I<",
        "RESERVA", "RESERVA", "52- CMD DE FECHAMENT",
    ]
    assert [p["elements"] for p in out["pages"]] == [1, 2, 0, 0, 0, 0]
    assert out["order"] == [0, 1, 2, 3, 4, 5]      # nothing staged yet
    assert out["dirty"] is False


def test_pages_for_a_gle_the_relay_does_not_have_is_404(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    r = h.get(f"/pages?rdb={key}&relay=QPC1_TR1&gle=GL9.gle")
    assert r.status == 404


def test_pages_for_a_gle_with_a_doctype_returns_400(tmp_path):
    """A GLE with DOCTYPE must return 400, not crash the request.

    This is the gap that let the bug through -- the DTD path was only tested
    against read_pages directly, never through HTTP.
    """
    h = build(build_gle_tabs_handler, tmp_path)
    hostile = fx.TABS_GLE.replace(
        b"<editor>", b"<!DOCTYPE editor [<!ENTITY a 'b'>]>\r\n<editor>", 1)
    info = fake_rdb(tmp_path, {
        "QPC1_TR1": {"GL1.gle": hostile},
    })
    h.add_rdb(info)
    key = info.sha256[:12]
    r = h.get(f"/pages?rdb={key}&relay=QPC1_TR1&gle=GL1.gle")
    assert r.status == 400
    out = r.json()
    assert out["ok"] is False
    assert "error" in out


# -- staging ----------------------------------------------------------------

def _stage(h, key, order, names=None):
    return h.post("/stage", {"rdb": key, "relay": "QPC1_TR1", "gle": "GL1.gle",
                             "order": order, "names": names or {}})


def test_a_reorder_is_staged_and_shows_on_the_gle_list(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    out = _stage(h, key, [1, 0, 2, 3, 4, 5]).json()
    assert out["ok"] is True
    assert out["moved"] == 2
    assert out["dirty"] is True
    gles = h.get(f"/gles?rdb={key}").json()["relays"][0]["gles"]
    assert gles[0]["dirty"] is True
    assert gles[1]["dirty"] is False
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == 1


def test_a_staged_edit_comes_back_from_pages(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    _stage(h, key, [1, 0, 2, 3, 4, 5], {"0": "CAPA NOVA"})
    out = h.get(f"/pages?rdb={key}&relay=QPC1_TR1&gle=GL1.gle").json()
    assert out["order"] == [1, 0, 2, 3, 4, 5]
    assert out["names"] == {"0": "CAPA NOVA"}
    assert out["dirty"] is True


def test_an_edit_that_changes_nothing_is_not_staged(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    out = _stage(h, key, [0, 1, 2, 3, 4, 5]).json()
    assert out["ok"] is True
    assert out["dirty"] is False
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == 0


def test_a_name_over_the_limit_is_refused_with_a_reason(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    r = _stage(h, key, [0, 1, 2, 3, 4, 5], {"0": "A" * 21})
    assert r.status == 400
    out = r.json()
    assert out["ok"] is False
    assert "20" in out["error"]
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == 0


def test_an_order_that_drops_a_page_is_refused(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    assert _stage(h, key, [0, 1, 2]).status == 400


def test_reset_clears_one_gle(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    _stage(h, key, [1, 0, 2, 3, 4, 5])
    out = h.post("/reset", {"rdb": key, "relay": "QPC1_TR1",
                            "gle": "GL1.gle"}).json()
    assert out["ok"] is True
    assert out["dirty"] == 0


def test_reset_without_a_gle_clears_the_whole_rdb(tmp_path):
    h, info = _harness(tmp_path)
    key = info.sha256[:12]
    _stage(h, key, [1, 0, 2, 3, 4, 5])
    h.post("/stage", {"rdb": key, "relay": "QPC1_TR1", "gle": "GL2.gle",
                      "order": [1, 0, 2, 3, 4, 5], "names": {}})
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == 2
    assert h.post("/reset", {"rdb": key}).json()["dirty"] == 0
