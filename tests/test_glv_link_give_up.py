"""`RelayLink._poll_gave_up`: the end of a poll loop that stopped by itself.

A poll loop that returns on its own has to take the screen with it -- mark the
link not-connected and clear `LiveState`, keeping the reason -- otherwise the
tab stays LIVE with "Desconectar" while `state.digitals` is frozen on the last
reading and the SVG keeps painting those colours under a red badge.

Doing that means writing link state, which means `self._lock`. But
`_stop_polling` performs its `thread.join(POLL_JOIN_TIMEOUT)` while HOLDING
that same lock, so a give-up that blocks on it turns every close() racing an
association drop into a full `POLL_JOIN_TIMEOUT` stall -- Desconectar looks
dead, and the log blames a wedged reader. That is a lock inversion, and these
tests pin the way out of it: the give-up never waits on the lock, because
whoever holds it is already invalidating the very state the give-up wanted to
write.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from pacct.web.glv.link import (
    GIVE_UP_LOCK_TIMEOUT,
    POLL_JOIN_TIMEOUT,
    RelayLink,
)


class _GivingUpTransport:
    """A transport whose `poll()` returns on its own, like a dropped MMS
    association: `_poll_runner` then calls `_poll_gave_up`."""

    mode = "fake"

    def __init__(self):
        self.fid = "FID"
        self.devid = "DEV"
        self.release = threading.Event()
        self.polling = threading.Event()
        self.closed = threading.Event()

    def connect(self, job=None):
        pass

    def abort(self):
        self.release.set()

    def close(self):
        self.closed.set()

    def prepare_bits(self, names, job=None, pause=None):
        return 0

    def poll(self, state, interval, stop, once):
        with state.lock:
            state.digitals = {"PLT01": 1}
            state.error = "MMS: associação caiu"
            state.last_update_ts = time.time()
        self.polling.set()
        self.release.wait(timeout=10.0)     # devolve = desistiu

    def coverage(self):
        return None


def _link(transport=None) -> RelayLink:
    return RelayLink("192.0.2.10", 102, logging.getLogger("t"),
                     transport=transport or _GivingUpTransport())


def test_the_give_up_does_not_wait_for_a_lock_someone_else_holds():
    """The measured defect: 4.00 s -- exactly POLL_JOIN_TIMEOUT.

    The holder here stands in for `_stop_polling`, which joins the very thread
    that is running this code, while holding the lock it is waiting for.
    """
    link = _link()
    stop = threading.Event()
    link._poll_stop = stop

    holding = threading.Event()
    let_go = threading.Event()

    def holder():
        with link._lock:
            holding.set()
            let_go.wait(timeout=POLL_JOIN_TIMEOUT * 2)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(timeout=5.0)

    # relogio monotonico: NTP anda pra tras e a medicao pode sair negativa
    t0 = time.monotonic()
    link._poll_gave_up(stop)
    elapsed = time.monotonic() - t0

    let_go.set()
    t.join(timeout=5.0)

    assert elapsed < POLL_JOIN_TIMEOUT / 2, (
        f"a desistencia esperou {elapsed:.2f}s pelo lock; e' o join de "
        f"_stop_polling que ela faria estourar")
    assert elapsed == pytest.approx(GIVE_UP_LOCK_TIMEOUT, abs=0.2)


def test_disconnecting_while_the_reader_gives_up_is_not_slow():
    """The whole point, end to end: `close()` racing a loop that is ending.

    Honest about what it is: this does NOT force the interleaving, and it
    passes with the defect in place -- the measured rate was 0 stalls in 150
    natural trials, because close() sets stop microseconds after abort(). The
    test above is the one that catches the inversion; this one guards the
    ordinary path it runs through (abort, join, close, clear) against a fix
    that bought its speed by breaking the shutdown.
    """
    link = _link()
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polling.wait(timeout=5.0)

    # relogio monotonico: NTP anda pra tras e a medicao pode sair negativa
    t0 = time.monotonic()
    link.close()
    elapsed = time.monotonic() - t0

    assert elapsed < POLL_JOIN_TIMEOUT / 2, (
        f"Desconectar levou {elapsed:.2f}s")
    assert link.transport.closed.is_set()
    assert not link.connected


def test_a_give_up_with_nobody_in_the_way_still_takes_the_screen_with_it():
    """The fast path must not have been traded away for the fix: the reason
    survives, the readings do not, and the link stops calling itself
    connected."""
    link = _link()
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polling.wait(timeout=5.0)
    assert link.connected

    link.transport.release.set()            # o loop devolve por conta propria
    for _ in range(100):
        if not link.connected:
            break
        time.sleep(0.05)

    assert not link.connected, "a aba continuaria LIVE com Desconectar"
    snap = link.state.snapshot()
    assert snap["digitals"] == {}, "manteve na tela a ultima leitura congelada"
    assert "associação caiu" in snap["error"], "perdeu o motivo da parada"


def test_a_give_up_from_a_stale_stop_event_changes_nothing():
    """A restart already swapped the stop event: the old reader's give-up must
    not knock the NEW one off the air."""
    link = _link()
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polling.wait(timeout=5.0)

    stale = threading.Event()
    link._poll_gave_up(stale)

    assert link.connected, "a desistencia de um leitor velho derrubou o atual"
    link.close()
