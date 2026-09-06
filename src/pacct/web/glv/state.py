"""Last snapshot read from a relay.

It lives here, and not in the diagram, because what writes into it are the
`poll_loop*` -- one thread per RELAY, not per drawing. Two diagrams open on
the same relay read the same LiveState, which is right: the Relay Word is the
relay's.
"""

from __future__ import annotations

import threading
import time


class LiveState:
    """Keeps the last snapshot of values read from the relay."""
    def __init__(self):
        self.lock = threading.Lock()
        # Counts READINGS, so a thread can block on one instead of asking for
        # one. The condition shares `self.lock` on purpose: the four poll
        # loops already write their values under it and `mark_updated` is
        # documented as being called with it in hand, which is exactly the
        # critical section the notify has to happen inside. A waiter releases
        # the lock while it sleeps, so `snapshot()` is never held up by it.
        self._changed = threading.Condition(self.lock)
        self.version = 0
        self.digitals: dict[str, int] = {}
        self.analogs: dict[str, float] = {}
        # A reading is stamped by TWO clocks, and the second is not a luxury.
        # The wall clock (`last_update_ts`) is the time of day; the monotonic
        # one (`last_update_mono`) is the only one you can measure AGE with.
        # Measured on this machine: the WSL clock was 82.5 s behind the
        # Windows clock, and the browser (which runs on Windows) did
        # `Date.now()/1000 - ts` -- 82.5 s of "stale values" over a perfect
        # link, and a screen that looked frozen because the subtraction
        # crosses two clocks that do not agree. What answers the age now is
        # the server, with the clock that does not run backwards.
        self.last_update_ts = 0.0
        self.last_update_mono = 0.0
        self.error = ""
        # Bits of the page the user is looking at. Only the `tar_digitals`
        # mode (3xx) uses it: there each Relay Word row costs ~200ms of round
        # trip, so reading the whole diagram every turn would be unworkable --
        # we read only what is on screen. Filled by the /values handler.
        self.wanted_bits: set[str] = set()

    def set_wanted_bits(self, bits) -> None:
        with self.lock:
            self.wanted_bits = {b.upper() for b in bits}

    def mark_updated(self) -> None:
        """Stamps the reading on both clocks. Call it WITH `self.lock` in
        hand -- the four polling loops already write the values under it, and
        the stamp has to go into the same critical section as they do."""
        self.last_update_ts = time.time()
        self.last_update_mono = time.monotonic()
        self._bump()

    def _bump(self) -> None:
        """Count one change and release everyone waiting for it.

        Call it WITH `self.lock` held -- `Condition.notify_all` requires it,
        and every caller is already inside that critical section.
        """
        self.version += 1
        self._changed.notify_all()

    def wait_for_change(self, last_version: int, timeout: float):
        """Block until `version` leaves `last_version`, or until `timeout`.

        Returns the new version, or `None` if the timeout expired with
        nothing having moved. A caller whose `last_version` is already stale
        is answered immediately and never parked -- that is the race between a
        reading landing and the next turn of an `/events` loop starting.

        The timeout is the heartbeat, not a safety net: the badge shows the
        reading's AGE, which has to keep ageing on screen while no bit moves.
        """
        with self._changed:
            if self.version != last_version:
                return self.version
            self._changed.wait(timeout)
            return self.version if self.version != last_version else None

    def snapshot(self):
        with self.lock:
            return {
                "digitals": dict(self.digitals),
                "analogs": {k: (str(v) if not isinstance(v, (int, float)) else v)
                            for k, v in self.analogs.items()},
                "ts": self.last_update_ts,
                # The age in seconds, measured HERE. `None` while nothing has
                # been read -- which is different from "zero seconds", and the
                # screen treats it as such.
                "age": (time.monotonic() - self.last_update_mono
                        if self.last_update_mono else None),
                "error": self.error,
            }

    def clear(self) -> None:
        """Puts everything back to indeterminate.

        Called on disconnect: the screen can never keep showing a value that
        is not being read now. `wanted_bits` stays -- it is what the open page
        asked for, not a value that was read.
        """
        with self.lock:
            self.digitals = {}
            self.analogs = {}
            self.last_update_ts = 0.0
            self.last_update_mono = 0.0
            self.error = ""
            # A disconnect is a change like any other: the screen has to
            # repaint to indeterminate. Without this the last drawing stays
            # up with nothing to trigger the repaint -- a value nobody read.
            self._bump()
