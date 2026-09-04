"""The Comparador de Ajustes' five routes.

The comparator puts up to 7 relays of ONE family side by side and compares
with SELOGIC equivalence rather than a text diff. What is driven here is the
HTTP layer: the project listing that registers each RDB while listing (which
is what let `/select-rdb` go away entirely), the family gate on `/groups`, and
the refusals on `/diff`.
"""

from __future__ import annotations

from pacct.web.settings_compare import build_settings_compare_handler
from tests.web_harness import build, fake_rdb

_S1_411L = (b'[S1]\r\n'
            b'RELAYTYPE,"SEL-411L-A"\x1c\r\n'
            b'E21P,"1"\x1c\r\n'
            b'Z1P,"5.00"\x1c\r\n')
# Same family, one setting apart -- what the comparator exists to show.
_S1_411L_OTHER = _S1_411L.replace(b'Z1P,"5.00"', b'Z1P,"6.50"')
# A 7xx: a different family, which is the gate `/groups` enforces.
_SET_1_751 = (b'[1]\r\n'
              b'RELAYTYPE,"SEL-751-A"\x1c\r\n'
              b'E50P1,"1"\x1c\r\n')


def _harness(tmp_path):
    h = build(build_settings_compare_handler, tmp_path)
    info = fake_rdb(
        tmp_path,
        {"QPC1_TR1": {"SET_S1.TXT": _S1_411L},
         "QPC1_TR2": {"SET_S1.TXT": _S1_411L_OTHER},
         "QPC1_ALIM": {"set_1.txt": _SET_1_751}},
        models={"QPC1_TR1": "SEL-411L-A", "QPC1_TR2": "SEL-411L-A",
                "QPC1_ALIM": "SEL-751-A"},
    )
    h.add_rdb(info)
    return h, info, info.sha256[:12]


def _ref(short, relay):
    return {"rdb_key": short, "relay_name": relay}


# -- pages ------------------------------------------------------------------

def test_the_page_comes_back_as_html(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_settings_state_is_a_liveness_sentinel(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/settings-state").json() == {"ok": True}


def test_an_unknown_route_is_404(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/nao-existe").status == 404
    assert h.post("/nao-existe").status == 404


# -- state ------------------------------------------------------------------

def test_state_lists_the_projects_rdbs_with_their_relays(tmp_path):
    h, info, short = _harness(tmp_path)
    rdbs = h.get("/state").json()["rdbs"]
    assert [r["sha256"] for r in rdbs] == [info.sha256]
    names = {r["name"] for r in rdbs[0]["relays"]}
    assert names == {"QPC1_TR1", "QPC1_TR2", "QPC1_ALIM"}


def test_state_is_empty_for_a_visitor_with_no_files(tmp_path):
    h, _, _ = _harness(tmp_path)
    other, _ = h.sessions.resolve(None)
    assert h.get("/state", session=other).json() == {"rdbs": []}


def test_listing_registers_the_rdb_so_groups_needs_no_select_step(tmp_path):
    """There is no `/select-rdb` on this tool: `/state` registers each RDB
    into `st.rdbs` while listing, so a row click goes straight to `/groups`."""
    h, _, short = _harness(tmp_path)
    h.get("/state")
    r = h.post("/groups", {"relays": [_ref(short, "QPC1_TR1")]})
    assert r.status == 200


def test_groups_works_even_without_the_listing_first(tmp_path):
    """`_ensure_rdbs` is the safety net: a tab left open while the session
    lost its state must not answer "relé não encontrado" for a file that is
    right there in the library."""
    h, _, short = _harness(tmp_path)
    assert h.post("/groups", {"relays": [_ref(short, "QPC1_TR1")]}).status == 200


# -- groups: the family gate ------------------------------------------------

def test_groups_reports_the_family_and_its_catalogue(tmp_path):
    h, _, short = _harness(tmp_path)
    out = h.post("/groups", {"relays": [_ref(short, "QPC1_TR1")]}).json()
    assert out["family"] == "4xx"
    by_key = {g["key"]: g for g in out["groups"]}
    assert by_key["S1"]["file"] == "SET_S1.TXT"
    assert by_key["S1"]["present_in_all"] is True
    assert by_key["S2"]["present_in_any"] is False


def test_two_relays_of_one_family_are_accepted(tmp_path):
    h, _, short = _harness(tmp_path)
    out = h.post("/groups", {"relays": [_ref(short, "QPC1_TR1"),
                                        _ref(short, "QPC1_TR2")]}).json()
    assert out["family"] == "4xx"
    assert next(g for g in out["groups"] if g["key"] == "S1")["present_count"] == 2


def test_mixing_two_families_is_refused(tmp_path):
    """The comparator compares relays of ONE family; a 411L against a 751 has
    no common catalogue to lay side by side."""
    h, _, short = _harness(tmp_path)
    r = h.post("/groups", {"relays": [_ref(short, "QPC1_TR1"),
                                      _ref(short, "QPC1_ALIM")]})
    assert r.status == 400
    assert "familia" in r.json()["error"]


def test_a_relay_that_is_not_in_the_rdb_is_refused(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.post("/groups", {"relays": [_ref(short, "NAO_EXISTE")]})
    assert r.status == 400


def test_an_rdb_key_that_names_nothing_is_refused(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.post("/groups", {"relays": [_ref("deadbeefdead", "QPC1_TR1")]})
    assert r.status == 400


def test_groups_with_no_relays_at_all_is_refused(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.post("/groups", {"relays": []}).status == 400


def test_a_body_that_is_not_json_is_refused_as_such(tmp_path):
    h, _, _ = _harness(tmp_path)
    r = h.post("/groups", body=b"{nao e json")
    assert r.status == 400
    assert "JSON" in r.json()["error"]


# -- diff -------------------------------------------------------------------

def test_diff_puts_two_relays_side_by_side(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.post("/diff", {"relays": [_ref(short, "QPC1_TR1"),
                                    _ref(short, "QPC1_TR2")],
                         "groups": ["S1"]})
    assert r.status == 200
    assert "error" not in r.json()


def test_diff_with_a_bad_body_is_400_and_not_a_stack_trace(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.post("/diff", body=b"nao e json").status == 400


def test_diff_of_a_relay_that_is_not_there_is_400(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.post("/diff", {"relays": [_ref(short, "NAO_EXISTE")],
                         "groups": ["S1"]})
    assert r.status == 400
