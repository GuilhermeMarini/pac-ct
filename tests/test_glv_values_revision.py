"""`/values` says whether anything the drawing depends on actually moved.

The GLV's screen repaints on a clock, not on a change. Measured on the
heaviest page of the 418-file reference corpus (118 elements, 83 connections),
one repaint costs the browser 7 `querySelectorAll` sweeps, 118 nested
`querySelector` calls, ~1238 `getAttribute` reads and ~806 `classList`
writes -- and in a substation at rest, essentially none of those writes change
a pixel, because the bits are not moving.

The client cannot decide that for itself by comparing the response: `ts` and
`age` are in every payload and differ on every read by construction. So the
server says it. `rev` is a checksum over exactly the three things the drawing
is a function of -- the open page, its digitals, its analogs -- and nothing
else. The client redraws when it changes and returns immediately when it does
not, and `/events` (SSE) pushes only when it changes.

The fourth test is the one that carries the whole idea: if the clock alone
moved `rev`, every one of these savings would be zero.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pacct.web.glv.diagram import build_diagram

FIXTURE = Path(__file__).parent / "fixtures" / "connectors.gle.xml"
PAGE = "P2_LEDs"


@pytest.fixture
def diagram():
    """A diagram off the connectors fixture, with no link and no network."""
    return build_diagram("d1", FIXTURE, "REL", "conn.gle", "192.0.2.10", 23,
                         None, logging.getLogger("test"))


def _set_digitals(d, mapping):
    """Write a reading into the idle state, the way a poll loop would."""
    with d.idle.lock:
        d.idle.digitals.update(mapping)
        d.idle.mark_updated()


def test_values_carries_a_revision(diagram):
    """Fails if `rev` is dropped from the payload -- the client then has
    nothing to compare and falls back to repainting on every tick."""
    payload = diagram.values(PAGE)
    assert "rev" in payload


def test_the_same_reading_twice_has_the_same_revision(diagram):
    """Fails the moment `rev` picks up anything that varies per call: the
    client would see a change every tick and the early-out would never fire.
    """
    _set_digitals(diagram, {"LED05": 1})
    first = diagram.values(PAGE)["rev"]
    second = diagram.values(PAGE)["rev"]
    assert first == second


def test_a_bit_the_page_draws_moves_the_revision(diagram):
    """Fails if `rev` stops covering the digitals -- the screen would then
    freeze on the last drawing while the relay keeps reporting changes, which
    is worse than the repaint it replaces."""
    _set_digitals(diagram, {"LED05": 0})
    before = diagram.values(PAGE)["rev"]
    _set_digitals(diagram, {"LED05": 1})
    assert diagram.values(PAGE)["rev"] != before


def test_time_passing_alone_does_not_move_the_revision(diagram):
    """The point of the whole change.

    `ts` and `age` differ on every single read -- `age` is recomputed from the
    monotonic clock inside `snapshot()`. If `rev` were a checksum of the
    payload it would differ every time and buy nothing. Fails if `rev` is ever
    computed over the whole snapshot instead of the three drawing inputs.
    """
    _set_digitals(diagram, {"LED05": 1})
    first = diagram.values(PAGE)
    _set_digitals(diagram, {"LED05": 1})       # same value, later clock
    second = diagram.values(PAGE)

    assert second["ts"] != first["ts"] or second["age"] != first["age"]
    assert second["rev"] == first["rev"]


def test_two_pages_of_one_diagram_do_not_share_a_revision(diagram):
    """Each open page draws a different set of bits, so the answer to "did my
    drawing change" is per page. Fails if `rev` stops covering the page id."""
    _set_digitals(diagram, {"LED05": 1})
    assert diagram.values("P1_Logica")["rev"] != diagram.values(PAGE)["rev"]
