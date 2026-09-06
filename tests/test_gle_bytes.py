"""What the regex surgery on a GLE and an SCD is allowed to do.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: both writers edit a protection relay's settings in place, by
regex, on bytes that are latin-1 while the XML header claims utf-8. There is
no schema check behind them. If a substitution corrupts the document, the
first thing to notice is AcSELerator QuickSet refusing the file -- or worse,
accepting it with a description silently on the wrong bit.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pacct.web.gle_exporter import update_port_comments_in_gle_bytes
from pacct.web.vb_updater import (
    _substitute_vb_comments_in_gle_bytes,
    _update_scd_extrefs_for_ied,
)
from tests import gle_fixtures as fx


def _parses(raw: bytes) -> ET.Element:
    """The document still parses. Decoded latin-1 because that is what it is."""
    return ET.fromstring(raw.decode("latin-1"))


def _comments(raw: bytes, element_id: str) -> list[str]:
    """Every port comment of one `<element>`, in document order."""
    root = _parses(raw)
    for el in root.iter("element"):
        if el.get("id") != element_id:
            continue
        return [(c.text or "") for c in el.iter("comment")][1:]  # [0] is the element's own
    raise AssertionError(f"element {element_id} not in document")


# -----------------------------------------------------------------------------
# VB Updater: SCD desc -> GLE port comment
# -----------------------------------------------------------------------------

class TestSubstituteVbComments:

    def test_replaces_the_comment_of_a_named_vb(self):
        """Fails if `_GLE_VB_BLOCK_RE` stops matching, or if the substitution
        writes to the wrong port."""
        out, stats = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "NOVA DESCRICAO"})
        assert stats["updated"] == 1
        assert b"NOVA DESCRICAO" in out
        assert b"TR1 UPC1 FALHA GOOSE" not in out

    def test_writes_only_the_first_port_of_the_block(self):
        """The contract is 'the FIRST port comment of each VB symbol'. Fails if
        the `count=1` on the subn is dropped and both ports get the text."""
        out, _ = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "SO NA PRIMEIRA"})
        assert out.count(b"SO NA PRIMEIRA") == 1

    def test_a_vb_absent_from_the_map_is_left_alone(self):
        """Fails if the writer starts blanking VBs the SCD did not mention --
        which would erase a description an engineer typed in QuickSet."""
        out, stats = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "X"})
        assert stats["untouched"] == 2          # VB007 and VB042
        assert _comments(out, "543") == ["", ""]
        assert _comments(out, "545")[1] == "RESERVA"

    def test_a_non_vb_symbol_is_never_touched(self):
        """TMB1A is a Relay Word symbol, not a virtual bit. Fails if the regex
        loosens to `physical_instance_name="[^"]*"`."""
        out, _ = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "X", "TMB1A": "NAO DEVE ENTRAR"})
        assert b"NAO DEVE ENTRAR" not in out
        assert "DISJUNTOR 52A POSIÇÃO" in out.decode("latin-1")

    def test_the_vb_key_is_matched_by_number_not_by_text(self):
        """`VB042` in the GLE is looked up as `VB42`: the code does
        `f"VB{int(num_str)}"`. Fails if that normalisation is removed, which
        would silently skip every zero-padded VB -- and 3xx/7xx GLEs pad to
        three digits (`VB001`), so the whole family would stop working."""
        out, stats = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB42": "PADDED"})
        assert stats["updated"] == 1
        assert b"PADDED" in out

    def test_an_all_empty_vb_cannot_be_filled_and_is_counted_skipped(self):
        """A REAL LIMITATION, pinned so it is not mistaken for a passing case.

        `_GLE_PORT_COMMENT_RE` requires `<comment>TEXT</comment>` and does not
        match the self-closing `<comment />` that QuickSet writes for an empty
        one. VB007 has nothing but self-closing comments, so the SCD's
        description for it is silently dropped and reported as `skipped` --
        an engineer reading 'X descriptions applied' will not see it.

        The GLE Exporter's own writer DOES handle `<comment />` (see
        `test_fills_a_previously_self_closing_comment`), so the two writers in
        this codebase disagree about whether an empty port is writable.

        Fails if the regex is widened to cover `<comment />` -- which would be
        a fix, and should come with this test flipped, not deleted."""
        _out, stats = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB7": "NAO VAI ENTRAR"})
        assert stats["updated"] == 0
        assert stats["skipped"] == 1

    def test_the_document_still_parses_afterwards(self):
        """The whole point. Fails if a substitution ever emits unbalanced tags."""
        out, _ = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "OK", "VB7": "OK2"})
        _parses(out)

    def test_accented_latin1_text_survives_the_round_trip(self):
        """Portuguese descriptions are the normal case, not an edge case.
        Fails if the encode is switched to utf-8 -- which would look fine in
        Python and render as mojibake in QuickSet."""
        out, _ = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "PROTEÇÃO DIFERENCIAL AÇÃO"})
        # Index 1 = the OUTPUT port: VB105's input is `<comment />`, which the
        # regex does not match, so the first port it CAN write is the output.
        assert _comments(out, "542")[1] == "PROTEÇÃO DIFERENCIAL AÇÃO"

    def test_a_character_outside_latin1_is_replaced_not_raised(self):
        """`errors="replace"` is deliberate: an export must not die on one bad
        character pasted from a Word document. Pinned so nobody 'fixes' it into
        a crash, and so the lossy behaviour stays visible.

        Fails if the encode drops `errors="replace"` (raises) or switches to
        utf-8 (the euro sign would survive as two bytes)."""
        out, stats = _substitute_vb_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"VB105": "CUSTO 10€"})
        assert stats["updated"] == 1
        assert _comments(out, "542")[1] == "CUSTO 10?"

    def test_counts_a_vb_whose_port_comment_is_unfindable_as_skipped(self):
        """A VB symbol with no `<port><comment>` at all. Fails if such a symbol
        starts being counted as updated, which would overstate the report the
        engineer signs off on."""
        doc = fx.gle(
            b'            <element id="900" type="SYMBOL">\r\n'
            b'              <logic_element type="SYMBOL" '
            b'physical_instance_name="VB050" alias="">\r\n'
            b'                <ports />\r\n'
            b'              </logic_element>\r\n'
            b'            </element>\r\n'
        )
        _out, stats = _substitute_vb_comments_in_gle_bytes(doc, {"VB50": "X"})
        assert stats == {"updated": 0, "skipped": 1, "untouched": 0}


# -----------------------------------------------------------------------------
# GLE Exporter: spreadsheet -> GLE port comment
# -----------------------------------------------------------------------------

class TestUpdatePortComments:

    def test_writes_the_input_side_from_the_first_ports_block(self):
        """Side is positional: block 0 is input, block 1 is output. Fails if
        that ordering assumption changes -- the comment would land on the wrong
        pin, which on a commissioning drawing is a wrong signal name."""
        out, stats = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"542": {("input", 0): "ENTRADA"}})
        assert stats["ports_updated"] == 1
        assert _comments(out, "542")[0] == "ENTRADA"

    def test_writes_the_output_side_from_the_second_ports_block(self):
        """Companion to the above; fails the same way."""
        out, stats = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"542": {("output", 0): "SAIDA"}})
        assert stats["ports_updated"] == 1
        assert _comments(out, "542")[1] == "SAIDA"

    def test_an_empty_comment_becomes_the_self_closing_spelling(self):
        """Clearing a comment must produce `<comment />`, the spelling QuickSet
        itself writes for an empty one. Fails if `_build_comment_node` starts
        emitting `<comment></comment>`."""
        out, _ = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"542": {("output", 0): ""}})
        assert b"<comment />" in out
        assert _comments(out, "542")[1] == ""

    def test_fills_a_previously_self_closing_comment(self):
        """VB007's output starts as `<comment />`. Fails if the regex's
        `(\\s*/>|\\s*>[^<]*</comment>)` alternation loses the self-closing arm,
        which would make every empty port uneditable."""
        out, stats = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"543": {("output", 0): "AGORA TEM TEXTO"}})
        assert stats["ports_updated"] == 1
        assert _comments(out, "543")[1] == "AGORA TEM TEXTO"

    def test_escapes_xml_metacharacters_in_the_text(self):
        """An engineer typing `A & B < C` must not produce a broken GLE.
        Fails if `_xml_text_escape` is bypassed."""
        out, _ = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"542": {("output", 0): "A & B < C"}})
        _parses(out)
        assert _comments(out, "542")[1] == "A & B < C"

    def test_an_unknown_element_id_is_reported_not_ignored(self):
        """Fails if `elements_missing` stops being counted -- the import
        summary would claim success for rows that changed nothing."""
        _out, stats = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"99999": {("output", 0): "X"}})
        assert stats["elements_missing"] == 1
        assert stats["ports_updated"] == 0

    def test_a_port_index_that_does_not_exist_is_skipped(self):
        """Fails if a missing port starts counting as updated."""
        _out, stats = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE, {"542": {("output", 7): "X"}})
        assert stats["ports_skipped"] == 1
        assert stats["ports_updated"] == 0

    def test_the_document_still_parses_afterwards(self):
        out, _ = update_port_comments_in_gle_bytes(
            fx.SAMPLE_GLE,
            {"542": {("input", 0): "A", ("output", 0): "B"},
             "543": {("output", 0): ""}})
        _parses(out)


# -----------------------------------------------------------------------------
# VB Updater: GLE comment -> SCD ExtRef desc
# -----------------------------------------------------------------------------

class TestUpdateScdExtrefs:

    def _doc(self) -> bytes:
        return fx.scd(
            fx.ied("QPC1_UPC2",
                   fx.extref("VB001", desc="ANTIGO"),
                   fx.extref("VB002")),
            fx.ied("OUTRO_IED",
                   fx.extref("VB001", desc="NAO MEXER")),
        )

    def test_replaces_an_existing_desc(self):
        """Fails if `_SCD_DESC_ATTR_RE` stops matching."""
        out, stats = _update_scd_extrefs_for_ied(
            self._doc(), "QPC1_UPC2", {"VB1": "NOVO"})
        assert stats["updated"] == 1
        assert b'desc="NOVO"' in out
        assert b'desc="ANTIGO"' not in out

    def test_inserts_a_desc_where_there_was_none(self):
        """VB002 has no `desc`. Fails if the insert-after-intAddr path is lost,
        which would silently drop every description for a spare VB."""
        out, stats = _update_scd_extrefs_for_ied(
            self._doc(), "QPC1_UPC2", {"VB2": "INSERIDO"})
        assert stats["inserted"] == 1
        assert b'desc="INSERIDO"' in out

    def test_never_touches_another_ied(self):
        """The same VB number exists under two IEDs. Writing outside the named
        `<IED>` block would put one relay's signal names on another's -- the
        exact failure that makes a commissioning report worthless.

        Fails if the IED-block slicing in `_update_scd_extrefs_for_ied` goes."""
        out, _ = _update_scd_extrefs_for_ied(
            self._doc(), "QPC1_UPC2", {"VB1": "NOVO"})
        assert b'desc="NAO MEXER"' in out

    def test_the_gle_side_escapes_the_scd_desc_too(self):
        """The SCD -> GLE direction writes `desc` into `<comment>`, and it was
        the one of the three writers that did not escape. An ampersand is
        ordinary in a signal name ("50/62BF & LT1"), and the malformed GLE
        went into the output RDB and then into the project library, where
        nothing tells it apart from a good file -- `write_streams` verifies
        the CONTAINER, never the XML inside a stream.

        Fails if `xml_text_escape` is bypassed in
        `_substitute_vb_comments_in_gle_bytes`."""
        raw = (b'<logic_element type="SYMBOL" physical_instance_name="VB001">'
               b'<port index="0"><comment>antigo</comment></port>'
               b'</logic_element>')
        out, stats = _substitute_vb_comments_in_gle_bytes(
            raw, {"VB1": "TRIP & BLOCK <A>"})
        assert stats["updated"] == 1
        root = ET.fromstring(out.decode("latin-1"))
        assert root.find(".//comment").text == "TRIP & BLOCK <A>"

    def test_an_unknown_ied_changes_nothing(self):
        """Fails if a missing IED starts falling through to a global replace."""
        doc = self._doc()
        out, stats = _update_scd_extrefs_for_ied(doc, "NAO_EXISTE", {"VB1": "X"})
        assert out == doc
        assert stats == {"updated": 0, "inserted": 0, "untouched": 0}

    def test_escapes_quotes_and_ampersands_in_the_attribute(self):
        """`desc` is an XML attribute; an unescaped `"` truncates it and an
        unescaped `&` breaks the parse. Fails if `_xml_attr_escape` is bypassed."""
        out, _ = _update_scd_extrefs_for_ied(
            self._doc(), "QPC1_UPC2", {"VB1": 'TRIP "A" & "B"'})
        root = ET.fromstring(out.decode("utf-8"))
        found = [e.get("desc") for e in root.iter()
                 if e.tag.endswith("ExtRef") and e.get("intAddr") == "VB001"]
        assert 'TRIP "A" & "B"' in found

    def test_an_empty_new_description_is_treated_as_no_change(self):
        """`if not new` skips. Pinned because it is load-bearing: the VB
        Updater maps an empty SCD desc to the literal 'reserva' upstream rather
        than writing an empty string here.

        Fails if the falsy check becomes `is None`."""
        doc = self._doc()
        out, stats = _update_scd_extrefs_for_ied(doc, "QPC1_UPC2", {"VB1": ""})
        assert out == doc
        assert stats["untouched"] == 2

    def test_the_document_still_parses_afterwards(self):
        out, _ = _update_scd_extrefs_for_ied(
            self._doc(), "QPC1_UPC2", {"VB1": "A", "VB2": "B"})
        ET.fromstring(out.decode("utf-8"))
