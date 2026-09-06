"""`GET /pages/<id>?have=1` records the open page without resending the SVG.

The client caches a page's parsed SVG: `/pages/<id>` answers the same bytes
every time (the server renders every page when the diagram opens and keeps the
strings), so going back and forth between two pages was re-downloading and
re-parsing ~48 kB of SVG per switch.

What the cache must not break is the invariant the tab's memory rests on:
**`/pages/<id>` is the only path by which the server learns which page the
visitor opened** (`GlvDiagram.remember_page`, which is what makes a tab come
back to page 27 instead of page 2). That cannot quietly depend on whether the
browser happened to have the bytes. So the request still happens; `have=1`
only says "skip the body", and the answer is 204.
"""

from __future__ import annotations

import logging

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import SCAN_TELNET
from tests.web_harness import build

_LOG = logging.getLogger("test")


def _harness(tmp_path):
    h = build(build_glv_handler, tmp_path, GlvDefaults())
    d = GlvDiagram("d1", relay_name="RELE1", gle_name="GL1", gle_path=None,
                   ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
                   scan_mode=SCAN_TELNET)
    d.pages_meta = [["Capa", "Capa"], ["TRIP", "TRIP"], ["LEDS", "LEDS"]]
    d.svgs = {p: f"<svg id='{p}'/>" for p in ("Capa", "TRIP", "LEDS")}
    st = h.sessions.state(h.session, "glv", h.handler.state_factory)
    st.diagrams["d1"] = d
    st.order.append("d1")
    st.active = "d1"
    return h, d


def test_the_full_fetch_still_returns_the_svg(tmp_path):
    """The uncached path is untouched. Fails if `have` starts being read as
    truthy-when-absent and every first load comes back empty."""
    h, d = _harness(tmp_path)
    r = h.get("/pages/TRIP?d=d1")
    assert r.status == 200
    assert b"<svg id='TRIP'/>" in r.body


def test_have_answers_204_with_no_body(tmp_path):
    """Fails if the 48 kB comes back anyway -- the cache would then save the
    parse and not the transfer, which is most of what it is for."""
    h, d = _harness(tmp_path)
    r = h.get("/pages/TRIP?d=d1&have=1")
    assert r.status == 204
    assert not r.body


def test_have_still_records_the_open_page(tmp_path):
    """The point of keeping the request at all. Fails if `have=1` short-circuits
    before `remember_page` -- the tab would then forget where it was every time
    the visitor returned to a page the browser had cached, which is exactly the
    common case."""
    h, d = _harness(tmp_path)
    assert d.open_page == ""
    h.get("/pages/LEDS?d=d1&have=1")
    assert d.open_page == "LEDS"


def test_have_on_a_page_that_is_not_this_gles_is_still_refused(tmp_path):
    """The id comes off the URL, so the guard that stops a foreign id being
    remembered has to apply on this path too."""
    h, d = _harness(tmp_path)
    h.get("/pages/TRIP?d=d1&have=1")
    r = h.get("/pages/nao-existe?d=d1&have=1")
    assert r.status == 404
    assert d.open_page == "TRIP"
