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
