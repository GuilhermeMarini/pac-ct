"""`GET /vb-source?d=` hands the layer the whole VB map at once.

The layer is re-applied on every page switch -- the client caches a page's
parsed SVG, so switching pages is otherwise free -- and the map behind it costs
a 368 ms parse of a 22 MB SCD. Asking per page would put that parse behind a
click; asking once per diagram and keeping it is what makes the toggle
instant the second time.

One answer serves a live diagram and a dead one. The connection changes only
how the browser draws the map -- live, the block fill is the bit state, so
only the signature and the hover card come out -- and it reads that off the
tab strip it already polls.
"""

from __future__ import annotations

import logging

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import SCAN_TELNET
from tests import gle_fixtures as fx
from tests.web_harness import build

_LOG = logging.getLogger("test")

_IED = "QPC1_LT2_UPC1"
_SCD = fx.scd(
    fx.ied(
        _IED,
        fx.extref_signal("VB003", "50/62BF LT2 UPC2", ied="PUB",
                         prefix="PSV", ln_inst="1", do="Ind05"),
        fx.rx_status_extref("VB101", "FALHA GOOSE", cb="GoSB01"),
        fx.extref_placeholder("VB050"),
        private=fx.goose_subscriptions(("GoSB01", "VB101")),
    ),
    fx.saddr_ied("PUB", fx.saddr_point("PSV05", prefix="PSV", ln_inst="1",
                                       do="Ind05")),
    ips={_IED: "192.0.2.10"},
)


def _harness(tmp_path, *, with_scd: bool = True):
    h = build(build_glv_handler, tmp_path, GlvDefaults())
    scd_path = None
    if with_scd:
        scd_path = tmp_path / "9f0c1a2b3c4d.scd"
        scd_path.write_bytes(_SCD)
    d = GlvDiagram("d1", relay_name=_IED, gle_name="GL1", gle_path=None,
                   ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
                   scan_mode=SCAN_TELNET, scd_path=scd_path,
                   scd_name="subestação.scd" if with_scd else "")
    st = h.sessions.state(h.session, "glv", h.handler.state_factory)
    st.diagrams["d1"] = d
    st.order.append("d1")
    st.active = "d1"
    return h, d


def test_the_route_answers_the_whole_map_with_its_census(tmp_path):
    """One trip, every VB, plus the three counts the audit tint reports."""
    h, _ = _harness(tmp_path)
    r = h.get("/vb-source?d=d1")
    assert r.status == 200
    data = r.json()
    assert data["ied"] == _IED
    assert data["matched_by"] == "IP"
    assert data["census"] == {"signal": 1, "quality": 1, "placeholder": 1}
    assert data["sources"]["VB003"]["bit"] == "PSV05"
    assert data["sources"]["VB101"]["kind"] == "quality"


def test_the_scd_is_named_the_way_it_was_uploaded(tmp_path):
    """The library stores an SCD as `<sha12>.scd`. Fails if the panel header
    starts naming the file by its hash -- the same trap `st.scd_name` exists
    for in the VB Updater."""
    h, _ = _harness(tmp_path)
    assert h.get("/vb-source?d=d1").json()["scd"] == "subestação.scd"


def test_the_map_is_read_once_and_kept(tmp_path):
    """Fails if the parse moves onto the request. The layer is re-applied on
    every page switch, and a 22 MB SCD parsed per switch is a click that
    stops being instant."""
    h, d = _harness(tmp_path)
    h.get("/vb-source?d=d1")
    first = d._vb_sources
    assert first is not None
    # Deleting the file the map came from proves the second answer never
    # touched it again.
    d.scd_path.unlink()
    assert h.get("/vb-source?d=d1").json()["sources"]["VB003"]["bit"] == "PSV05"
    assert d._vb_sources is first


def test_a_diagram_with_no_scd_says_so_instead_of_failing(tmp_path):
    """Opening a diagram without choosing an SCD is an ordinary thing to do.
    Fails if that answers 500, or an empty map with no reason -- the panel has
    to tell the difference between "nothing to read" and "read nothing"."""
    h, _ = _harness(tmp_path, with_scd=False)
    r = h.get("/vb-source?d=d1")
    assert r.status == 200
    data = r.json()
    assert data["sources"] == {}
    assert "nenhum SCD" in data["error"]


def test_the_answer_does_not_depend_on_the_link(tmp_path):
    """The map is read from a file; whether the relay is being read has
    nothing to do with it. Fails if the route grows a connection-dependent
    field again -- the drawing mode is the browser's decision, taken from the
    tab strip, not something to re-derive per request."""
    h, _ = _harness(tmp_path)
    assert "connected" not in h.get("/vb-source?d=d1").json()


def test_an_unknown_diagram_is_a_404(tmp_path):
    """Same contract as every other `?d=` route."""
    h, _ = _harness(tmp_path)
    assert h.get("/vb-source?d=nao-existe").status == 404
