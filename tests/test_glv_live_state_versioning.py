"""`LiveState` can be waited on, so the screen stops asking.

The GLV polls. Measured end to end, the relay answers in 1.5 ms, `/values`
costs 0.03 ms and the HTTP round trip 1 ms -- against 100 to 500 ms of waiting
for the next turn of the screen's clock. The wait is the whole latency, and no
amount of making the drawing faster touches it, because the drawing is not
what the screen is waiting for.

What removes it is the server telling the browser when something happened,
which needs one primitive: a reading that a thread can BLOCK on. `version`
counts readings, `wait_for_change` sleeps until the count moves or the timeout
expires. `/events` (SSE) is a loop over it.

The timeout is not a safety net -- it is the heartbeat. The badge shows the
reading's AGE, which has to keep ageing on screen while no bit moves, so a
waiter that never woke would freeze the age at whatever it was when the last
bit changed.
"""

from __future__ import annotations

import threading
import time

from pacct.web.glv.state import LiveState


def _reading(st, mapping):
    """Write a reading the way the four poll loops do: under the lock."""
    with st.lock:
        st.digitals.update(mapping)
        st.mark_updated()


def test_a_reading_moves_the_version():
    """Fails if `mark_updated` stops counting -- `/events` then never has a
    reason to push and the screen goes dark."""
    st = LiveState()
    before = st.version
    _reading(st, {"PLT01": 1})
    assert st.version != before


def test_a_waiter_wakes_when_a_reading_lands():
    """The whole point: a thread parked in `wait_for_change` is released by
    the poll loop, not by a clock. Fails if the notify is dropped or moved
    outside the lock the writers already hold -- the waiter would then sleep
    the full timeout and the push would arrive up to a heartbeat late.
    """
    st = LiveState()
    seen = []

    def waiter():
        seen.append(st.wait_for_change(st.version, timeout=5.0))

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)                      # let it park in the wait
    _reading(st, {"PLT01": 1})
    t.join(timeout=5.0)

    assert not t.is_alive(), "o waiter nao acordou com a leitura"
    assert seen == [st.version]


def test_waiting_returns_on_timeout_when_nothing_moves():
    """The heartbeat. Fails if the wait becomes unbounded: the age on the
    badge would stop ageing whenever the relay is quiet, which is exactly when
    the engineer most wants to know how old the reading is.
    """
    st = LiveState()
    t0 = time.monotonic()
    out = st.wait_for_change(st.version, timeout=0.15)
    waited = time.monotonic() - t0

    assert out is None
    assert 0.1 <= waited < 2.0


def test_a_version_that_already_moved_does_not_wait_at_all():
    """A client whose last-seen version is stale must be answered
    immediately, not parked for a heartbeat. This is the race between a
    reading landing and the next `/events` turn starting; fails if the wait
    ignores the caller's version and always sleeps.
    """
    st = LiveState()
    stale = st.version
    _reading(st, {"PLT01": 1})            # moves on before anyone waits

    t0 = time.monotonic()
    out = st.wait_for_change(stale, timeout=5.0)
    assert out == st.version
    assert time.monotonic() - t0 < 1.0


def test_disconnecting_moves_the_version_too():
    """`clear()` puts everything back to indeterminate, and the screen has to
    repaint to say so. Fails if a disconnect leaves the last drawing on
    screen with nothing to trigger the repaint -- the closest this toolkit
    gets to showing a value nobody read.
    """
    st = LiveState()
    _reading(st, {"PLT01": 1})
    before = st.version
    st.clear()
    assert st.version != before
