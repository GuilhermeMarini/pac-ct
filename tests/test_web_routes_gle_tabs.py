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
