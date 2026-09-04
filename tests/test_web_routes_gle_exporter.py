"""The Exportador de Comentários GLE's seven routes.

The tool round-trips a GLE's port comments through a spreadsheet, so what
matters at the HTTP layer is the selection (`/select-rdb`), the two refusals
around `/export`, and `/download`'s sandbox -- which is the one that has a
security shape: it takes a PATH from the request, so it is confined to this
session's own directories and must NOT reach the shared RDB cache.
"""

from __future__ import annotations

from pacct.web.gle_exporter import build_gle_exporter_handler
from pacct.web.project_files import library as filelib
from tests import gle_fixtures as fx
from tests.web_harness import build, fake_rdb


def _harness(tmp_path):
    h = build(build_gle_exporter_handler, tmp_path)
    info = fake_rdb(tmp_path, {"QPC1_TR1": {"GL1.gle": fx.SAMPLE_GLE}})
    h.add_rdb(info)
    return h, info


# -- pages ------------------------------------------------------------------

def test_the_page_comes_back_as_html(tmp_path):
    h, _ = _harness(tmp_path)
    r = h.get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_gle_state_is_a_liveness_sentinel(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/gle-state").json() == {"ok": True}


def test_an_unknown_route_is_404(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/nao-existe").status == 404
    assert h.post("/nao-existe").status == 404


# -- choosing an RDB --------------------------------------------------------

def test_state_before_any_rdb_says_so(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/state").json()["has_rdb"] is False


def test_choosing_an_rdb_reports_its_relays_and_gles(tmp_path):
    h, info = _harness(tmp_path)
    r = h.post("/select-rdb", {"sha256": info.sha256})
    assert r.status == 200
    out = r.json()
    assert out["has_rdb"] is True
    assert out["rdb_name"] == "projeto.rdb"


def test_choosing_an_rdb_the_project_does_not_have_is_404(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.post("/select-rdb", {"sha256": "f" * 64}).status == 404


def test_choosing_an_scd_as_if_it_were_an_rdb_is_404(tmp_path):
    """The kind is checked, not just the presence: an SCD shares the library
    and its sha would otherwise resolve."""
    h, _ = _harness(tmp_path)
    with h.session.lock:
        h.library().add(filelib.FileEntry(
            sha256="d" * 64, kind=filelib.KIND_SCD,
            display_name="projeto.scd", size=10))
    assert h.post("/select-rdb", {"sha256": "d" * 64}).status == 404


def test_the_choice_survives_into_the_next_request(tmp_path):
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    assert h.get("/state").json()["has_rdb"] is True


def test_two_visitors_do_not_share_the_chosen_rdb(tmp_path):
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    other, _ = h.sessions.resolve(None)
    assert h.get("/state", session=other).json()["has_rdb"] is False


# -- export -----------------------------------------------------------------

def test_exporting_with_no_rdb_chosen_is_refused(tmp_path):
    h, _ = _harness(tmp_path)
    r = h.post("/export", {"selections": [{"relay": "QPC1_TR1",
                                           "gle": "GL1"}]})
    assert r.status in (400, 409)


def test_exporting_an_empty_selection_is_refused(tmp_path):
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    assert h.post("/export", {"selections": []}).status == 400


def test_exporting_a_gle_that_is_not_in_the_rdb_is_refused(tmp_path):
    """Every selection is resolved before anything is written; a list that
    resolves to nothing is 422 rather than an empty spreadsheet."""
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    r = h.post("/export", {"selections": [{"relay": "NAO_EXISTE",
                                           "gle": "GL9"}]})
    assert r.status == 422


def test_exporting_a_real_gle_produces_a_spreadsheet(tmp_path):
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    r = h.post("/export", {"selections": [{"relay": "QPC1_TR1",
                                           "gle": "GL1"}]})
    assert r.status == 200
    out = r.json()
    assert out["ok"] is True
    assert out["download_url"].endswith(".xlsx")


def test_an_export_enters_the_project_library(tmp_path):
    """A tool's output goes back into the project -- that is the only path
    between two tabs, and it is what `publish_output` -> `derived.adopt`
    exists for. An `.xlsx` enters as KIND_XLSX, which no picker offers."""
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    out = h.post("/export", {"selections": [{"relay": "QPC1_TR1",
                                             "gle": "GL1"}]}).json()
    assert out["project_file"] is not None
    assert out["project_file"]["kind"] == filelib.KIND_XLSX
    assert out["project_file"]["origin"]


# -- download: the sandbox --------------------------------------------------

def test_download_without_a_file_param_is_400(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.get("/download").status == 400


def test_download_outside_the_sessions_own_directories_is_403(tmp_path):
    """This route takes the path from the REQUEST, so it is confined to the
    session's `out/` and `xlsx/`. Widening it to the shared RDB cache would
    let one visitor ask for another's generated file -- which is exactly why
    `/files/download?sha256=` is a separate route."""
    h, _ = _harness(tmp_path)
    assert h.get("/download?file=/etc/passwd").status == 403
    assert h.get(f"/download?file={tmp_path}/projeto.rdb").status == 403


def test_download_hands_back_a_file_the_tool_itself_produced(tmp_path):
    h, info = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    out = h.post("/export", {"selections": [{"relay": "QPC1_TR1",
                                             "gle": "GL1"}]}).json()
    from urllib.parse import parse_qs, urlparse
    target = parse_qs(urlparse(out["download_url"]).query)["file"][0]
    r = h.get(f"/download?file={target}")
    assert r.status == 200
    assert r.body[:2] == b"PK"          # an xlsx is a zip
    assert "attachment;" in r.headers["content-disposition"]
