"""The VB Updater's routes.

The tool syncs Virtual Bit descriptions between a GLE and an SCD, so it is the
one tool that needs BOTH files chosen before it can do anything -- which makes
"neither is loaded yet" a first-class answer rather than an error, and is most
of what is pinned here. `/apply` and `/apply-batch` write into an RDB, and
that path goes through `rdb_write` (never `olefile` directly, never padding
XML to fit); what is driven here is the refusals in front of it.
"""

from __future__ import annotations

from urllib.parse import quote, unquote

from pacct.web.project_files import library as filelib
from pacct.web.vb_updater import build_vb_updater_handler
from tests import gle_fixtures as fx
from tests.web_harness import build, fake_rdb

# The matcher pairs on IP or RID, never on the name -- so both sides carry the
# same documentation address (RFC 5737) for the match to be found at all.
_IP = "192.0.2.10"
_SCD = fx.scd(fx.ied("QPC1_TR1", fx.extref("VB105", "TR1 UPC1 FALHA GOOSE")),
              ips={"QPC1_TR1": _IP})


def _harness(tmp_path, *, with_scd=True):
    h = build(build_vb_updater_handler, tmp_path)
    info = fake_rdb(tmp_path, {"QPC1_TR1": {"GL1.gle": fx.SAMPLE_GLE}},
                    ips={"QPC1_TR1": _IP})
    h.add_rdb(info)
    scd_entry = None
    if with_scd:
        scd_path = tmp_path / "projeto.scd"
        scd_path.write_bytes(_SCD)
        scd_entry = h.add_scd(scd_path, name="projeto.scd")
    return h, info, scd_entry


# -- pages ------------------------------------------------------------------

def test_the_page_comes_back_as_html(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_vb_state_is_a_liveness_sentinel(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/vb-state").json() == {"ok": True}


def test_an_unknown_route_is_404(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/nao-existe").status == 404
    assert h.post("/nao-existe").status == 404


# -- state, before anything is chosen ---------------------------------------

def test_state_starts_with_neither_file(tmp_path):
    h, _, _ = _harness(tmp_path)
    out = h.get("/state").json()
    assert out["has_rdb"] is False and out["has_scd"] is False
    assert out["matches"] == []


def test_state_names_the_scd_by_its_display_name_not_its_path(tmp_path):
    """The library stores an SCD as `<sha12>.scd`, so a tool deriving the name
    from the path on disk hands the user `72586aeda11e_...`. It reads
    `st.scd_name`."""
    h, _, scd = _harness(tmp_path)
    h.post("/select-scd", {"sha256": scd.sha256})
    assert h.get("/state").json()["scd_name"] == "projeto.scd"


# -- choosing the two files -------------------------------------------------

def test_choosing_an_rdb_lists_the_gles_per_relay(tmp_path):
    h, info, _ = _harness(tmp_path)
    out = h.post("/select-rdb", {"sha256": info.sha256}).json()
    assert out["has_rdb"] is True
    assert out["gles_by_relay"] == {"QPC1_TR1": ["GL1"]}


def test_choosing_an_rdb_that_is_not_in_the_project_is_404(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.post("/select-rdb", {"sha256": "f" * 64}).status == 404


def test_select_rdb_and_select_scd_check_the_kind_not_just_the_sha(tmp_path):
    """One route serves both, branching on the path, so each has to refuse the
    other's kind -- both files are in the same library."""
    h, info, scd = _harness(tmp_path)
    assert h.post("/select-rdb", {"sha256": scd.sha256}).status == 404
    assert h.post("/select-scd", {"sha256": info.sha256}).status == 404


def test_the_cross_match_only_runs_once_both_files_are_there(tmp_path):
    """`_maybe_match` already knows the match needs both, so choosing the RDB
    on its own reports no matches rather than a half one."""
    h, info, scd = _harness(tmp_path)
    assert h.post("/select-rdb", {"sha256": info.sha256}).json()["matches"] == []
    out = h.post("/select-scd", {"sha256": scd.sha256}).json()
    assert out["has_rdb"] is True and out["has_scd"] is True
    assert [m["rdb_name"] for m in out["matches"]] == ["QPC1_TR1"]


def test_the_match_is_made_on_the_ip_and_says_so(tmp_path):
    """It pairs by IP or RID and never by name: two files routinely name the
    same bay differently, and `matched_by` is what tells the user which of the
    two answered."""
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    out = h.post("/select-scd", {"sha256": scd.sha256}).json()
    assert out["matches"][0]["matched_by"] == "ip"
    assert out["matches"][0]["ip"] == _IP


def test_two_visitors_do_not_share_the_chosen_files(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    other, _ = h.sessions.resolve(None)
    out = h.get("/state", session=other).json()
    assert out["has_rdb"] is False and out["has_scd"] is False


# -- compare ----------------------------------------------------------------

def test_compare_before_both_files_are_chosen_is_409(tmp_path):
    """409 and not 404: nothing is missing, the tool simply has not been given
    what it needs yet."""
    h, _, _ = _harness(tmp_path)
    r = h.get("/compare?relay=QPC1_TR1&ied=QPC1_TR1&gle=GL1")
    assert r.status == 409


def test_compare_of_a_gle_that_is_not_in_the_rdb_is_404(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    r = h.get("/compare?relay=QPC1_TR1&ied=QPC1_TR1&gle=GL9")
    assert r.status == 404


def test_compare_renders_the_two_sides(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    r = h.get("/compare?relay=QPC1_TR1&ied=QPC1_TR1&gle=GL1")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "VB105" in r.text


# -- apply: the refusals in front of the RDB write --------------------------

def test_applying_with_no_files_chosen_is_refused(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.post("/apply", {"direction": "scd-to-gle", "relay": "QPC1_TR1",
                          "ied": "QPC1_TR1", "gle": "GL1"})
    assert r.status in (400, 409)


def test_an_unknown_direction_is_refused_before_anything_is_read(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    r = h.post("/apply", {"direction": "para-os-lados", "relay": "QPC1_TR1",
                          "ied": "QPC1_TR1", "gle": "GL1"})
    assert r.status == 400
    assert "direction" in r.json()["error"]


def test_a_body_that_is_not_json_is_refused(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.post("/apply", body=b"{{{").status == 400


def test_apply_batch_with_no_selection_is_refused(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    assert h.post("/apply-batch", {"selections": []}).status == 400


# -- the spreadsheet round trip ---------------------------------------------

def test_exporting_descriptions_with_an_empty_selection_is_refused(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.post("/export-descriptions", {"selections": []}).status == 400


def test_exporting_descriptions_needs_the_scd(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.post("/export-descriptions",
               {"selections": [{"relay": "QPC1_TR1", "ied": "QPC1_TR1"}]})
    assert r.status == 409


def test_exporting_descriptions_produces_a_spreadsheet(tmp_path):
    h, info, scd = _harness(tmp_path)
    h.post("/select-rdb", {"sha256": info.sha256})
    h.post("/select-scd", {"sha256": scd.sha256})
    r = h.post("/export-descriptions",
               {"selections": [{"relay": "QPC1_TR1", "ied": "QPC1_TR1"}]})
    assert r.status == 200
    out = r.json()
    assert out["download_url"].endswith(".xlsx")
    assert out["project_file"]["kind"] == filelib.KIND_XLSX


def test_download_is_sandboxed_to_the_sessions_own_directories(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/download?file=/etc/passwd").status == 403


def test_download_names_an_accented_file_with_rfc_5987(tmp_path):
    """The name of the file this tool WRITES comes from the SCD's display
    name (`scd_label`), which carries accents now that the library no longer
    strips them. The header used to be a plain `filename="..."`, and
    `http.server.send_header` encodes latin-1 strict: an en-dash in the
    substation's name -- or any character outside latin-1 -- raised
    `UnicodeEncodeError` in the middle of the response, after the status line
    had gone out. `dnp_map`, the GLV and Arquivos do Projeto already answered
    in RFC 5987; these two were behind `sanitize_name` and never converted.
    """
    h, _, _ = _harness(tmp_path)
    out = h.session.subdir("vb-updater-out")
    name = "subestação–1_comments_updated.scd"     # en-dash: not latin-1
    (out / name).write_bytes(b"<SCL/>")
    r = h.get("/download?file=" + quote(str(out / name), safe=""))
    assert r.status == 200
    disp = r.headers["content-disposition"]
    assert unquote(disp.split("UTF-8''", 1)[1]) == name
