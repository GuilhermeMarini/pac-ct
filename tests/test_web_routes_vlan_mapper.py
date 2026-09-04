"""The VLAN Mapper's four routes, driven through `tests/web_harness.py`.

The tool derives GOOSE VLAN port maps from an SCD, so every route below either
answers about the SCD the visitor has chosen or refuses because there is none.
`/select-scd` is the tail of the old upload handler (`docs/ENGINEERING-NOTES.md`:
uploads happen in exactly one place), which is why it takes a sha256 out of the
project library rather than a file.
"""

from __future__ import annotations

from pathlib import Path

from pacct.web.vlan_mapper import build_vlan_mapper_handler
from tests.web_harness import build

_SCD = Path(__file__).parent / "fixtures" / "saddr_min.scd"


def _harness(tmp_path):
    return build(build_vlan_mapper_handler, tmp_path)


# -- the page ---------------------------------------------------------------

def test_the_page_comes_back_as_html(tmp_path):
    r = _harness(tmp_path).get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_the_page_is_served_at_index_html_too(tmp_path):
    h = _harness(tmp_path)
    assert h.get("/index.html").status == 200


def test_an_unknown_route_is_404_and_not_a_stack_trace(tmp_path):
    r = _harness(tmp_path).get("/nao-existe")
    assert r.status == 404


# -- state ------------------------------------------------------------------

def test_state_before_any_scd_is_empty_and_says_so(tmp_path):
    r = _harness(tmp_path).get("/state")
    assert r.status == 200
    assert r.json() == {"has_scd": False, "scd_name": None, "rows": [],
                        "ied_count": 0, "vlan_count": 0, "all_vlans": []}


def test_vlan_state_is_a_liveness_sentinel_and_not_the_state(tmp_path):
    """The home asks `/vlan-state` only to find out the tool is up, so it
    answers a flat `{"ok": true}` and must not start carrying the payload --
    it is reached with no session's worth of work done."""
    h = _harness(tmp_path)
    assert h.get("/vlan-state").json() == {"ok": True}


# -- choosing an SCD --------------------------------------------------------

def test_choosing_an_scd_the_project_does_not_have_is_404(tmp_path):
    r = _harness(tmp_path).post("/select-scd", {"sha256": "f" * 64})
    assert r.status == 404
    assert "projeto" in r.json()["error"]


def test_choosing_with_no_sha_at_all_is_404_not_a_crash(tmp_path):
    assert _harness(tmp_path).post("/select-scd", {}).status == 404


def test_choosing_an_scd_reads_its_ieds(tmp_path):
    h = _harness(tmp_path)
    entry = h.add_scd(_SCD, name="saddr_min.scd")
    r = h.post("/select-scd", {"sha256": entry.sha256})
    assert r.status == 200
    out = r.json()
    assert out["has_scd"] is True
    assert out["scd_name"] == "saddr_min.scd"
    assert [row["ied_name"] for row in out["rows"]] == ["REL_A"]


def test_the_chosen_scd_survives_into_the_next_request(tmp_path):
    """State is per visitor, so the second request has to see the first's
    choice -- a module-level singleton would pass this and be wrong for two
    visitors, which is why `_state_payload(st)` takes the state."""
    h = _harness(tmp_path)
    entry = h.add_scd(_SCD)
    h.post("/select-scd", {"sha256": entry.sha256})
    assert h.get("/state").json()["has_scd"] is True


def test_two_visitors_do_not_share_the_chosen_scd(tmp_path):
    h = _harness(tmp_path)
    entry = h.add_scd(_SCD)
    h.post("/select-scd", {"sha256": entry.sha256})
    other, _ = h.sessions.resolve(None)
    assert h.get("/state", session=other).json()["has_scd"] is False


def test_an_entry_of_the_wrong_kind_is_refused(tmp_path):
    """`/select-scd` checks the kind, not just the presence: an RDB shares the
    library with the SCDs and its sha would otherwise resolve."""
    h = _harness(tmp_path)
    from pacct.web.project_files import library as filelib
    with h.session.lock:
        h.library().add(filelib.FileEntry(
            sha256="a" * 64, kind=filelib.KIND_RDB,
            display_name="projeto.rdb", size=10))
    assert h.post("/select-scd", {"sha256": "a" * 64}).status == 404
