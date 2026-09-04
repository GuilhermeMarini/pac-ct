"""The Editor de Mapa DNP's routes -- the largest surface of the six tools.

`test_dnp_map_model.py` and `test_dnp_map_export.py` cover the per-session
diffs, the drag, the cross-relay copy and the export as units. This drives the
HTTP layer over them: which routes register an RDB while listing, what an
unknown key answers, and that an edit made through `/edit` is what `/map` and
the pending counts report afterwards.
"""

from __future__ import annotations

from pacct.web.dnp_map.handler import build_dnp_map_handler
from tests.dnp_fixtures import SAMPLE_411L
from tests.web_harness import build, fake_rdb

# A second relay of the SAME RELAYTYPE, which is what makes it a legal
# destination for a copy -- `set_dnp.same_model` compares the [INFO] string
# exactly, only normalised for blanks and case.
_OTHER_411L = SAMPLE_411L.replace(b'BI_1,"PSV22"', b'BI_1,"PSV99"')
# A different model: eligible for nothing, and the list must still show it.
_751 = SAMPLE_411L.replace(b"RELAYTYPE=SEL-411L-A", b"RELAYTYPE=SEL-751-A")


def _harness(tmp_path, relays=None):
    h = build(build_dnp_map_handler, tmp_path)
    info = fake_rdb(tmp_path, relays or {
        "QPC1_TR1": {"SET_D1.TXT": SAMPLE_411L},
        "QPC1_TR2": {"SET_D1.TXT": _OTHER_411L},
        "QPC1_2440": {"SET_D1.TXT": _751},
    })
    h.add_rdb(info)
    return h, info, info.sha256[:12]


# -- pages ------------------------------------------------------------------

def test_the_three_pages_come_back_as_html(tmp_path):
    h, _, _ = _harness(tmp_path)
    for path in ("/", "/editor", "/copiar"):
        r = h.get(path)
        assert r.status == 200, path
        assert r.headers["content-type"].startswith("text/html"), path


def test_an_unknown_route_is_404(tmp_path):
    h, _, _ = _harness(tmp_path)
    assert h.get("/nao-existe").status == 404
    assert h.post("/nao-existe").status == 404


# -- listing ----------------------------------------------------------------

def test_rdbs_lists_the_project_not_what_this_tool_adopted(tmp_path):
    """There is one list of files and it lives in `/files/`. What this route
    adds is the only thing this tool knows: the pending edits per RDB."""
    h, info, short = _harness(tmp_path)
    out = h.get("/rdbs").json()
    assert out["ok"] is True
    assert [r["sha256"] for r in out["rdbs"]] == [info.sha256]
    assert out["rdbs"][0]["dirty"] == []


def test_listing_registers_the_rdb_so_a_row_click_goes_straight_to_relays(tmp_path):
    """The Editor de Mapa DNP dropped `/select-rdb` entirely: `/rdbs`
    registers each RDB while listing, so the next call is `/relays`."""
    h, _, short = _harness(tmp_path)
    h.get("/rdbs")
    assert h.get(f"/relays?rdb={short}").json()["ok"] is True


def test_relays_reports_each_relays_type_and_sessions(tmp_path):
    h, _, short = _harness(tmp_path)
    relays = h.get(f"/relays?rdb={short}").json()["relays"]
    by_name = {r["name"]: r for r in relays}
    assert by_name["QPC1_TR1"]["relaytype"] == "SEL-411L-A"
    assert by_name["QPC1_TR1"]["sessions"] == ["D1"]
    assert by_name["QPC1_2440"]["relaytype"] == "SEL-751-A"


def test_a_relay_with_settings_but_no_gle_still_appears(tmp_path):
    """`discover()` walks `Relays/` rather than reusing `RdbInfo.relays`,
    which only lists a relay that owns a `.gle` -- a data concentrator has a
    DNP map and no diagram, and its map is exactly what someone edits."""
    h, _, short = _harness(tmp_path, {"SEL_2440": {"SET_D1.TXT": _751}})
    names = [r["name"] for r in h.get(f"/relays?rdb={short}").json()["relays"]]
    assert names == ["SEL_2440"]


def test_an_rdb_key_that_names_no_file_is_404(tmp_path):
    """Left for an old link to a file that has left the project -- an RDB that
    IS in the library needs no extra step."""
    h, _, _ = _harness(tmp_path)
    r = h.get("/relays?rdb=deadbeefdead")
    assert r.status == 404
    assert r.json()["ok"] is False


# -- the map ----------------------------------------------------------------

def test_map_returns_the_points_of_one_session(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D1")
    assert r.status == 200
    blocks = r.json()["blocks"]
    values = {p["key"]: p["value"] for p in blocks["BI"]}
    assert values["BI_1"] == "PSV22"


def test_map_for_a_session_that_does_not_exist_is_404(tmp_path):
    h, _, short = _harness(tmp_path)
    assert h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D9").status == 404
    assert h.get(f"/map?rdb={short}&relay=NAO_EXISTE&d=D1").status == 404


def test_a_point_carries_its_scale_and_deadband_keys(tmp_path):
    """The scale is an attribute of the mapped quantity, not a point of its
    own -- which is what makes a drag move `AI_SCAn`/`AI_DBDn` with it."""
    h, _, short = _harness(tmp_path)
    ai = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D1").json()["blocks"]["AI"]
    assert ai[0]["sca_key"] == "AI_SCA1"
    assert ai[0]["dbd_key"] == "AI_DBD1"


# -- editing ----------------------------------------------------------------

def test_an_edit_is_pending_and_shows_up_in_the_map(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                         "changes": {"BI_1": "PSV33"}})
    assert r.status == 200
    blocks = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D1").json()["blocks"]
    values = {p["key"]: p["value"] for p in blocks["BI"]}
    assert values["BI_1"] == "PSV33"


def test_an_edit_is_counted_against_its_own_rdb(tmp_path):
    h, _, short = _harness(tmp_path)
    h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                     "changes": {"BI_1": "PSV33"}})
    dirty = h.get("/rdbs").json()["rdbs"][0]["dirty"]
    assert dirty != []


def test_editing_back_to_the_original_value_leaves_nothing_pending(tmp_path):
    """Each field is diffed against its OWN original, so a value that already
    matched is not an edit and the pending count stays honest."""
    h, _, short = _harness(tmp_path)
    h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                     "changes": {"BI_1": "PSV33"}})
    h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                     "changes": {"BI_1": "PSV22"}})
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == []


def test_edits_are_per_visitor(tmp_path):
    """Two visitors on the same RDB -- the same sha256, the same extraction --
    must not see each other's pending edits. `st.rdbs` is a per-session cache
    over a shared, content-addressed extraction, so the file is one and the
    edits are not."""
    h, info, short = _harness(tmp_path)
    h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                     "changes": {"BI_1": "PSV33"}})
    other, _ = h.sessions.resolve(None)
    h.add_rdb(info, session=other)
    blocks = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D1",
                   session=other).json()["blocks"]
    values = {p["key"]: p["value"] for p in blocks["BI"]}
    assert values["BI_1"] == "PSV22"


# -- the cross-relay copy ---------------------------------------------------

def test_copying_onto_a_relay_of_another_model_is_refused(tmp_path):
    """`set_dnp.same_model` is an exact RELAYTYPE comparison, deliberately NOT
    the lenient family match: the option suffix is what changes the I/O board,
    and with it how many BI/BO points the file has."""
    h, _, short = _harness(tmp_path)
    r = h.post("/copy-to-relays", {
        "rdb": short, "relay": "QPC1_TR1", "session": "D1",
        "dest_rdb": short,
        "targets": [{"relay": "QPC1_2440", "session": "D1"}],
    })
    assert r.status in (400, 409, 422)
    assert r.json()["ok"] is False


def test_a_rejected_target_list_writes_nothing_at_all(tmp_path):
    """The route validates every target before writing any."""
    h, _, short = _harness(tmp_path)
    h.post("/copy-to-relays", {
        "rdb": short, "relay": "QPC1_TR1", "session": "D1", "dest_rdb": short,
        "targets": [{"relay": "QPC1_TR2", "session": "D1"},
                    {"relay": "QPC1_2440", "session": "D1"}],
    })
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == []


def test_copying_onto_a_same_model_relay_records_the_edits(tmp_path):
    h, _, short = _harness(tmp_path)
    r = h.post("/copy-to-relays", {
        "rdb": short, "relay": "QPC1_TR1", "session": "D1",
        "dest_rdb": short,
        "targets": [{"relay": "QPC1_TR2", "session": "D1"}],
    })
    assert r.status == 200 and r.json()["ok"] is True
    blocks = h.get(f"/map?rdb={short}&relay=QPC1_TR2&d=D1").json()["blocks"]
    values = {p["key"]: p["value"] for p in blocks["BI"]}
    assert values["BI_1"] == "PSV22"


def test_copying_a_relay_onto_itself_inside_one_rdb_changes_nothing(tmp_path):
    h, _, short = _harness(tmp_path)
    h.post("/copy-to-relays", {
        "rdb": short, "relay": "QPC1_TR1", "session": "D1",
        "dest_rdb": short,
        "targets": [{"relay": "QPC1_TR1", "session": "D1"}],
    })
    assert h.get("/rdbs").json()["rdbs"][0]["dirty"] == []
