"""The `/events` stream: the server says when, instead of the browser asking.

`event_frames` is the whole of the SSE route that is worth testing -- the
route itself only writes what this yields onto a socket. It is a generator so
a test can drive it with a 50 ms heartbeat and a short deadline instead of a
real browser and a real relay.

Two properties carry the feature. The FIRST frame goes out immediately, before
any wait: a browser that has just connected has nothing on screen, and making
it wait a heartbeat for the current state would be slower than the polling it
replaces. And a frame keeps coming while nothing moves, because the badge
shows the reading's AGE and that has to keep ageing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from pacct.web.glv.diagram import build_diagram
from pacct.web.glv.events import event_frames

FIXTURE = Path(__file__).parent / "fixtures" / "connectors.gle.xml"
PAGE = "P2_LEDs"


@pytest.fixture
def diagram():
    return build_diagram("d1", FIXTURE, "REL", "conn.gle", "192.0.2.10", 23,
                         None, logging.getLogger("test"))


def _payload(frame: str) -> dict:
    """The JSON out of one `data: ...` SSE frame."""
    assert frame.startswith("data: "), frame
    assert frame.endswith("\n\n"), repr(frame[-4:])
    return json.loads(frame[len("data: "):].strip())


def _reading(d, mapping):
    with d.idle.lock:
        d.idle.digitals.update(mapping)
        d.idle.mark_updated()


def test_the_first_frame_goes_out_without_waiting(diagram):
    """Fails if the loop waits before its first send: a freshly opened tab
    would sit blank for a heartbeat, which is worse than the poll it
    replaces."""
    t0 = time.monotonic()
    frames = event_frames(diagram, PAGE, heartbeat=5.0, max_seconds=5.0)
    first = next(frames)
    frames.close()

    assert time.monotonic() - t0 < 1.0
    assert "rev" in _payload(first)


def test_a_frame_is_a_well_formed_sse_data_event(diagram):
    """`EventSource` silently ignores a frame that is not `data: ...` with a
    blank line after it. Fails on any framing change -- and the failure in a
    browser is no error at all, just a screen that never updates."""
    frames = event_frames(diagram, PAGE, heartbeat=5.0, max_seconds=5.0)
    first = next(frames)
    frames.close()

    assert first.startswith("data: ")
    assert first.endswith("\n\n")
    assert "\n" not in first[len("data: "):-2], "JSON com quebra de linha"


def test_a_reading_produces_the_next_frame(diagram):
    """The point of the stream. Fails if the loop stops waking on a reading
    and falls back to the heartbeat -- the screen would then lag the relay by
    up to a full heartbeat instead of by the round trip."""
    frames = event_frames(diagram, PAGE, heartbeat=5.0, max_seconds=5.0)
    first = _payload(next(frames))

    threading.Timer(0.05, lambda: _reading(diagram, {"LED05": 1})).start()
    t0 = time.monotonic()
    second = _payload(next(frames))
    elapsed = time.monotonic() - t0
    frames.close()

    assert second["rev"] != first["rev"]
    assert elapsed < 4.0, "esperou o heartbeat em vez da leitura"


def test_it_keeps_sending_while_nothing_moves(diagram):
    """The heartbeat, which is what keeps the age on the badge ageing. Fails
    if the loop only ever emits on change: a quiet relay would freeze the
    displayed age at the last bit that moved."""
    frames = event_frames(diagram, PAGE, heartbeat=0.05, max_seconds=5.0)
    first = _payload(next(frames))
    second = _payload(next(frames))
    frames.close()

    assert second["rev"] == first["rev"], "nada mudou, rev nao podia mudar"


def test_the_stream_ends_at_its_deadline(diagram):
    """A forgotten tab must not hold a server thread for ever; `EventSource`
    reconnects on its own. Fails if `max_seconds` stops being honoured."""
    frames = event_frames(diagram, PAGE, heartbeat=0.02, max_seconds=0.15)
    collected = list(frames)

    assert len(collected) >= 1
    assert len(collected) < 200
