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
