"""Server-sent events for the GLV: the server says when, instead of the
browser asking.

Measured end to end before this existed: the relay answers in 1.5 ms,
`d.values(page)` costs 0.03 ms, the HTTP round trip is 1 ms and one repaint in
Chrome is 0.80 ms -- about 3 ms of work inside 100 to 500 ms of waiting for
the next turn of the screen's clock. The WAIT was the whole latency, and
nothing about making the drawing faster touches it. Worse, with MMS reading at
10 ms, 49 of every 50 readings died without ever reaching the drawing, because
nothing between the poll loop and the browser accumulates.

So the screen stops asking. `LiveState.wait_for_change` parks a thread on the
reading itself, and a frame goes out when the relay actually says something.
The heartbeat is not a safety net: the badge shows the reading's AGE, which
has to keep ageing on screen while no bit moves.

The loop is a generator so it can be tested with a 50 ms heartbeat and no
socket. `handler.py` writes what it yields and nothing more.
"""

from __future__ import annotations

import json
import time

#: How long a quiet stream waits before sending anyway, in seconds. It is the
#: age badge's refresh rate, not a protocol floor -- a frame costs 0.9 kB and
#: the client's `rev` check means a heartbeat repaints nothing.
HEARTBEAT_SECONDS = 1.0

#: A stream ends here and the browser's `EventSource` reconnects on its own.
#: A forgotten tab would otherwise hold a `ThreadingHTTPServer` thread for the
#: life of the process.
MAX_STREAM_SECONDS = 300.0


def event_frames(d, page: str, *, heartbeat: float = HEARTBEAT_SECONDS,
                 max_seconds: float = MAX_STREAM_SECONDS):
    """Yield `text/event-stream` frames for one diagram's open page.

    The first frame goes out with no wait at all: a tab that has just
    connected has nothing on screen, and making it wait a heartbeat for the
    current state would be slower than the polling this replaces.
    """
    deadline = time.monotonic() + max_seconds
    while True:
        state = d.state
        # Read the version BEFORE building the payload, never after. A reading
        # that lands while `values()` is running has then already moved the
        # version, so the wait below returns at once instead of parking for a
        # heartbeat on a value we did not send.
        version = state.version
        yield "data: " + json.dumps(d.values(page)) + "\n\n"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        state.wait_for_change(version, timeout=min(heartbeat, remaining))
