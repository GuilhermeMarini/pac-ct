"""`RelayLink.set_poll_interval`: the runtime knob behind `/period`.

Two things pinned here that were not true before this file existed:

1. A running poll loop only ever received `interval` as a thread argument and
   never re-read it, so changing the period had to mean stop-then-restart the
   poll thread with the new value.
2. That stop-then-restart used to release `self._lock` BETWEEN the stop and
   the restart. `close()` (called by `/disconnect`, on its own thread) also
   takes that lock to stop polling -- so a `/period` racing a `/disconnect` on
   the same diagram could see `close()` slip in during that gap, finish
   closing the transport, and then have the OLD `/period` call resume and
   spawn a brand new poll thread onto a transport that was already closed.
   That thread's `stop` Event is never set again (the `RelayLink` has already
   left `LinkPool._links`), so it loops forever against a dead connection.
"""
from __future__ import annotations

import logging
import threading
import time

from pacct.web.glv.link import RelayLink


class _FakeTransport:
    """Records what happened and lets a test hold `poll()` open on demand."""

    mode = "fake"

    def __init__(self):
        self.fid = "FID"
        self.devid = "DEV"
        self.close_called = threading.Event()
        self.seen_intervals: list = []
        self.polled = threading.Event()

    def connect(self, job=None):
        pass

    def abort(self):
        pass

    def close(self):
        self.close_called.set()

    def prepare_bits(self, names, job=None, pause=None):
        return 0

    def poll(self, state, interval, stop, once):
        self.seen_intervals.append(interval)
        self.polled.set()
        stop.wait(timeout=5.0)

    def coverage(self):
        return None


def _connected_link() -> RelayLink:
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=_FakeTransport())
    link.connect(relay_model=None, poll_interval=0.5)
    assert link.transport.polled.wait(timeout=5.0)
    return link


class TestSetPollInterval:

    def test_restarts_the_running_poll_with_the_new_period(self):
        link = _connected_link()
        link.transport.polled.clear()
        applied = link.set_poll_interval(0.1)
        assert applied is True
        assert link.transport.polled.wait(timeout=5.0)
        assert link.transport.seen_intervals == [0.5, 0.1]
        link.close()

    def test_returns_false_and_only_stores_the_value_when_nothing_is_polling(self):
        """A diagram still connecting, or already disconnected, has no poll
        thread to restart -- the value must still be kept for the next
        `_start_polling()`, per `RelayLink._poll_interval`."""
        link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                         transport=_FakeTransport())
        applied = link.set_poll_interval(0.2)
        assert applied is False
        assert link.poll_interval == 0.2

    def test_the_zombie_guard_also_only_stores_the_value(self):
        """A poll thread that ignored `stop` (survived the join) still owns
        the socket; `set_poll_interval` must not stack a second reader on
        top of it, same guard as `pause_polling`."""
        link = _connected_link()

        class NeverDies(threading.Thread):
            def is_alive(self):
                return True

        link._poll_dying = NeverDies(target=lambda: None)
        applied = link.set_poll_interval(0.3)
        assert applied is False
        assert link.poll_interval == 0.3
        # cleanup: let the real test process exit without a lingering thread
        link._poll_dying = None
        link.close()


class TestSetPollIntervalDoesNotRaceClose:
    """The regression from the fix-round-1 review: `/period` racing
    `/disconnect` must not spawn a poll thread after `close()` has run."""

    def test_close_cannot_interleave_with_the_stop_and_restart(self):
        link = _connected_link()

        # Force the exact interleaving the bug depended on: suspend
        # `set_poll_interval`'s call to `_start_polling()` right at the point
        # where the OLD code had already released `self._lock`. With the fix,
        # that call now happens INSIDE the lock, so a concurrent `close()`
        # must block until this thread finishes -- it cannot possibly see
        # "nothing to stop" and finish closing the transport first.
        resume = threading.Event()
        entered = threading.Event()
        real_start_polling = link._start_polling

        def blocking_start_polling():
            entered.set()
            resume.wait(timeout=5.0)
            real_start_polling()

        link._start_polling = blocking_start_polling

        setter = threading.Thread(target=link.set_poll_interval, args=(0.1,))
        setter.start()
        assert entered.wait(timeout=5.0)

        closer = threading.Thread(target=link.close)
        closer.start()
        time.sleep(0.05)
        assert not link.transport.close_called.is_set(), (
            "close() ran while set_poll_interval's stop-and-restart was "
            "still in flight -- the lock did not serialize them")
        assert closer.is_alive()

        resume.set()
        setter.join(timeout=5.0)
        closer.join(timeout=5.0)

        assert not setter.is_alive()
        assert not closer.is_alive()
        assert link.transport.close_called.is_set()
        # No orphaned thread left dangling on a closed transport: `close()`
        # ran AFTER the restart and correctly stopped the freshly started one.
        assert link._poll_thread is None
        assert link._poll_stop is None
