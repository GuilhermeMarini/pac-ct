"""GLE connectors: the named network the drawing uses in place of a line.

A connector is NOT an edge in the XML. It is two or more
`<element type="Connector">` sharing one `<label>`, with nothing linking them:

    conn 658:  element 667     ->  Connector #723   <label>Cont1</label>
    conn 134:  Connector #206  ->  element 517      <label>Cont1</label>

Measured on the 418 `.gle` of `rdbs/extracted/`: 107 use a connector, 324
elements, 110 networks -- and **exactly one receiver per network, 110 of
110**, with fan-out up to 9 and 9 networks crossing pages. No versioned GLE
has a connector, which is why the fixture reproduces those shapes instead of
depending on an RDB.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sellib.gle import parse_gle

from pacct.web.glv import connectors

FIXTURE = Path(__file__).parent / "fixtures" / "connectors.gle.xml"


@pytest.fixture(scope="module")
def nets():
    return connectors.extract(parse_gle(FIXTURE))


@pytest.fixture(scope="module")
def svg():
    from sellib.gle import render_page
    return render_page(parse_gle(FIXTURE).findall(".//page")[0])


class TestTheNetItself:

    def test_a_label_becomes_one_net_with_one_receiver(self, nets):
        assert nets["Cont1"].receiver == "4"
        assert nets["Cont1"].emitters == ("5",)

    def test_the_driver_page_is_where_the_receiver_sits(self, nets):
        assert nets["LEDGRP"].driver_page == "P1"
        assert nets["LEDGRP"].pages == ("P1", "P2_LEDs")

    def test_a_net_can_fan_out_to_several_emitters(self, nets):
        """Medido: 12 redes do corpus tem 9 emissores."""
        assert nets["LEDGRP"].emitters == ("14", "15")

    def test_a_label_with_no_receiver_is_not_a_net(self, nets):
        """Guessing which end drives it would paint a line on a hunch."""
        assert "SEMFONTE" not in nets


class TestTheEquation:

    def test_it_reads_back_in_selogic(self, nets):
        """`*` AND, `+` OR, `!` NOT -- the AcSELerator notation. Measured: no
        arithmetic block appears inside a connector equation anywhere in the
        corpus, so `*` and `+` do not collide with ADD/MULT."""
        assert nets["Cont1"].equation == "(IN101 * !IN102)"

    def test_the_leaf_bits_are_what_the_poll_has_to_ask_for(self, nets):
        assert nets["Cont1"].bits == frozenset({"IN101", "IN102"})
        assert nets["LEDGRP"].bits == frozenset({"GROUNDT", "TRIPS"})

    def test_an_or_reads_back_with_plus(self, nets):
        assert nets["LEDGRP"].equation == "(GROUNDT + TRIPS)"

    def test_the_tree_is_json_shaped_for_the_client(self, nets):
        """Python extracts STRUCTURE; the JS decides SEMANTICS. That is why the
        tree travels as data, and not as an already evaluated value -- a
        second implementation of NOT/RTRIG/latch in Python is exactly the kind
        of copy this repository has already seen diverge."""
        import json
        t = nets["Cont1"].tree
        assert json.loads(json.dumps(t)) == t
        assert t["op"] == "AND"
        assert t["args"][0] == {"op": "BIT", "name": "IN101"}
        assert t["args"][1] == {"op": "NOT",
                                "args": [{"op": "BIT", "name": "IN102"}]}


class TestItTerminates:

    def test_a_feedback_loop_ends_instead_of_hanging(self, nets):
        """An extractor that goes into a loop hangs the whole `build_diagram`.
        The corpus has no cycle at all (0 of 110), but latch feedback would."""
        assert "LACO" in nets
        assert "IN109" in nets["LACO"].bits

    def test_the_cut_is_visible_instead_of_invented(self, nets):
        """A visibly incomplete equation is honest; an invented one is not."""
        assert "…" in nets["LACO"].equation


class TestConstants:
    """A literal from the drawing is a value, not a bit and not a cut.

    Measured: the only cut left in the whole corpus was a counter's preset --
    `PCN((PLT16 * ↓PCT17Q), …, (↑PSV12 + PCN01Q + PSV60))`, where the `…` was
    the constant `6` on the middle port. A constant does not exist in the
    Relay Word (`collect_bit_names` already excludes it), so it does not enter
    the polling -- but showing `…` in its place says the equation is
    incomplete when it is not.
    """

    def test_a_literal_reads_back_as_its_value(self, nets):
        assert nets["PRESET"].equation == "(IN110 * 6)"

    def test_a_literal_is_not_a_bit_to_poll(self, nets):
        assert nets["PRESET"].bits == frozenset({"IN110"})

    def test_the_client_gets_it_as_a_number(self, nets):
        assert nets["PRESET"].tree["args"][1] == {"op": "CONST", "value": 6.0}


# -- the drawing: the connector has to say its own name ----------------------

class TestRendering:
    """Without the label in the SVG there is no pairing key -- nor any way for
    a person to see that the two ends are the same network.

    The connector came out as a box with `→` because `render_gate` gets its
    `info` from `element_info`, which reads the name from `<logic_element>`; a
    Connector has no `logic_element` at all, the `<label>` is a direct child
    of the `<element>`.
    """

    def test_the_group_carries_the_label_as_the_pairing_key(self, svg):
        assert 'id="el-4" data-type="Connector" data-connector="Cont1"' in svg
        assert 'id="el-5" data-type="Connector" data-connector="Cont1"' in svg

    def test_the_box_shows_the_name_instead_of_an_anonymous_arrow(self, svg):
        assert ">Cont1<" in svg
        assert ">LEDGRP<" in svg

    def test_a_label_with_xml_special_characters_is_escaped(self):
        """The real labels are free-form -- `TC - LOP`, `RX 50BF C/ BARRAS
        UNIDAS`. An `&` in a label must not break the SVG."""
        import xml.etree.ElementTree as ET

        from sellib.gle import element_info, render_element
        el = ET.fromstring(
            '<element id="9" type="Connector" left="0" top="0">'
            '<label>A &amp; B</label></element>')
        out = render_element(element_info(el), 1, 1)
        assert 'data-connector="A &amp; B"' in out
        assert ">A &amp; B<" in out


# -- the diagram: the network has to reach the polling and the client -------

def _diagram():
    """A `GlvDiagram` from the fixture, without touching the network
    (build_diagram does not touch it).

    `relay_model=None` on purpose: the fixture belongs to no model, and the
    path without a model has to work -- it is what happens with a relay whose
    profile nobody has written yet.
    """
    import logging

    from pacct.web.glv.diagram import build_diagram
    return build_diagram("d1", FIXTURE, "REL", "conn.gle", "192.0.2.10", 23,
                         None, logging.getLogger("test"))


class TestTheDiagramCarriesTheNets:

    def test_build_extracts_them_without_touching_the_network(self):
        d = _diagram()
        assert set(d.connectors) == {"Cont1", "LEDGRP", "LACO", "PRESET"}

    def test_the_open_page_polls_the_bits_that_drive_its_connectors(self):
        """`LEDGRP` is driven on P1 and derived on P2. Opening P2 has to ask
        for GROUNDT and TRIPS, which are not drawn on P2 -- without that the
        connector stays indeterminate forever, which is the `LEDGROUND` case
        measured on the corpus (9 networks crossing pages)."""
        d = _diagram()
        d.values("P2_LEDs")
        assert {"GROUNDT", "TRIPS"} <= d.idle.wanted_bits

    def test_the_page_bits_are_still_there_alongside_them(self):
        d = _diagram()
        d.values("P2_LEDs")
        assert {"LED05", "LED06"} <= d.idle.wanted_bits

    def test_the_driving_bits_were_already_in_the_map_request(self):
        """It does not change `ensure_bits`: the bits already enter
        `all_wanted_bits` because they are in the GLE, on some page. What was
        missing was the open-page filter narrowing them back down."""
        d = _diagram()
        assert {"GROUNDT", "TRIPS"} <= d.all_wanted_bits

    def test_values_hands_the_page_connectors_to_the_client(self):
        d = _diagram()
        payload = d.values("P2_LEDs")
        assert set(payload["connectors"]) == {"LEDGRP"}
        net = payload["connectors"]["LEDGRP"]
        assert net["equation"] == "(GROUNDT + TRIPS)"
        assert net["tree"]["op"] == "OR"
        assert sorted(net["bits"]) == ["GROUNDT", "TRIPS"]

    def test_a_page_with_no_connector_says_so_with_an_empty_dict(self):
        d = _diagram()
        d.connectors = {}
        assert d.values("P1")["connectors"] == {}
