"""What the GLE-to-SVG renderer draws, and which Relay Word bit each block owns.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: `parsers/gle.py` draws every diagram the GLV shows, and it
draws them from geometry the GLE only implies -- port spacing, the header row a
latch needs for its label, the width a symbol must have for its wire to land on
the box. Nothing downstream validates the result. A block drawn 12 px too short
does not fail; it puts the wire on the wrong pin, and an engineer reads a
signal off the wrong input.

The second half is `data-output-bit`, which is the *contract with the relay*:
it names the Relay Word bit whose live value colours the block. `PLT04` never
carries a `Q` and `PCT03` always does; `_SV01` becomes `SV01T` and `_SC01`
becomes `SC01QU`. Getting one of those wrong asks the relay for a bit that does
not exist, and the block stays grey forever with no error anywhere.

Two kinds of test here:

* a **golden file** (`fixtures/render_page.svg`) rendered from a trimmed,
  hand-built `fixtures/render_page.gle.xml`. Regenerate with
  `SEL_UPDATE_GOLDEN=1 pytest tests/test_gle_render.py` and READ THE DIFF --
  a golden nobody looks at is a rubber stamp. The fixture is deliberately
  ~10 KB, not the 1 MB `samples/GL1.gle.xml`: a golden for that is
  unreviewable;
* narrow unit tests for the rules the golden merely *contains*, so a failure
  says which rule broke rather than 'the SVG changed'.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from selfiles import gle
from selfiles.models import relay_models

FIXTURES = Path(__file__).parent / "fixtures"
GLE_FIXTURE = FIXTURES / "render_page.gle.xml"
GOLDEN = FIXTURES / "render_page.svg"

TITLE_PREFIX = "QPC1_TR1_UPC1 / "


@pytest.fixture(scope="module")
def page() -> ET.Element:
    return gle.parse_gle(GLE_FIXTURE).findall(".//page")[0]


def _info(**kw) -> dict:
    """An `element_info` dict with every key the renderers read."""
    base = dict(id="1", type="AND", left=0, top=0, name="", alias="",
                input_mods={}, output_mods={},
                input_comments={}, output_comments={})
    base.update(kw)
    return base


def _output_bit(svg: str) -> str | None:
    m = re.search(r'data-output-bit="([^"]*)"', svg)
    return m.group(1) if m else None


def _page_from(xml: str) -> ET.Element:
    return ET.fromstring(xml).findall(".//page")[0]


# -----------------------------------------------------------------------------
# The golden file
# -----------------------------------------------------------------------------

class TestGolden:

    def test_the_rendered_page_matches_the_golden_svg(self, page):
        """The whole renderer, end to end, on one small page: two symbols in a
        labelled group, a const, a Text element in an unlabelled group, an AND
        with a NOT input, an OR with a NOT output, a PLT, a PCNDTIMER, nine
        connection polylines, two junction dots, port comments on both sides
        and an RTRIG arrow.

        Fails on ANY change to the emitted geometry, class names, data
        attributes or the embedded stylesheet. That is the point: everything
        downstream -- the GLV's live colouring, the highlighter, the search
        overlay, the group checkboxes -- selects on those names from
        JavaScript, where a rename is silent.

        `SEL_UPDATE_GOLDEN=1` rewrites the file; read the diff before
        committing it."""
        svg = gle.render_page(page, title_prefix=TITLE_PREFIX)
        if os.environ.get("SEL_UPDATE_GOLDEN"):
            GOLDEN.write_text(svg, encoding="utf-8")
        assert svg == GOLDEN.read_text(encoding="utf-8")

    def test_the_golden_is_well_formed_xml(self, page):
        """An SVG the browser refuses is worse than an ugly one, and string
        concatenation has no parser behind it. Fails the moment a renderer
        emits an unbalanced tag or an unescaped attribute."""
        ET.fromstring(GOLDEN.read_text(encoding="utf-8"))

    def test_the_viewbox_grows_left_for_a_long_input_comment(self, page):
        """`min_x` goes NEGATIVE when a port comment on the left side of a
        symbol overflows past x=0 -- here a 32-character comment on a symbol at
        left=60. Fails if `page_bounds` stops subtracting
        `_estimate_comment_width`, and the comment is then clipped off the
        canvas."""
        svg = gle.render_page(page)
        assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" '
                              'viewBox="-66 0 662 326"')

    def test_the_title_prefix_is_escaped_into_the_svg_title(self, page):
        """The `<title>` is what the browser shows on hover and what a saved
        SVG is named by. Fails if the prefix stops being joined or stops being
        escaped."""
        svg = gle.render_page(page, title_prefix=TITLE_PREFIX)
        assert "<title>QPC1_TR1_UPC1 / PROTECAO</title>" in svg


class TestParseGle:

    def test_the_declaration_says_utf8_and_the_bytes_are_latin1(self):
        """QuickSet writes latin-1 under a `encoding="utf-8"` header. The
        fixture's `PARTIDA INSTANTÂNEA` is a single 0xC2 byte, which is not
        valid UTF-8 on its own.

        Fails if `parse_gle` stops swapping the declared encoding or stops
        decoding latin-1 -- `ET.parse` would raise on every real GLE that
        contains one accented Portuguese word, which is all of them."""
        assert b"\xc2NEA" in GLE_FIXTURE.read_bytes()
        root = gle.parse_gle(GLE_FIXTURE)
        comments = [c.text for c in root.iter("comment") if c.text]
        assert "PARTIDA INSTANTÂNEA" in comments

    def test_the_accented_comment_survives_into_the_svg(self, page):
        """Fails the same way, one layer later: the SVG is written UTF-8, so a
        latin-1 byte that survived the parse must not be re-encoded twice."""
        assert "PARTIDA INSTANTÂNEA" in gle.render_page(page)


# -----------------------------------------------------------------------------
# data-output-bit: which Relay Word bit each block owns
# -----------------------------------------------------------------------------

class TestOutputBitConventions:
    """The SEL naming rules, per family. docs/ENGINEERING-NOTES.md states them; this is where
    they are enforced. Every case below fails if the suffix rule for that block
    changes -- and the failure mode in production is a block that silently
    never lights up, because the relay simply has no bit by that name."""

    def test_a_4xx_protection_latch_drops_the_underscore_and_takes_no_q(self):
        """`_PLT04` -> `PLT04`. A latch NEVER carries a `Q`. Fails if the
        `lstrip("_")` goes, or if a `Q` is appended by symmetry with the
        timer."""
        assert _output_bit(gle.render_plt(_info(type="PLT", name="_PLT04"),
                                          2, 1)) == "PLT04"

    def test_a_4xx_automation_latch_follows_the_same_rule(self):
        """`_ALT01` -> `ALT01`. This is the one the 411L profile got wrong by
        copying `ALT##Q` from the timer, asking for a bit that does not
        exist."""
        assert _output_bit(gle.render_alt(_info(type="ALT", name="_ALT01"),
                                          2, 1)) == "ALT01"

    def test_a_4xx_timer_takes_a_q_and_keeps_its_name_as_written(self):
        """`PCT03` -> `PCT03Q`. A timer ALWAYS carries a `Q`, and the GLE
        writes the timer's name without a leading underscore. Fails if the
        suffix goes or if `lstrip("_")` is applied here too (harmless today,
        wrong the day a GLE writes `_PCT03`)."""
        assert _output_bit(gle.render_pcndtimer(_info(type="PCNDTIMER",
                                                      name="PCT03"),
                                                3, 1)) == "PCT03Q"

    def test_a_4xx_counter_takes_a_q(self):
        """`PCN03` -> `PCN03Q`. Fails if the counter is given the latch rule."""
        assert _output_bit(gle.render_pcn(_info(type="PCN", name="PCN03"),
                                          3, 2)) == "PCN03Q"

    def test_a_4xx_automation_set_reset_takes_a_q(self):
        """`AST01` -> `AST01Q`. Fails the same way."""
        assert _output_bit(gle.render_ast(_info(type="AST", name="AST01"),
                                          3, 2)) == "AST01Q"

    def test_a_7xx_latch_has_no_suffix(self):
        """`_LT01` -> `LT01`. The 7xx SELOGIC family writes `LATCH`/`TIMER`/
        `COUNTER` as the XML type; the 4xx writes `PLT`/`PCNDTIMER`/`PCN`.
        Fails if the two dialects are merged into one renderer."""
        assert _output_bit(gle.render_latch_7xx(_info(type="LATCH",
                                                      name="_LT01"),
                                                2, 1)) == "LT01"

    def test_a_7xx_timer_takes_a_trailing_t(self):
        """`_SV01` -> `SV01T`: the delayed output. Bare `SV01` is the
        PRE-timer input, so dropping the `T` would colour the block with the
        value the timer has not applied its delay to yet -- a wrong reading
        that looks perfectly plausible."""
        assert _output_bit(gle.render_timer_7xx(_info(type="TIMER",
                                                      name="_SV01"),
                                                3, 1)) == "SV01T"

    def test_a_7xx_counter_takes_qu_not_qd(self):
        """`_SC01` -> `SC01QU`, the count-UP output. `SC01QD` exists and is not
        the default. Fails if the suffix changes."""
        assert _output_bit(gle.render_counter_7xx(_info(type="COUNTER",
                                                        name="_SC01"),
                                                  3, 1)) == "SC01QU"

    def test_an_unnamed_block_declares_no_output_bit_at_all(self):
        """A block with no `physical_instance_name` (an AND, or a latch the
        engineer never named) must emit NO attribute rather than an empty one.
        Fails if the `if output_bit` guard goes -- the poller would then be
        handed `""` as a bit name to look up."""
        svg = gle.render_plt(_info(type="PLT", name=""), 2, 1)
        assert "data-output-bit" not in svg
        assert '<text class="gate-label" x="27.0" y="10">PLT</text>' in svg

    def test_a_plain_gate_carries_its_type_but_no_output_bit(self):
        """AND/OR/NOT have no Relay Word bit of their own. Fails if
        `render_element` starts passing one."""
        svg = gle.render_element(_info(type="AND"), 2, 1)
        assert 'data-type="AND"' in svg
        assert "data-output-bit" not in svg


# -----------------------------------------------------------------------------
# Symbols
# -----------------------------------------------------------------------------

class TestSymbols:

    def test_a_named_symbol_carries_the_bit_the_poller_reads(self):
        """`data-bit` is the selector the GLV's JavaScript colours by. Fails if
        the attribute is renamed -- the drawing would render perfectly and
        never change colour."""
        assert 'data-bit="IN101"' in gle.render_symbol(
            _info(type="SYMBOL", name="IN101"))

    @pytest.mark.parametrize("name,is_const", [
        ("600", True), ("0", True), ("1", True), ("-5", True), ("2.5", True),
        ("IN101", False), ("50P1", False), ("3I2", False), ("", False),
    ])
    def test_a_numeric_symbol_name_is_a_constant_not_a_bit(self, name, is_const):
        """A SYMBOL whose name is a number is a SETPOINT feeding a timer or a
        comparator, not a Relay Word bit. Asking the relay for a bit called
        `600` costs a round trip and always answers 'not findable'.

        Fails if `INT_LITERAL_RE` loosens (`3I2` is a real analog name and must
        stay a bit) or tightens (`2.5` is a real pickup value)."""
        assert gle.is_const_symbol_name(name) is is_const

    def test_a_constant_gets_data_const_and_never_data_bit(self):
        """Fails if the const branch stops shadowing the bit branch -- the
        poller would add `600` to its discovery list."""
        svg = gle.render_symbol(_info(type="SYMBOL", name="600"))
        assert 'data-const="600"' in svg
        assert "data-bit=" not in svg
        assert 'class="element-symbol const"' in svg

    def test_an_analog_symbol_gets_a_value_placeholder(self):
        """An analog block shows a live reading, not a colour, so it renders a
        `---` placeholder the JavaScript replaces. Fails if the placeholder or
        either data attribute is renamed: the box would draw and stay `---`
        forever."""
        svg = gle.render_symbol(_info(type="SYMBOL", name="IAW"),
                                analog_group_key="WND")
        assert 'data-analog="IAW"' in svg
        assert 'data-analog-group="WND"' in svg
        assert '<text class="analog-value"' in svg and ">---<" in svg
        assert "data-bit=" not in svg

    def test_a_constant_wins_over_an_analog_group(self):
        """`is_analog = analog_group_key is not None and not is_const`. A
        literal is a setpoint even if some group's pattern happens to match it.
        Fails if the `and not is_const` goes."""
        svg = gle.render_symbol(_info(type="SYMBOL", name="600"),
                                analog_group_key="WND")
        assert 'data-const="600"' in svg
        assert "data-analog=" not in svg

    def test_the_relay_model_is_what_marks_a_symbol_analog(self):
        """`render_page` asks the model, per symbol. Pinned against the REAL
        SEL-411L profile, so it also guards `data/relay_models/SEL-411L.json`:
        `IAW` is in the `WND` group and `IN101` is a digital.

        Fails if `analog_group_for` stops being consulted, or if that profile's
        patterns are edited -- the winding currents would go back to being
        polled as Relay Word bits that do not exist."""
        page = _page_from(
            '<editor><pages><page name="P"><elements>'
            '<element id="1" type="SYMBOL" left="0" top="0">'
            '<logic_element type="SYMBOL" physical_instance_name="IAW" alias="">'
            '</logic_element></element>'
            '<element id="2" type="SYMBOL" left="0" top="24">'
            '<logic_element type="SYMBOL" physical_instance_name="IN101" alias="">'
            '</logic_element></element>'
            '</elements><connections /></page></pages></editor>')
        svg = gle.render_page(page, relay_model=relay_models.lookup("411L"))
        assert 'data-analog="IAW"' in svg
        assert 'data-analog-group="WND"' in svg
        assert 'data-bit="IN101"' in svg

    def test_an_alias_becomes_both_the_label_and_the_polled_bit(self):
        """`name = info["name"] or info["alias"]`, and that same `name` feeds
        BOTH the visible text and `data-bit`. So a symbol with no physical name
        is polled under its human label -- the relay has no such bit and the
        block stays indeterminate. Pinned, not fixed: no GLE in the corpus
        writes an alias (703 symbols in `samples/GL1.gle.xml`, zero aliases),
        so this is latent rather than live, and Phase 3 changes no production
        code.

        The two also disagree on WIDTH: `render_symbol` measures the alias
        (52 px here) while `page_bounds` measures the empty physical name
        (30 px), so such a symbol can be drawn wider than the canvas reserved
        for it.

        Fails if `data-bit` is narrowed to the physical name -- which would be
        the fix, and should come with this test rewritten, not deleted."""
        svg = gle.render_symbol(_info(type="SYMBOL", name="", alias="APELIDO"))
        assert ">APELIDO<" in svg
        assert 'data-bit="APELIDO"' in svg
        assert 'width="52"' in svg
        assert gle._estimate_symbol_width("") == 30

    def test_a_name_with_xml_metacharacters_is_escaped(self):
        """Nothing sanitises `physical_instance_name` upstream. Fails if
        `html.escape` is dropped from either the text node or the data
        attribute -- one `&` and the browser refuses the whole page."""
        svg = gle.render_symbol(_info(type="SYMBOL", name='A&B<"C"'))
        assert "&amp;" in svg and "&lt;" in svg
        ET.fromstring(f"<g>{svg}</g>")


# -----------------------------------------------------------------------------
# Geometry
# -----------------------------------------------------------------------------

class TestGeometry:

    def test_a_tall_block_starts_its_first_port_two_grid_rows_down(self):
        """A PLT/PCNDTIMER reserves a header row for its label, so port 0 sits
        24 px below `top`; an AND has no label row and starts at 12. These
        offsets were MEASURED off real GLE connection endpoints -- get one
        wrong and every wire on that block lands between two pins.

        Fails if `PORT_FIRST_OFFSET` loses an entry (the 12 px default would
        apply) or if `PORT_SPACING` changes."""
        assert gle._port_y(96, 0, "PLT") == 120
        assert gle._port_y(96, 1, "PLT") == 132
        assert gle._port_y(96, 0, "AND") == 108
        assert gle._port_y(96, 0, "Connector") == 102     # 6, not 12
        assert gle._port_y(96, 0, "TIPO_DESCONHECIDO") == 108

    def test_a_block_grows_tall_enough_for_its_ports(self):
        """Height = first offset + (ports-1)*spacing + padding, floored by the
        type's minimum. Fails if the port count stops driving the height: a
        6-input OR would draw pins outside its own rectangle."""
        assert gle.compute_size(_info(type="AND"), 2, 1) == (36, 30)
        assert gle.compute_size(_info(type="AND"), 6, 1) == (36, 78)
        assert gle.compute_size(_info(type="PLT"), 2, 1) == (54, 42)

    def test_a_symbol_ignores_its_port_count(self):
        """A SYMBOL is one box with one pin a side, whatever the connections
        say. Fails if the SYMBOL early-return goes and symbols start growing."""
        assert gle.compute_size(_info(type="SYMBOL"), 4, 4) == (66, 12)

    def test_the_port_count_comes_from_the_highest_port_number_used(
            self, page):
        """A GLE does not declare how many pins a block has; the renderer
        infers it from the connections that land on it (`max(port)+1`). Fails
        if the `+1` goes -- the last input of every block would lose its pin."""
        in_counts, out_counts = gle.count_actual_ports(page)
        assert in_counts["301"] == 3        # ports 0, 1 and 2 are wired
        assert in_counts["200"] == 2
        assert out_counts["200"] == 1

    def test_a_symbols_width_is_read_back_off_its_own_wire(self, page):
        """The GLE stores no width. The first waypoint of an outgoing
        connection IS the right edge of the box, so the width is recovered from
        it -- which is why the drawing lines up with the wires at all.

        Fails if `deduce_symbol_widths` stops taking the max, stops bounding to
        12..200, or starts measuring non-SYMBOL elements (the PLT at 360 has an
        outgoing wire and must not appear here)."""
        widths = gle.deduce_symbol_widths(page)
        assert widths == {"100": 66, "101": 66, "400": 66}

    def test_an_implausible_deduced_width_is_discarded(self):
        """A first waypoint inside the block, or a page away, is not a width.
        Fails if the 12..200 window goes: a 400 px wide symbol box would be
        drawn over half the diagram."""
        page = _page_from(
            '<editor><pages><page name="P"><elements>'
            '<element id="1" type="SYMBOL" left="60" top="0">'
            '<logic_element type="SYMBOL" physical_instance_name="A" alias="">'
            '</logic_element></element></elements>'
            '<connections><connection id="9">'
            '<source_port element_id="1" port_number="0" />'
            '<sink_port element_id="2" port_number="0" />'
            '<points><point x="66" y="6" /><point x="600" y="6" /></points>'
            '</connection></connections></page></pages></editor>')
        assert gle.deduce_symbol_widths(page) == {}    # 66-60 = 6, below 12

    def test_a_dot_is_drawn_only_where_three_connections_actually_branch(
            self, page):
        """Two wires that merely CROSS must not get a dot; three that meet
        must. On a protection drawing that dot is the difference between 'these
        signals are tied together' and 'these signals pass over each other'.

        Fails if the `>= 3` threshold changes."""
        assert gle.find_junctions(page) == {(276, 108), (318, 108)}

    def test_one_connection_visiting_a_point_twice_counts_once(self):
        """A GLE routinely repeats a waypoint inside one polyline (the fixture's
        real wires do). Without the per-connection `seen` set, a single wire
        with a doubled corner would draw a branch dot on itself.

        Fails if that de-duplication goes."""
        page = _page_from(
            '<editor><pages><page name="P"><elements /><connections>'
            '<connection id="1"><points>'
            '<point x="10" y="10" /><point x="10" y="10" />'
            '<point x="10" y="10" /><point x="20" y="10" />'
            '</points></connection>'
            '</connections></page></pages></editor>')
        assert gle.find_junctions(page) == set()

    def test_comment_width_is_estimated_from_the_character_count(self):
        """3.5 px per monospace character at font-size 6, plus a 4 px gap. This
        is what reserves canvas for a comment, so an over-tight estimate clips
        the text and a loose one leaves a wide empty margin.

        Fails if `PORT_COMMENT_CHAR_W` or `PORT_COMMENT_GAP` changes."""
        assert gle._estimate_comment_width("") == 4
        assert gle._estimate_comment_width("ENTRADA") == int(7 * 3.5) + 4
        assert gle._estimate_comment_width(None) == 4

    def test_symbol_width_is_estimated_with_a_floor(self):
        """Used when no outgoing wire gives the real width. Fails if the 30 px
        floor goes -- a one-character symbol would be narrower than its own
        label."""
        assert gle._estimate_symbol_width("A") == 30      # floor, not 19
        assert gle._estimate_symbol_width("TRIP") == 36
        assert gle._estimate_symbol_width("") == 30

    def test_an_output_comment_extends_the_canvas_to_the_right(self):
        """Companion to the negative-`min_x` case. Fails if the
        `output_comments` loop in `page_bounds` goes and the longest signal
        name on the right-hand column is cut off."""
        tpl = ('<editor><pages><page name="P"><elements>'
               '<element id="1" type="SYMBOL" left="0" top="0">'
               '<logic_element type="SYMBOL" physical_instance_name="A" alias="">'
               '<ports><port index="0"><comment /></port></ports>'
               '<ports><port index="0"><comment>{c}</comment></port></ports>'
               '</logic_element></element></elements>'
               '<connections /></page></pages></editor>')
        short = gle.page_bounds(_page_from(tpl.format(c="")), {}, {}, {})
        long_ = gle.page_bounds(
            _page_from(tpl.format(c="UMA DESCRICAO BEM LONGA")), {}, {}, {})
        assert long_[0] > short[0]
        assert long_[2] == short[2] == 0     # min_x untouched by an output


# -----------------------------------------------------------------------------
# Groups, connections and the types that draw nothing
# -----------------------------------------------------------------------------

class TestGroupsAndConnections:

    def test_a_group_frame_wraps_the_real_size_of_its_elements(self, page):
        """`group_bounds` recomputes each element's SIZE rather than trusting
        `left`/`top`, so the dashed frame actually encloses the blocks: the
        bottom edge here is 144 (the symbol at top=132 plus its 12 px height),
        not 132, and the right edge is 276 (the AND at 240 plus its deduced
        36 px width), not 240.

        Fails if it falls back to the raw coordinates -- the frame would cut
        through the bottom row of every group."""
        grp = [g for g in page.findall(".//group") if g.get("id") == "40"][0]
        in_counts, out_counts = gle.count_actual_ports(page)
        widths = gle.deduce_symbol_widths(page)
        assert gle.group_bounds(grp, in_counts, out_counts, widths) == (
            60, 96, 276, 144)

    def test_an_empty_group_draws_nothing(self):
        """Fails if `group_bounds` stops returning None for an empty group --
        `render_group` would emit a rect at the 10**9 sentinel coordinates."""
        grp = ET.fromstring('<group id="9"><elements /></group>')
        assert gle.group_bounds(grp, {}, {}, {}) is None
        assert gle.render_group(grp, {}, {}, {}) == ""

    def test_a_group_without_a_label_still_gets_its_checkbox(self, page):
        """The checkbox is how the GLV hides a group; a group with no label
        must still be switchable. Fails if the else-branch goes."""
        svg = gle.render_page(page)
        assert svg.count('class="group-checkbox" data-group-id="41"') == 1
        assert 'data-group-id="41">' in svg

    def test_a_connection_carries_both_endpoints_and_both_port_modifiers(
            self, page):
        """The polyline is inert markup until the JavaScript walks it: it
        propagates a bit's value along the wire and inverts it at a `NOT` pin.
        Fails if `data-sink-mod` / `data-src-mod` stop being pre-computed --
        an inverted input would light up green while the relay reads 0."""
        svg = gle.render_page(page)
        assert ('<polyline class="connection" data-src="201" data-src-port="0" '
                'data-dst="500" data-dst-port="0" data-sink-mod="RTRIG" '
                'data-src-mod="NOT" points="276,168 504,168 504,126 540,126"/>'
                ) in svg

    def test_a_connection_with_fewer_than_two_points_is_dropped(self):
        """A one-point polyline is not a line. Fails if the guard goes and the
        SVG gains a `points="10,10"` polyline the browser draws as nothing but
        the search overlay still counts."""
        conn = ET.fromstring(
            '<connection><points><point x="10" y="10" /></points></connection>')
        assert gle.render_connection(conn) == ""

    @pytest.mark.parametrize("etype", ["Block", "Text", "COISA_NOVA"])
    def test_types_the_renderer_has_no_shape_for_draw_nothing(self, etype):
        """`Text` holds RTF the SVG cannot show, `Block` is a container, and an
        unknown type is a GLE from a relay family this renderer predates.
        Returning `""` keeps the page rendering.

        Fails if the fallthrough starts raising, or starts drawing a generic
        box -- an unrecognised element would appear as a phantom gate."""
        assert gle.render_element(_info(type=etype), 1, 1) == ""


# -----------------------------------------------------------------------------
# Pinned oddities
# -----------------------------------------------------------------------------

class TestPinnedOddities:

    def test_a_not_input_pin_is_drawn_as_a_zero_length_line(self):
        """A REAL cosmetic defect, pinned so it is not mistaken for intent.

        `_input_pins_svg` draws the stub from `x - side_w` to `x - 4`, and
        `side_w` defaults to 4 -- so on a NOT input the stub has zero length
        and only the bubble shows. The output side does the same thing from
        `x_right + 4` to `x_right + side_w`.

        Nothing reads the pin, so this is cosmetic, not a wrong signal. Fails
        if `side_w` is widened or the NOT arm reworked -- which would be the
        fix, and should come with this test rewritten, not deleted."""
        svg = gle._input_pins_svg(240, 96, 2, {1: "NOT"}, "AND")
        assert '<line class="port-pin" x1="236" y1="120" x2="236" y2="120"/>' in svg
        assert '<circle class="port-bubble" cx="238" cy="120" r="2"/>' in svg

    def test_the_stylesheet_travels_inside_every_page(self, page):
        """The SVG is injected into a themed page but paints itself: the
        drawing must NOT change with the theme, so its colours are literal here
        and not tokens. Fails if `CSS` is moved out into `/theme.css`."""
        svg = gle.render_page(page)
        assert svg.count("<style>") == 1
        assert ".bit-1" not in svg      # live-state classes belong to the GLV
        assert ".connection      { fill: none; stroke: #404040;" in svg
