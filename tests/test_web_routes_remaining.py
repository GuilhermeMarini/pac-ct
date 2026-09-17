"""The last nine route branches that nothing executed.

Measured by tracing the whole suite rather than by grepping it for paths: 71 of
the 80 route branches ran, and these nine did not. They are not a theme -- they
are what is left after a tool's obvious routes get covered by the tests written
while building it: the second file-download, a registry read, two destructive
edits, and the three round-trips that take back a spreadsheet or a zip the tool
itself produced.

**Three of them are covered at their refusal and not at their success**, and
that is stated rather than hidden. `dnp_map POST /export`, `gle_exporter POST
/import` and `vb_updater POST /import-descriptions` all end in writing a real
Compound File or parsing a real `.xlsx`; driving the success path needs a
genuine RDB, which this suite deliberately does not carry (`fake_rdb` writes a
placeholder, and `tests/test_rdb_write.py` is where the writer is tested
against real bytes). What is pinned here is the contract at the boundary: what
each answers when the input is missing or wrong, which is the half a client
actually has to handle.
"""

from __future__ import annotations

import logging

import pytest

from pacct.web.dashboard import build_home_handler
from pacct.web.dnp_map.handler import build_dnp_map_handler
from pacct.web.gle_exporter.handler import build_gle_exporter_handler
from pacct.web.vb_updater.handler import build_vb_updater_handler
from tests.dnp_fixtures import SAMPLE_411L
from tests.web_harness import build, fake_rdb

_LOG = logging.getLogger("test")

# A DNP session is a FILE, not a section: `SET_D1.TXT` is session D1 and
# `SET_D2.TXT` is D2, which is what `RelayEntry.sessions` enumerates. A second
# `[D2]` block inside one file is not a second session and `/copy-session`
# would find nothing to copy to -- worth stating, because the `[D1]` header
# inside the file makes the other reading look right.
_EMPTY_SESSION = SAMPLE_411L.replace(b'BI_1,"PSV22"', b'BI_1,""')


def _dnp(tmp_path, files=None):
    h = build(build_dnp_map_handler, tmp_path)
    info = fake_rdb(tmp_path,
                    {"QPC1_TR1": files or {"SET_D1.TXT": SAMPLE_411L}})
    h.add_rdb(info)
    return h, info, info.sha256[:12]


def _dnp_two_sessions(tmp_path):
    return _dnp(tmp_path, {"SET_D1.TXT": SAMPLE_411L,
                           "SET_D2.TXT": _EMPTY_SESSION})


# -- dashboard: the liveness sentinel -----------------------------------------

def test_the_home_reports_itself_up(tmp_path):
    """One of five `*-state` sentinels the menu polls to grey out a dead tool.

    This one is the odd member: the other four go through `_send_json`, and the
    home writes the same two bytes as a string literal through `_send`. No
    client can tell, and the contract records the difference rather than
    repairing it.
    """
    # The home takes a logger and no `sessions`: it is the one mount with no
    # state of its own, so the harness's factory call needs adapting.
    h = build(lambda logger, sessions: build_home_handler(logger), tmp_path)
    r = h.get("/home-state")
    assert r.status == 200
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"ok": True}


# -- dnp_map: the registry read -----------------------------------------------

def test_the_wordbit_models_are_listed_without_a_session(tmp_path):
    """Process-wide reference data, not the visitor's: a model's name domain
    is worth the same to the next engineer. The route reads no session state,
    which is why it answers before any RDB has been chosen.
    """
    h, _, _ = _dnp(tmp_path)
    body = h.get("/wordbits").json()
    assert body["ok"] is True
    assert isinstance(body["models"], list)


# -- dnp_map: the sandboxed download ------------------------------------------

@pytest.mark.parametrize("query", ["", "?f=/etc/passwd", "?f=../../etc/passwd",
                                   "?file=/etc/passwd"])
def test_every_way_of_getting_this_download_wrong_answers_403(query, tmp_path):
    """Two findings of the phase, and both are recorded rather than repaired.

    **The parameter is `?f=`, not `?file=`.** `gle_exporter` and `vb_updater`
    spell the same idea `?file=`; `dnp_map` and `gle_tabs` spell it `?f=`. A
    client written against one pair and pointed at the other gets a 403 that
    says nothing about why -- which is the second finding: this route collapses
    "you sent no parameter", "that path is outside the sandbox" and "no such
    file" into one 403, where `gle_exporter` and `vb_updater` separate them into
    400, 403 and 404. Hence `?file=` below: passing the OTHER tool's parameter
    name is indistinguishable from an attempted escape.

    Neither is a security hole -- the refusal is the safe direction in every
    case -- which is exactly why it survived: nothing ever failed loudly.
    """
    h, _, _ = _dnp(tmp_path)
    r = h.get("/download" + query)
    assert r.status == 403
    # The download routes answer text/plain, never the `{"error": ...}` the
    # rest of the tool uses.
    assert r.headers["content-type"].startswith("text/plain")


# -- dnp_map: swap -------------------------------------------------------------

def test_two_points_exchange_their_values(tmp_path):
    """The drag, over HTTP. `BI_1` holds `PSV22` and `BI_2` is free."""
    h, _, short = _dnp(tmp_path)
    r = h.post("/swap", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                         "a": "BI_1", "b": "BI_2"})
    assert r.status == 200
    assert r.json()["ok"] is True
    blocks = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D1").json()["blocks"]
    values = {p["key"]: p["value"] for p in blocks["BI"]}
    assert values["BI_1"] == ""
    assert values["BI_2"] == "PSV22"


def test_a_swap_in_a_session_that_does_not_exist_is_404(tmp_path):
    h, _, short = _dnp(tmp_path)
    r = h.post("/swap", {"rdb": short, "relay": "QPC1_TR1", "session": "D9",
                         "a": "BI_1", "b": "BI_2"})
    assert r.status == 404
    assert r.json() == {"ok": False, "error": "Sessão inexistente."}


def test_what_configures_the_session_may_not_be_swapped_into_the_map(tmp_path):
    """`MINDIST` configures the DNP session; it is not a point.

    The guard is `point_keys()`, and what it admits is wider than "points": it
    includes each point's `AI_SCA*`/`AI_DBD*` columns, because those ARE
    editable in the map and travel with the quantity they scale. What it
    excludes is everything `extras()` reports -- the session's own
    configuration, shown read-only.
    """
    h, _, short = _dnp(tmp_path)
    r = h.post("/swap", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                         "a": "BI_1", "b": "MINDIST"})
    assert r.status == 400
    assert r.json()["ok"] is False


# -- dnp_map: copy between the sessions of one relay ---------------------------

def test_a_session_map_copies_onto_the_others(tmp_path):
    """`copy_session` is `copy_map_to` with source and destination RDB equal."""
    h, _, short = _dnp_two_sessions(tmp_path)
    r = h.post("/copy-session", {"rdb": short, "relay": "QPC1_TR1",
                                 "session": "D1"})
    assert r.status == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sessions"] == ["D2"]
    blocks = h.get(f"/map?rdb={short}&relay=QPC1_TR1&d=D2").json()["blocks"]
    assert {p["key"]: p["value"] for p in blocks["BI"]}["BI_1"] == "PSV22"


def test_copying_from_a_session_that_does_not_exist_is_404(tmp_path):
    h, _, short = _dnp(tmp_path)
    r = h.post("/copy-session", {"rdb": short, "relay": "QPC1_TR1",
                                 "session": "D9"})
    assert r.status == 404
    assert r.json()["ok"] is False


def test_a_relay_with_one_session_has_nothing_to_copy_to(tmp_path):
    """Answered 200 with an empty target list rather than refused: nothing
    went wrong, there is simply nowhere to copy."""
    h, _, short = _dnp(tmp_path)
    body = h.post("/copy-session", {"rdb": short, "relay": "QPC1_TR1",
                                    "session": "D1"}).json()
    assert body["ok"] is True
    assert body["sessions"] == []


# -- dnp_map: import a device profile ------------------------------------------

def test_an_empty_device_profile_is_refused(tmp_path):
    """The one upload that is not a project input: a profile describes a relay
    MODEL and lands in the packaged registry, not in the visitor's session.

    Only the refusal is driven. The success path rewrites process-wide
    reference data that every later test would then see, and a test that
    mutates a shared registry is worse than an untested branch.
    """
    h, _, _ = _dnp(tmp_path)
    r = h.post("/import-profile", b"")
    assert r.status == 400
    assert r.json()["ok"] is False


# -- the three round-trips, at their refusal -----------------------------------

def test_exporting_an_rdb_that_is_not_a_real_compound_file_fails_honestly(tmp_path):
    """A failed export must never become a half-written RDB in the library.

    `fake_rdb` writes a placeholder where the OLE container would be, so this
    drives the path where the writer refuses. What matters for the contract is
    that the refusal is a status and a message, not a torn response.
    """
    h, _, short = _dnp(tmp_path)
    h.post("/edit", {"rdb": short, "relay": "QPC1_TR1", "session": "D1",
                     "changes": {"BI_1": "PSV33"}})
    r = h.post("/export", {"rdb": short})
    assert r.status >= 400
    assert r.json()["ok"] is False
    assert r.json()["error"]


def test_importing_comments_with_no_rdb_chosen_is_refused(tmp_path):
    """`gle_exporter POST /import` takes back the spreadsheet the tool wrote.

    With no RDB selected there is nothing to write into, and the tool says so
    rather than accepting the upload and failing later.
    """
    h = build(build_gle_exporter_handler, tmp_path)
    r = h.post("/import", b"")
    assert r.status >= 400
    assert r.json()["error"]


def test_importing_descriptions_with_no_scd_chosen_is_refused(tmp_path):
    """The VB Updater's half of the same idea, and the same refusal."""
    h = build(build_vb_updater_handler, tmp_path)
    r = h.post("/import-descriptions", b"")
    assert r.status >= 400
    assert r.json()["error"]


# -- the property all three refusals share -------------------------------------

@pytest.mark.parametrize("factory,route", [
    (build_gle_exporter_handler, "/import"),
    (build_vb_updater_handler, "/import-descriptions"),
])
def test_a_round_trip_refusal_never_publishes_a_file(factory, route, tmp_path):
    """The library must not gain an entry from a request that failed.

    `publish_output` is what puts a tool's output into the project, and it runs
    only after the write succeeded. A refusal that published would put a file
    nothing distinguishes from a complete one in front of the next tool.
    """
    h = build(factory, tmp_path)
    before = len(h.library().list())
    h.post(route, b"")
    assert len(h.library().list()) == before


# -- the defect this phase found, pinned rather than fixed ---------------------

def test_a_relay_ref_without_its_keys_tears_the_connection(tmp_path):
    """`POST /settings-compare/groups` answers NOTHING when a ref is short.

    This is a characterisation test: it asserts what the application does
    today, and it is meant to fail the day somebody fixes it, deliberately.
    B12 describes; repairing this is a phase with its own reason.

    `settings_compare/handler.py:122` builds its pairs with
    `[(r["rdb_key"], r["relay_name"]) for r in refs]` -- a subscript, on data
    that came from the request body. A ref missing either key raises
    `KeyError` out of `do_POST`, and nothing above it in `http.server` turns an
    exception into a response: `handle_error` logs a traceback and the socket
    is closed. The client does not get a 400, or a 500; it gets
    `RemoteDisconnected`, which it cannot tell from the server dying.

    Measured to be the ONLY one of its kind: every other route that subscripts
    client-supplied data -- `glv`'s `/diagrams`, `/diagrams/batch`,
    `/group-state`, `/highlights` -- wraps it in `except (..., KeyError, ...)`
    and answers 400. This one route was missed.

    It is asserted over a real socket and not through `web_harness`, because
    the two environments disagree about what the failure IS: in the harness the
    exception simply propagates into the test, which looks like a test error
    rather than a contract. Only a socket shows the client's side of it.
    """
    import http.client
    import json as _json
    import logging as _logging

    from pacct.web.mount import Mount
    from pacct.web.settings_compare.handler import build_settings_compare_handler
    from tests.dispatcher_harness import serve, sessions_for

    s = sessions_for(tmp_path)
    mount = Mount("/settings-compare",
                  build_settings_compare_handler(_logging.getLogger("test"), s),
                  "Comparador")
    with serve([mount], s) as c:
        with pytest.raises(http.client.RemoteDisconnected):
            c.post("/settings-compare/groups",
                   body=_json.dumps({"relays": [{"rdb_key": "x"}]}).encode(),
                   headers={"Content-Type": "application/json"})


def test_the_same_route_answers_properly_when_the_body_is_well_formed(tmp_path):
    """The guard that IS there works: a body that is not JSON gets a 400.

    Which is what makes the case above a gap rather than a policy -- the route
    already knows how to refuse, and one path around the refusal was missed.
    """
    from pacct.web.settings_compare.handler import build_settings_compare_handler

    h = build(build_settings_compare_handler, tmp_path)
    r = h.post("/groups", b"nao e json")
    assert r.status == 400
    assert r.json()["error"]
