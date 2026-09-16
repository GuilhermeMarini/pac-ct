"""The GLV's routes, which were 4 of 26 covered before B12.

The GLV is the largest mount -- 26 routes against the next tool's 14 -- and it
was the least tested, because most of its routes need a diagram in the session
and there was no fixture that made one cheaply. `tests/test_glv_page_cache_route.py`
worked one out for its own two routes; this is that fixture applied to the rest.

What is asserted here is the CONTRACT, not the behaviour behind it: the status,
the shape of the payload and the failure mode. The GLE rendering, the poll
loops and the evaluator have their own tests, and a route test that re-asserted
them would fail for reasons that have nothing to do with the route.

**`GET /events` is the one route not driven here**, and it is in the document
as untested for a stated reason rather than an omission: it is Server-Sent
Events, holds the connection open and has no `Content-Length`, so a test that
drove it would have to decide when to stop listening -- a timing judgement, and
the flakiest kind. Its `?d=` guard IS covered below, which is the half that can
be pinned without a clock.
"""

from __future__ import annotations

import json
import logging

import pytest

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import SCAN_TELNET
from tests.web_harness import build

_LOG = logging.getLogger("test")


def _harness(tmp_path, **defaults):
    """A GLV with one open diagram, `d1`, active and disconnected."""
    h = build(build_glv_handler, tmp_path, GlvDefaults(**defaults))
    d = GlvDiagram("d1", relay_name="RELE1", gle_name="GL1", gle_path=None,
                   ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
                   scan_mode=SCAN_TELNET)
    d.pages_meta = [["Capa", "Capa"], ["TRIP", "TRIP"]]
    d.svgs = {p: f"<svg id='{p}'/>" for p in ("Capa", "TRIP")}
    st = h.sessions.state(h.session, "glv", h.handler.state_factory)
    st.diagrams["d1"] = d
    st.order.append("d1")
    st.active = "d1"
    return h, d


def _empty(tmp_path):
    """A GLV with no diagram at all -- the state a new visitor arrives in."""
    return build(build_glv_handler, tmp_path, GlvDefaults())


# -- the two screens ----------------------------------------------------------

def test_the_shell_redirects_to_the_picker_when_no_tab_is_open(tmp_path):
    """A shell with no tabs has nothing to show, so `/` is not always a page.

    302 and not 301: which tab is open changes, so the answer must not be
    cached by the browser as permanent.
    """
    h = _empty(tmp_path)
    r = h.get("/")
    assert r.status == 302
    assert r.headers["location"].endswith("/novo")


def test_the_shell_is_a_page_once_a_tab_is_open(tmp_path):
    h, _ = _harness(tmp_path)
    r = h.get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_the_shell_hands_the_open_tabs_down_in_its_page_data(tmp_path):
    """Substituted per REQUEST, unlike `/files/` -- this is the visitor's own
    tab strip, not a table that is the same for everyone."""
    h, _ = _harness(tmp_path)
    body = h.get("/").text
    assert 'id="page-data"' in body
    assert "d1" in body


def test_the_shell_can_be_asked_to_activate_a_tab_by_query(tmp_path):
    h, _ = _harness(tmp_path)
    st = h.sessions.state(h.session, "glv", h.handler.state_factory)
    st.active = None
    h.get("/?d=d1")
    assert st.active == "d1"


def test_the_picker_is_a_page_of_its_own(tmp_path):
    """`/novo` is a landing page, not only a redirect target."""
    h = _empty(tmp_path)
    r = h.get("/novo")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_the_picker_state_reports_the_ceilings_the_screen_enforces(tmp_path):
    h = _empty(tmp_path)
    body = h.get("/landing-state").json()
    assert body["open_diagrams"] == 0
    assert body["max_diagrams"] == GlvDefaults().max_diagrams
    assert body["scan_mode"] == GlvDefaults().scan_mode


def test_the_picker_state_locks_the_connect_button_under_no_relay(tmp_path):
    """`--no-relay` is a whole-run decision, so the screen is told to lock and
    not merely to default."""
    h = build(build_glv_handler, tmp_path, GlvDefaults(no_relay=True))
    body = h.get("/landing-state").json()
    assert body["no_relay_default"] is True
    assert body["no_relay_locked"] is True


# -- the tab strip ------------------------------------------------------------

def test_the_open_tabs_are_listed(tmp_path):
    h, _ = _harness(tmp_path)
    body = h.get("/diagrams").json()
    assert [t["id"] for t in body["diagrams"]] == ["d1"]
    assert body["active"] == "d1"


def test_activating_a_tab_answers_only_which_one_is_active(tmp_path):
    """The narrowest payload in the application, and deliberately so: the
    client already has the strip."""
    h, _ = _harness(tmp_path)
    body = h.post("/diagrams/activate?d=d1").json()
    assert body == {"active": "d1"}


def test_closing_the_only_tab_leaves_the_strip_empty(tmp_path):
    h, _ = _harness(tmp_path)
    body = h.post("/diagrams/close?d=d1").json()
    assert body["diagrams"] == []
    assert body["active"] is None


# -- the per-diagram reads ----------------------------------------------------

def test_the_metadata_names_the_relay_and_its_pages(tmp_path):
    h, _ = _harness(tmp_path)
    body = h.get("/meta?d=d1").json()
    assert body["relay"] == "RELE1"
    assert [p[0] for p in body["pages"]] == ["Capa", "TRIP"]


def test_the_metadata_prefers_the_remembered_page_over_the_default(tmp_path):
    """A tab comes back to page 27, not to page 2. Recorded by `/pages/<id>`,
    which is the only path to a page's SVG."""
    h, _ = _harness(tmp_path)
    h.get("/pages/TRIP?d=d1&have=1")
    assert h.get("/meta?d=d1").json()["initial"] == "TRIP"


def test_values_are_json_although_they_do_not_go_through_send_json(tmp_path):
    """Emitted with `_send(200, json.dumps(...), "application/json")`.

    Pinned because the difference is invisible from the outside and easy to
    lose: this route and `/debug/analogs` are the two JSON responses a scan for
    `_send_json` does not find.
    """
    h, _ = _harness(tmp_path)
    r = h.get("/values?d=d1&page=TRIP")
    assert r.status == 200
    assert r.headers["content-type"] == "application/json"
    assert "rev" in r.json()


def test_the_unreachable_report_comes_in_two_media_types(tmp_path):
    """The same question answered as JSON for the screen and as text for the
    engineer to paste into a report."""
    h, _ = _harness(tmp_path)
    as_json = h.get("/unreachable?d=d1")
    as_text = h.get("/unreachable.txt?d=d1")
    assert as_json.headers["content-type"] == "application/json"
    assert as_text.headers["content-type"].startswith("text/plain")


def test_the_debug_route_answers_json(tmp_path):
    """The only route with a path SEGMENT rather than a flat name, and the only
    one called `debug`. Recorded, not redesigned."""
    h, _ = _harness(tmp_path)
    r = h.get("/debug/analogs?d=d1")
    assert r.status == 200
    assert r.headers["content-type"] == "application/json"


def test_the_vb_source_route_is_reachable_without_an_scd(tmp_path):
    """Offline layer: with no SCD there is no answer to give, and saying so is
    the contract rather than an error."""
    h, _ = _harness(tmp_path)
    r = h.get("/vb-source?d=d1")
    assert r.status == 200


# -- the shared `?d=` guard ---------------------------------------------------

#: Every per-diagram route goes through `_diagram(qs)`, which answers the 404
#: itself. One guard, one failure shape, and `/events` is in the list because
#: this half of it can be pinned without holding a connection open.
PER_DIAGRAM_GET = [
    "/meta", "/values", "/events", "/group-state", "/note", "/highlights",
    "/vb-source", "/unreachable", "/unreachable.txt", "/debug/analogs",
    "/pages/TRIP",
]
PER_DIAGRAM_POST = [
    "/diagrams/close", "/diagrams/activate", "/connect", "/disconnect",
    "/period", "/group-state", "/note", "/highlights",
]


@pytest.mark.parametrize("path", PER_DIAGRAM_GET)
def test_a_get_for_a_diagram_that_is_not_open_is_404(path, tmp_path):
    h, _ = _harness(tmp_path)
    r = h.get(f"{path}?d=nao-existe")
    assert r.status == 404
    assert r.json()["error"]


@pytest.mark.parametrize("path", PER_DIAGRAM_POST)
def test_a_post_for_a_diagram_that_is_not_open_is_404(path, tmp_path):
    h, _ = _harness(tmp_path)
    r = h.post(f"{path}?d=nao-existe")
    assert r.status == 404
    assert r.json()["error"]


def test_omitting_d_falls_back_to_the_active_diagram(tmp_path):
    """`?d=` is optional everywhere, and its absence means the active tab."""
    h, _ = _harness(tmp_path)
    assert h.get("/meta").json()["relay"] == "RELE1"


# -- the relay conversation ---------------------------------------------------

def test_connecting_under_no_relay_is_refused_with_the_reason(tmp_path):
    """`--no-relay` is a viewing run, and 409 says the refusal is about the
    state of this execution and not about the request."""
    h, _ = _harness(tmp_path, no_relay=True)
    r = h.post("/connect?d=d1")
    assert r.status == 409
    assert "visualiza" in r.json()["error"]


def test_disconnecting_a_diagram_that_never_connected_is_not_an_error(tmp_path):
    """Idempotent by design: a double click on Desconectar must not raise."""
    h, _ = _harness(tmp_path)
    r = h.post("/disconnect?d=d1")
    assert r.status == 200
    assert r.json()["id"] == "d1"


def test_the_period_route_refuses_a_telnet_diagram_with_200(tmp_path):
    """The one route whose REFUSAL is a 200, and it is right to be.

    `/period` answers a three-valued `status` -- `aplicado`, `adiado`,
    `recusado` -- because the outcomes are not success and failure. The request
    was understood and answered; what it reports is that this diagram polls
    over telnet, where the cadence is several round trips per turn and nobody
    has a bench to measure tightening it. A 4xx would say the client got the
    request wrong, and it did not.

    This is the single place in the application where the HTTP status alone
    does not tell the caller what happened, and the contract records it as a
    deliberate exception rather than an inconsistency to be regularised.
    """
    h, _ = _harness(tmp_path)
    r = h.post("/period?d=d1", {"interval_ms": 250})
    assert r.status == 200
    body = r.json()
    assert body["status"] == "recusado"
    assert "MMS" in body["reason"]
    # Nothing was touched: the period already in force comes back.
    assert body["interval_ms"] != 250


@pytest.mark.parametrize("bad", [{"interval_ms": "rapido"}, {}, {"interval_ms": None}])
def test_a_period_that_is_not_a_number_is_refused(bad, tmp_path):
    h, _ = _harness(tmp_path)
    r = h.post("/period?d=d1", bad)
    assert r.status == 400
    assert r.json()["error"]


# -- notes, and the error shape that is not `_send_json` ----------------------

def test_a_group_checkbox_round_trips(tmp_path):
    h, d = _harness(tmp_path)
    assert h.post("/group-state?d=d1",
                  {"group_id": "g1", "checked": True}).status == 200
    # `checked` is a sorted LIST of the ticked ids, not a map of every id to a
    # boolean: the screen only ever asks which are on.
    assert h.get("/group-state?d=d1").json()["checked"] == ["g1"]


def test_a_note_round_trips(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.post("/note?d=d1",
                  {"scope": "relay", "html": "<p>oi</p>"}).status == 200
    assert "oi" in json.dumps(h.get("/note?d=d1").json())


def test_a_highlight_round_trips(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.post("/highlights?d=d1",
                  {"page": "TRIP", "item_id": "x1", "highlighted": True}).status == 200
    # Per page, a map of item id -> on. `group-state` next door answers a
    # sorted LIST for the same kind of question; the two payloads disagree in
    # shape and the contract records it rather than reconciling it.
    assert h.get("/highlights?d=d1").json()["pages"]["TRIP"] == {"x1": True}


@pytest.mark.parametrize("body,expected", [
    ({"scope": "nao-existe", "html": ""}, 400),
    ({"scope": "page", "html": ""}, 400),
])
def test_a_note_with_a_bad_scope_is_refused(body, expected, tmp_path):
    """`page` without a page id is refused rather than written to the relay
    scope, which would silently put the note on the wrong thing."""
    h, _ = _harness(tmp_path)
    r = h.post("/note?d=d1", body)
    assert r.status == expected


def test_a_group_state_missing_its_keys_is_refused(tmp_path):
    h, _ = _harness(tmp_path)
    assert h.post("/group-state?d=d1", {"checked": True}).status == 400


def test_the_notes_errors_are_json_written_by_hand(tmp_path):
    """Recorded, not repaired.

    These three routes do not use `_send_json`: they write the JSON as a string
    literal through `_send`. The payload is `{"error": ...}` either way, so no
    client can tell -- but a scan for the error convention will not find them,
    which is why the contract names the emission path and not only the shape.
    """
    h, _ = _harness(tmp_path)
    r = h.post("/group-state?d=d1", {"nada": 1})
    assert r.status == 400
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"error": "bad request"}


def test_a_note_over_the_ceiling_is_refused_with_413(tmp_path):
    """The one 413 in the GLV, and it is checked twice -- once on the
    `Content-Length` before the body is read, once on the decoded string."""
    h, _ = _harness(tmp_path)
    r = h.post("/note?d=d1", {"scope": "relay", "html": "x" * (300 * 1024)})
    assert r.status == 413


# -- choosing an RDB ----------------------------------------------------------

def test_selecting_a_file_that_is_not_in_the_project_is_404(tmp_path):
    """Every tool's `/select-*` says the same thing the same way: the file is
    addressed by content hash, and a hash the project does not hold is gone."""
    h = _empty(tmp_path)
    r = h.post("/select-rdb", {"sha256": "0" * 64})
    assert r.status == 404
    assert r.json()["error"]


def test_an_unknown_route_under_the_tool_is_plain_text(tmp_path):
    """Recorded oddity: the fall-through 404 of every mount is `text/plain`,
    never the `{"error": ...}` the rest of the tool answers with."""
    h = _empty(tmp_path)
    r = h.get("/nao-existe")
    assert r.status == 404
    assert r.headers["content-type"].startswith("text/plain")
