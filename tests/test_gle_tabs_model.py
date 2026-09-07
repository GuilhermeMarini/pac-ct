"""The Organizador de Abas GLE's byte surgery.

A page is the largest span in a GLE -- megabytes of RTF and base64 -- and
`rdb_write` verifies the OLE container, never the XML inside a stream. A
boundary one byte wrong therefore reaches the project library looking like a
good file. These tests are the only thing standing there.
"""

from __future__ import annotations

import pytest
from sellib.scl._xmlsafe import DtdNotAllowed

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
    with pytest.raises(DtdNotAllowed):
        model.read_pages(hostile)


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
