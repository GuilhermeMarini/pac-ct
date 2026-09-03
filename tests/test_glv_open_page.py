"""The tab remembers the page that was open.

Switching diagrams does not reload the page: the client fetches `/meta?d=`
and re-renders the page strip and the viewer with `meta.initial`. While
`initial` was always the GLE's initial page, whoever was on page 7 of a
12-page GLE came back to the first one on every trip between two relays -- and
the same happened after an F5. The open page is diagram state, which lives on
the server like the diagram list itself.
"""
from __future__ import annotations

import logging

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.transport import SCAN_TELNET

_LOG = logging.getLogger("test")


class _Defaults:
    poll_interval = 0.5
    mms_interval_ms = 100
    acc_password = "OTTER"
    setup_timeout = 60.0


def _diagram(pages=("Capa", "SCADA", "LEDS", "TRIP")) -> GlvDiagram:
    d = GlvDiagram(
        "d1", relay_name="RELE1", gle_name="GL1", gle_path=None,
        ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
        scan_mode=SCAN_TELNET,
    )
    # `build_diagram` builds both together: a safe id per page in
    # `pages_meta` and that page's SVG in `svgs`.
    d.pages_meta = [[p, p] for p in pages]
    d.svgs = {p: f"<svg id='{p}'/>" for p in pages}
    return d


# -- the default for someone who never opened anything ----------------------

def test_a_diagram_nobody_opened_yet_starts_on_the_second_page():
    """The first page of a QuickSet GLE is a cover/index in almost every file
    of the corpus. Behaviour unchanged -- it is what `initial` always did, now
    with a name."""
    d = _diagram()
    assert d.open_page == ""
    assert d.default_page() == "SCADA"
    assert d.meta(_Defaults())["initial"] == "SCADA"


def test_a_single_page_gle_opens_that_page():
    d = _diagram(pages=("Unica",))
    assert d.default_page() == "Unica"
    assert d.meta(_Defaults())["initial"] == "Unica"


def test_a_gle_with_no_page_at_all_answers_empty_instead_of_raising():
    d = _diagram(pages=())
    assert d.default_page() == ""
    assert d.meta(_Defaults())["initial"] == ""


# -- remembering ------------------------------------------------------------

def test_meta_comes_back_on_the_page_that_was_open():
    d = _diagram()
    d.remember_page("TRIP")
    assert d.meta(_Defaults())["initial"] == "TRIP"


def test_the_last_page_opened_is_the_one_remembered():
    d = _diagram()
    d.remember_page("LEDS")
    d.remember_page("TRIP")
    assert d.meta(_Defaults())["initial"] == "TRIP"


def test_the_first_page_can_be_remembered_too():
    """`open_page` cannot be falsy-tested against the initial one: the cover
    is a legitimate choice, and falling back to `default_page()` would return
    it as SCADA."""
    d = _diagram()
    d.remember_page("Capa")
    assert d.open_page == "Capa"
    assert d.meta(_Defaults())["initial"] == "Capa"


def test_two_diagrams_remember_their_own_page():
    """The memory is per diagram. Two tabs on the same relay, each on its own
    page, is exactly the case the tab strip exists to serve."""
    a, b = _diagram(), _diagram()
    a.remember_page("SCADA")
    b.remember_page("TRIP")
    assert a.meta(_Defaults())["initial"] == "SCADA"
    assert b.meta(_Defaults())["initial"] == "TRIP"


# -- what is NOT remembered -------------------------------------------------

def test_a_page_the_gle_does_not_have_is_not_remembered():
    """`remember_page` runs with what came in the URL of `/pages/<id>`. An id
    that is not a page of this GLE cannot become the next `initial`."""
    d = _diagram()
    d.remember_page("SCADA")
    d.remember_page("../../etc/passwd")
    d.remember_page("")
    assert d.open_page == "SCADA"
    assert d.meta(_Defaults())["initial"] == "SCADA"


def test_a_remembered_page_that_vanished_falls_back_to_the_default():
    """The diagram can be rebuilt over another GLE. Opening empty would be
    worse than opening on the initial page."""
    d = _diagram()
    d.remember_page("TRIP")
    d.pages_meta = [["Capa", "Capa"], ["OUTRA", "OUTRA"]]
    d.svgs = {"Capa": "<svg/>", "OUTRA": "<svg/>"}
    assert d.meta(_Defaults())["initial"] == "OUTRA"


# -- the route that records ------------------------------------------------
#
# `GET /pages/<safe_id>` is the ONLY path to the SVG, and both the page-strip
# click and the variable search's navigation travel through it. That is why
# the recording lives there, and not in a new endpoint: there is no way to
# open a page without fetching its drawing.

def _handler_with_diagram(tmp_path):
    """`(handler_instance_factory, diagram)` -- a real GLV with a three-page
    diagram, with no socket. Same pattern as
    `tests/test_glv_handler_scan_mode.py`: instance via `__new__` and `_send`
    swapped for a recorder."""
    from pacct.web.glv.handler import GlvDefaults, build_glv_handler
    from pacct.web.session import SessionManager

    sessions = SessionManager(root=tmp_path / "sessions", logger=_LOG)
    Handler = build_glv_handler(_LOG, sessions, GlvDefaults(port=23))
    sess, _ = sessions.resolve(None)

    d = _diagram(pages=("Capa", "SCADA", "LEDS"))
    # The tool state is created by the handler itself; take it from there.
    probe = Handler.__new__(Handler)
    probe.session = sess
    probe.mount_prefix = ""
    st = probe.sess()
    st.diagrams[d.id] = d
    st.order.append(d.id)
    st.active = d.id

    def request(path):
        h = Handler.__new__(Handler)
        h.session = sess
        h.mount_prefix = ""
        h.path = path
        h.headers = {}
        sent: list = []
        h._send = lambda code, body, ctype: sent.append((code, ctype))
        h._send_json = lambda code, data: sent.append((code, data))
        h.do_GET()
        return sent

    return request, d


def test_fetching_a_pages_svg_is_what_records_the_open_page(tmp_path):
    request, d = _handler_with_diagram(tmp_path)
    sent = request("/pages/LEDS?d=d1")
    assert sent and sent[0][0] == 200 and sent[0][1] == "image/svg+xml"
    assert d.open_page == "LEDS"
    assert d.meta()["initial"] == "LEDS"


def test_a_404_page_records_nothing(tmp_path):
    request, d = _handler_with_diagram(tmp_path)
    request("/pages/SCADA?d=d1")
    sent = request("/pages/NAO_EXISTE?d=d1")
    assert sent and sent[0][0] == 404
    assert d.open_page == "SCADA"
