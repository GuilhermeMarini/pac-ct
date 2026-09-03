"""The Transport seam: RelayLink drives a transport it does not know."""
from __future__ import annotations

import logging
import threading

import pytest

from pacct.web.glv import link as link_mod
from pacct.web.glv.link import PollingWedged, RelayLink


class FakeTransport:
    """Records the order RelayLink calls it in."""
    mode = "fake"

    def __init__(self):
        self.fid = "FAKE-FID"
        self.devid = "FAKE-DEV"
        self.calls: list = []
        self.polled = threading.Event()

    def connect(self, job=None):
        self.calls.append("connect")

    def abort(self):
        self.calls.append("abort")

    def close(self):
        self.calls.append("close")

    def prepare_bits(self, names, job=None, pause=None):
        self.calls.append(("prepare_bits", tuple(sorted(names))))
        # A transport with something to ask the relay enters the pause; the
        # shell is what stops the thread, because the thread is the shell's.
        with pause():
            return len(names)

    def poll(self, state, interval, stop, once):
        self.calls.append("poll")
        self.polled.set()
        stop.wait(timeout=5.0)

    def coverage_for(self, bits):
        return None


def test_connect_uses_the_transport_and_starts_polling():
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=FakeTransport())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.ready.is_set()
    assert link.error == ""
    assert link.fid == "FAKE-FID"
    assert link.transport.polled.wait(timeout=5.0)
    assert "connect" in link.transport.calls
    link.close()
    assert "close" in link.transport.calls


def test_prepare_bits_stops_polling_around_the_call():
    """The telnet stream cannot interleave discovery with the poll pipeline,
    and py61850's client is not thread-safe. The shell must pause either way."""
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=FakeTransport())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polled.wait(timeout=5.0)
    link.prepare_bits({"PLT01", "PLT02"})
    calls = link.transport.calls
    i = calls.index(("prepare_bits", ("PLT01", "PLT02")))
    # polling ran before, and was restarted after
    assert "poll" in calls[:i]
    assert "poll" in calls[i + 1:]
    link.close()


def test_a_failing_connect_leaves_the_link_ready_and_errored():
    class Boom(FakeTransport):
        def connect(self, job=None):
            raise RuntimeError("sem rota")

    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=Boom())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.ready.is_set()
    assert "sem rota" in link.error
    assert not link.connected


class QuietTransport(FakeTransport):
    """A transport with nothing to ask the relay -- a 7xx, whose digitals ride
    inside the Fast Meter banks, or any relay whose FID cache already has every
    bit the page wants. It never enters the pause."""

    def prepare_bits(self, names, job=None, pause=None):
        self.calls.append(("prepare_bits", tuple(sorted(names))))
        return 0


class StubbornTransport(FakeTransport):
    """A poll round that is not looking at `stop`: a real one can sit up to
    `RESPONSE_DEADLINE_S` inside a read that never checks it."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def poll(self, state, interval, stop, once):
        self.calls.append("poll")
        self.polled.set()
        self.release.wait(30.0)


def test_prepare_bits_leaves_the_poll_thread_alone_when_nobody_asks_it_to_pause():
    """The pause is the transport's to enter, and a 751 never does: stopping
    the reader anyway costs a restart on a telnet that was fine, and
    `_stop_polling`'s join can return with the old thread still alive."""
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=QuietTransport())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polled.wait(timeout=5.0)
    thread = link._poll_thread
    assert link.prepare_bits({"LT01", "LT02"}) == 0
    assert link._poll_thread is thread          # a mesma thread, nao outra
    assert link.transport.calls.count("poll") == 1
    link.close()


def test_the_join_deadline_outlasts_one_poll_round():
    """`_stop_polling` used to join for 2.0s while one round can spend 3.3s
    inside a wait that never looks at the stop event."""
    from pacct.web.glv.poll import DRAIN_DEADLINE_S, RESPONSE_DEADLINE_S
    assert link_mod.POLL_JOIN_TIMEOUT > RESPONSE_DEADLINE_S + DRAIN_DEADLINE_S


def test_a_poll_thread_that_would_not_die_blocks_a_second_reader(monkeypatch):
    """Two readers on one telnet share the memoised `FastMessageChannel` and
    scramble each other's replies -- which is what pausing exists to avoid. No
    reading is better than wrong reading, and the log says so."""
    monkeypatch.setattr(link_mod, "POLL_JOIN_TIMEOUT", 0.05)
    transport = StubbornTransport()
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=transport)
    link.connect(relay_model=None, poll_interval=0.05)
    assert transport.polled.wait(timeout=5.0)
    link.prepare_bits({"PLT01"})
    assert transport.calls.count("poll") == 1
    assert link._poll_thread is None
    transport.release.set()
    link.close()


class WedgedThenAsking(StubbornTransport):
    """Wedges its poll thread, then asks for discovery a second time -- the
    second diagram opening its bits on a connection that is already stuck."""

    def prepare_bits(self, names, job=None, pause=None):
        self.calls.append(("prepare_bits", tuple(sorted(names))))
        with pause():
            self.calls.append("discover")     # traffic on the telnet
            return len(names)


def test_a_wedged_link_refuses_the_next_discovery_instead_of_scrambling_it(
        monkeypatch, caplog):
    """After a join times out the thread is remembered in `_poll_dying` and
    `_poll_thread` is None -- so `was_polling` would read False and the pause
    would do nothing at all, sending `discover_bits` down a socket the zombie
    is still reading. You cannot pause a thread that already ignored a stop:
    the only honest answer is to refuse, out loud."""
    monkeypatch.setattr(link_mod, "POLL_JOIN_TIMEOUT", 0.05)
    transport = WedgedThenAsking()
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=transport)
    link.connect(relay_model=None, poll_interval=0.05)
    assert transport.polled.wait(timeout=5.0)

    link.prepare_bits({"PLT01"})              # 1a: passa, e deixa a zumbi
    assert link._poll_thread is None
    assert link._poll_dying is not None and link._poll_dying.is_alive()
    assert transport.calls.count("discover") == 1

    with caplog.at_level(logging.WARNING):
        with pytest.raises(PollingWedged):
            link.prepare_bits({"PLT02"})      # 2a: recusa
    assert transport.calls.count("discover") == 1     # nao falou com o rele
    assert "ainda nao terminou" in caplog.text

    transport.release.set()
    link.close()


def test_the_refusal_lifts_when_the_zombie_finally_dies(monkeypatch):
    """`_poll_dying` is reaped, not sticky: once the thread really ends, the
    next discovery goes through instead of being refused forever. The reader
    itself still only comes back on a reconnect -- no reading beats a wrong
    reading, and that trade is deliberate."""
    monkeypatch.setattr(link_mod, "POLL_JOIN_TIMEOUT", 0.05)
    transport = WedgedThenAsking()
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=transport)
    link.connect(relay_model=None, poll_interval=0.05)
    assert transport.polled.wait(timeout=5.0)
    link.prepare_bits({"PLT01"})
    transport.release.set()                   # a zumbi termina
    link._poll_dying.join(timeout=5.0)
    assert link.prepare_bits({"PLT02"}) == 1
    assert transport.calls.count("discover") == 2
    link.close()


# ---- close() must never queue behind a conversation with the relay ---------
#
# The ledger of this feature names a race in `pause_polling` (the restart
# happens outside the lock). That race was NOT reachable: `prepare_bits` held
# `self._lock` -- an RLock -- across the entire `transport.prepare_bits(...)`,
# so `pause_polling`'s inner acquire only went 2 -> 1 and never let go.
#
# The reachable defect is its mirror image: `close()` waited behind the WHOLE
# discovery. `POST /disconnect` -> `pool.release` -> `link.close()` blocked at
# `with self._lock` for a full cold-3xx TAR sweep (~90 s measured) or an MMS
# layout fetch, and on screen Desconectar looked dead. Moving discovery onto
# its own lock fixes that AND makes the ledger's race real, so the `_closed`
# guard lands in the same change.

def _link_with(transport, poll_interval=0.05):
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=transport)
    link.connect(relay_model=None, poll_interval=poll_interval)
    assert transport.polled.wait(timeout=5.0)
    return link


class _SlowDiscovery(FakeTransport):
    """A discovery that will not finish until the test lets it."""

    def __init__(self, use_pause=False, abort_breaks_it=False):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.use_pause = use_pause
        self.abort_breaks_it = abort_breaks_it

    def prepare_bits(self, names, job=None, pause=None):
        self.calls.append("prepare_bits")
        if self.use_pause:
            with pause():
                self.started.set()
                self.release.wait(timeout=10.0)
        else:
            self.started.set()
            self.release.wait(timeout=10.0)
        return 0

    def abort(self):
        self.calls.append("abort")
        if self.abort_breaks_it:
            self.release.set()


def test_close_does_not_queue_behind_a_discovery_in_flight():
    t = _SlowDiscovery()
    link = _link_with(t)
    worker = threading.Thread(target=link.prepare_bits, args=({"PLT01"},),
                              daemon=True)
    worker.start()
    assert t.started.wait(timeout=5.0)

    closed = threading.Event()
    threading.Thread(target=lambda: (link.close(), closed.set()),
                     daemon=True).start()
    assert closed.wait(timeout=5.0), \
        "close() ficou esperando a descoberta terminar"

    t.release.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()


def test_close_aborts_a_discovery_in_flight_instead_of_orphaning_it():
    """Telnet breaks a hung read by closing its socket; MMS has
    `client.close()`. Either way it is the TRANSPORT that knows how, which is
    why `close()` asks it instead of inventing a timeout."""
    t = _SlowDiscovery(abort_breaks_it=True)
    link = _link_with(t)
    worker = threading.Thread(target=link.prepare_bits, args=({"PLT01"},),
                              daemon=True)
    worker.start()
    assert t.started.wait(timeout=5.0)

    link.close()
    assert "abort" in t.calls
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "a descoberta ficou orfa depois do close()"


def test_a_close_during_a_paused_discovery_leaves_no_reader_behind():
    """The race the ledger described, now that it is real: `pause_polling`
    restarts the reader in its `finally`, outside the lifecycle lock. If
    `close()` ran in between, that restart would put a fresh thread on a
    transport `close()` had already shut."""
    t = _SlowDiscovery(use_pause=True)
    link = _link_with(t)
    worker = threading.Thread(target=link.prepare_bits, args=({"PLT01"},),
                              daemon=True)
    worker.start()
    assert t.started.wait(timeout=5.0)

    link.close()
    t.release.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert link._poll_thread is None, "subiu um leitor num link fechado"


def test_a_closed_link_refuses_a_new_discovery_too():
    """And refuses it WITHOUT asking the transport: the socket is gone, so a
    discovery there would only hang until its own timeout."""
    t = _SlowDiscovery()
    link = _link_with(t)
    link.close()
    assert link.prepare_bits({"PLT01"}) == 0
    assert "prepare_bits" not in t.calls


# ---- a reader that gives up must not leave the screen frozen --------------
#
# The three telnet loops set `state.error` and keep looping; the MMS one
# returns on `Iec61850Error`, because a dropped association does not come
# back on its own. Stopping is fine -- stopping SILENTLY is not: the link
# stayed `_connected`, the tab stayed LIVE with "Desconectar", and
# `state.digitals` stayed frozen at the last reading with the SVG still
# painting those colours under a red badge.

class _GivesUp(FakeTransport):
    """A loop that reads once and then returns of its own accord."""

    def poll(self, state, interval, stop, once):
        self.calls.append("poll")
        with state.lock:
            state.digitals = {"PLT01": 1, "PLT02": 0}
            state.last_update_ts = 1.0
            state.error = "MMS: association lost"
        self.polled.set()
        return          # sem stop: o transporte desistiu


def test_a_reader_that_gives_up_marks_the_link_disconnected():
    t = _GivesUp()
    link = _link_with(t)
    for _ in range(200):
        if not link.connected:
            break
        threading.Event().wait(0.01)
    assert not link.connected, "a aba continuaria LIVE com 'Desconectar'"
    link.close()


def test_a_reader_that_gives_up_clears_the_frozen_bits_but_keeps_the_reason():
    """Indeterminate says "nobody is reading this"; a frozen 1 says "the relay
    is asserting this bit". In commissioning those are not the same sentence."""
    t = _GivesUp()
    link = _link_with(t)
    for _ in range(200):
        if not link.connected:
            break
        threading.Event().wait(0.01)
    snap = link.state.snapshot()
    assert snap["digitals"] == {}
    assert "association lost" in snap["error"]
    assert "association lost" in link.error
    link.close()


def test_an_ordinary_stop_says_nothing():
    """`_stop_polling` (disconnect, a period change, a pause for discovery)
    also ends the loop -- and must not be reported as the reader giving up."""
    t = FakeTransport()
    link = _link_with(t)
    link.prepare_bits({"PLT01"})            # para e sobe o leitor
    assert link.connected
    assert link.error == ""
    link.close()


# ---- the progress bar must not walk backwards ------------------------------

class _JobRecorder:
    def __init__(self):
        self.stages: list = []

    def stage(self, text, pct=None):
        self.stages.append((text, pct))

    def fraction(self, text, done, total):
        self.stages.append((text, 100.0 * done / total))

    def finish(self, text="Pronto"):
        self.stages.append((text, 100.0))

    def fail(self, error):
        self.stages.append((error, None))


def test_connect_never_reports_a_percentage_lower_than_the_last_one():
    """A 4xx/3xx `connect()` runs `_setup_ascii_reader`, which reports 30.
    The shell then said "Conectado" at 20, so the bar went 8 -> 30 -> 20 -> 70
    -- and a bar that walks backwards reads as something having gone wrong."""
    class Discovering(FakeTransport):
        def connect(self, job=None):
            self.calls.append("connect")
            if job:
                job.stage("Descobrindo bits da regiao TARGET...", 30)

    job = _JobRecorder()
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=Discovering())
    link.connect(relay_model=None, poll_interval=0.05, job=job)
    pcts = [p for _, p in job.stages if p is not None]
    assert pcts == sorted(pcts), job.stages
    link.close()
