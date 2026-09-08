"""The Organizador de Abas GLE's byte surgery.

A page is the largest span in a GLE -- megabytes of RTF and base64 -- and
`rdb_write` verifies the OLE container, never the XML inside a stream. A
boundary one byte wrong therefore reaches the project library looking like a
good file. These tests are the only thing standing there.
"""

from __future__ import annotations

import dataclasses

import pytest
from py61850.scl import DtdNotAllowed

from pacct.web.gle_tabs import model
from tests import gle_fixtures as fx


def test_it_finds_every_page_in_order():
    spans = model.read_pages(fx.TABS_GLE)
    assert [s.name for s in spans] == [
        "Capa", "Entradas Críticas", "U>U< I >I<",
        "RESERVA", "RESERVA", "52- CMD DE FECHAMENT",
    ]
    assert [s.index for s in spans] == [0, 1, 2, 3, 4, 5]


def test_a_span_covers_exactly_its_page_element():
    spans = model.read_pages(fx.TABS_GLE)
    first = spans[0]
    assert fx.TABS_GLE[first.start:first.start + 5] == b"<page"
    assert fx.TABS_GLE[first.end - 7:first.end] == b"</page>"


def test_an_escaped_close_tag_in_text_does_not_end_the_page():
    """The page whose <rtf_text> holds "&lt;/page&gt;" is page 0, and it must
    still end at its real </page> -- this is the case a regex gets wrong."""
    spans = model.read_pages(fx.TABS_GLE)
    assert b"&lt;/page&gt;" in fx.TABS_GLE[spans[0].start:spans[0].end]
    assert spans[0].name == "Capa"


def test_the_name_span_points_at_the_attribute_value():
    spans = model.read_pages(fx.TABS_GLE)
    esc = spans[2]                      # U&gt;U&lt; I &gt;I&lt;
    assert fx.TABS_GLE[esc.name_start:esc.name_end] == b"U&gt;U&lt; I &gt;I&lt;"
    assert esc.name == "U>U< I >I<"     # expat hands back the UNESCAPED name


def test_elements_are_counted_per_page():
    spans = model.read_pages(fx.TABS_GLE)
    assert [s.elements for s in spans] == [1, 2, 0, 0, 0, 0]


def test_a_gle_with_a_doctype_is_refused():
    """A GLE arrives inside an RDB somebody uploaded; it is no more trusted
    than an SCD, so the billion-laughs guard runs before the parse."""
    hostile = fx.TABS_GLE.replace(
        b"<editor>", b"<!DOCTYPE editor [<!ENTITY a 'b'>]>\r\n<editor>", 1)
    with pytest.raises(model.GleTabsError) as excinfo:
        model.read_pages(hostile)
    # The wrap is at the source (model.read_pages), not at call sites, so
    # callers have only GleTabsError to know. The cause chain survives.
    assert isinstance(excinfo.value.__cause__, DtdNotAllowed)


# -- validation -------------------------------------------------------------

def _spans():
    return model.read_pages(fx.TABS_GLE)


def test_an_order_that_drops_a_page_is_refused():
    """Omission is the dangerous shape: a short list would silently delete."""
    with pytest.raises(model.GleTabsError, match="permuta"):
        model.validate_edit(_spans(), order=[0, 1, 2, 3, 4], names={})


def test_an_order_that_repeats_a_page_is_refused():
    with pytest.raises(model.GleTabsError, match="permuta"):
        model.validate_edit(_spans(), order=[0, 0, 1, 2, 3, 4], names={})


def test_the_identity_order_is_accepted():
    model.validate_edit(_spans(), order=[0, 1, 2, 3, 4, 5], names={})


def test_an_empty_name_is_refused():
    with pytest.raises(model.GleTabsError, match="vazio"):
        model.validate_edit(_spans(), order=list(range(6)), names={0: "   "})


def test_a_name_over_twenty_characters_is_refused():
    with pytest.raises(model.GleTabsError, match="20"):
        model.validate_edit(_spans(), order=list(range(6)),
                            names={0: "A" * 21})


def test_a_name_of_exactly_twenty_characters_is_accepted():
    model.validate_edit(_spans(), order=list(range(6)), names={0: "A" * 20})


def test_a_name_with_a_control_character_is_refused():
    with pytest.raises(model.GleTabsError, match="controle"):
        model.validate_edit(_spans(), order=list(range(6)),
                            names={0: "TRIP\x07"})


def test_a_name_the_gle_cannot_store_is_refused():
    """The file is latin-1. A name outside it would raise on encode, deep
    inside the splice, with the RDB half built."""
    with pytest.raises(model.GleTabsError, match="latin-1"):
        model.validate_edit(_spans(), order=list(range(6)), names={0: "TRIP €"})


def test_a_rename_that_creates_a_duplicate_is_refused():
    with pytest.raises(model.GleTabsError, match="RESERVA"):
        model.validate_edit(_spans(), order=list(range(6)),
                            names={0: "RESERVA"})


def test_a_duplicate_that_was_already_there_is_tolerated():
    """Pages 3 and 4 are both "RESERVA" in the fixture, as real files ship.
    Refusing them would reject the file for a state the tool did not create."""
    model.validate_edit(_spans(), order=[5, 4, 3, 2, 1, 0], names={})


def test_naming_a_page_that_does_not_exist_is_refused():
    with pytest.raises(model.GleTabsError, match="não existe"):
        model.validate_edit(_spans(), order=list(range(6)), names={99: "X"})


def test_the_attribute_escape_covers_the_quote():
    """`rdb_write.xml_text_escape` is for TEXT and leaves `"` alone. A name is
    an ATTRIBUTE: an unescaped quote would close it and produce a malformed
    GLE that nothing downstream distinguishes from a good one."""
    assert model.escape_attr('A "B" & <C>') == "A &quot;B&quot; &amp; &lt;C&gt;"


# -- the splice -------------------------------------------------------------

def test_an_identity_edit_is_byte_identical():
    """The property the whole tool rests on. Measured over the local corpus at
    design time: 215 files, 3.111 pages, byte-identical 215/215."""
    out, stats = model.apply_page_edits(
        fx.TABS_GLE, order=list(range(6)), names={})
    assert out == fx.TABS_GLE
    assert stats == {"moved": 0, "renamed": 0}


def test_a_permutation_reorders_the_pages_and_keeps_the_length():
    order = [5, 0, 1, 2, 3, 4]
    out, stats = model.apply_page_edits(fx.TABS_GLE, order=order, names={})
    assert [s.name for s in model.read_pages(out)] == [
        "52- CMD DE FECHAMENT", "Capa", "Entradas Críticas",
        "U>U< I >I<", "RESERVA", "RESERVA",
    ]
    # A permutation moves bytes, it never adds any. That is what lets
    # olefile.write_stream swap the stream in place instead of rebuilding.
    assert len(out) == len(fx.TABS_GLE)
    assert stats["moved"] == 6


def test_the_bytes_outside_the_pages_never_move():
    order = [3, 2, 1, 0, 5, 4]
    out, _ = model.apply_page_edits(fx.TABS_GLE, order=order, names={})
    spans_in = model.read_pages(fx.TABS_GLE)
    spans_out = model.read_pages(out)
    assert out[:spans_out[0].start] == fx.TABS_GLE[:spans_in[0].start]
    assert out[spans_out[-1].end:] == fx.TABS_GLE[spans_in[-1].end:]


def test_the_comment_between_two_pages_stays_where_it_was():
    """It is page furniture, not part of a page: it must not travel."""
    out, _ = model.apply_page_edits(
        fx.TABS_GLE, order=[5, 4, 3, 2, 1, 0], names={})
    spans = model.read_pages(out)
    between = out[spans[0].end:spans[1].start]
    assert b"<!-- separador" in between


def test_a_rename_touches_only_the_name_attribute():
    out, stats = model.apply_page_edits(
        fx.TABS_GLE, order=list(range(6)), names={0: "CAPA NOVA"})
    assert [s.name for s in model.read_pages(out)][0] == "CAPA NOVA"
    assert stats == {"moved": 0, "renamed": 1}
    # everything after page 0 is untouched
    old, new = model.read_pages(fx.TABS_GLE), model.read_pages(out)
    assert out[new[1].start:] == fx.TABS_GLE[old[1].start:]


def test_a_rename_is_escaped_and_reads_back_verbatim():
    out, _ = model.apply_page_edits(
        fx.TABS_GLE, order=list(range(6)), names={0: 'A "B" & <C>'})
    assert b'name="A &quot;B&quot; &amp; &lt;C&gt;"' in out
    assert model.read_pages(out)[0].name == 'A "B" & <C>'


def test_an_accented_rename_is_written_latin_one():
    out, _ = model.apply_page_edits(
        fx.TABS_GLE, order=list(range(6)), names={0: "Proteção"})
    assert "Proteção".encode("latin-1") in out
    assert model.read_pages(out)[0].name == "Proteção"


def test_renaming_a_page_to_the_name_it_has_is_not_counted():
    out, stats = model.apply_page_edits(
        fx.TABS_GLE, order=list(range(6)), names={0: "Capa"})
    assert out == fx.TABS_GLE
    assert stats["renamed"] == 0


def test_moving_and_renaming_at_once():
    out, stats = model.apply_page_edits(
        fx.TABS_GLE, order=[1, 0, 2, 3, 4, 5], names={1: "PRIMEIRA"})
    assert [s.name for s in model.read_pages(out)][:2] == ["PRIMEIRA", "Capa"]
    assert stats == {"moved": 2, "renamed": 1}


def test_an_invalid_edit_raises_before_producing_anything():
    with pytest.raises(model.GleTabsError):
        model.apply_page_edits(fx.TABS_GLE, order=[0, 1], names={})


def test_the_self_check_refuses_a_mis_spliced_page(monkeypatch):
    """`apply_page_edits` re-parses its own output, and that check is the only
    one there is: `rdb_write` verifies the OLE container and never the XML
    inside a stream, so a page spliced one byte wrong would otherwise reach the
    project library looking like a good file. Forced here by making the second
    `read_pages` -- the one that reads the RESULT -- hand back a name the
    splice cannot have produced."""
    real = model.read_pages
    seen = []

    def spy(raw):
        spans = real(raw)
        seen.append(raw)
        if len(seen) == 1:          # the pass that measures the INPUT
            return spans
        return [dataclasses.replace(s, name="ERRADO") if s.index == 0 else s
                for s in spans]

    monkeypatch.setattr(model, "read_pages", spy)
    with pytest.raises(model.GleTabsError) as excinfo:
        model.apply_page_edits(fx.TABS_GLE, order=[1, 0, 2, 3, 4, 5], names={})
    msg = str(excinfo.value)
    assert "não conferiu" in msg
    assert "ERRADO" in msg              # what the file came out with
    assert "Entradas Críticas" in msg   # what was asked for
    assert "Nada foi gravado" in msg
