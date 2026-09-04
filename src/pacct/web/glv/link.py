"""One connection per relay, shared by the diagrams that ask for it.

A SEL relay accepts few simultaneous sessions, so the connection cannot belong
to the diagram: it is the PROCESS's, keyed by `ip:porta` and refcounted. The
second diagram asking for the same relay joins the existing connection; it
only drops when the last one lets go.

The `LiveState` lives here, and not in the diagram, because what writes into
it are the polling threads -- one per connection. Two diagrams on the same
relay read the same state, which is right: the Relay Word is the relay's.

The PROTOCOL does not live here. `RelayLink` is the shell: identity, refcount,
`LiveState`, watchdog and the polling thread. What knows how to talk to the
relay is the transport (`transport/telnet.py`, and tomorrow the MMS one),
behind the Protocol of `transport/__init__.py`. The shell never asks which
transport it is: when it needs to abort a stuck setup, it calls
`transport.abort()`, because how you wake a hanging read is the protocol's
business -- on telnet, closing the socket is the only thing that makes
selprotopy raise.

Who locks what:

    LinkPool._lock            the `key -> RelayLink` map and EVERY transition
                              of `owners`. Never held during
                              connect()/close()/discovery.
    RelayLink._lock           the transport, the polling thread, the stop
                              event and the `_closed` flag. It is the
                              LIFECYCLE lock: it is only held for short
                              operations, never for a conversation with the
                              relay.
    RelayLink._discovery_lock one discovery at a time. It is this one, and
                              not the one above, that stays held during the
                              bit sweep -- which on a cold 3xx costs ~90s of
                              `TAR`.
    LiveState.lock            the values.

This is how `Desconectar` stopped looking stuck: `prepare_bits` held the
`_lock` during the whole `transport.prepare_bits(...)`, so a
`POST /disconnect` -> `pool.release` -> `link.close()` sat BLOCKED there
until discovery finished. Splitting the two locks reopens the race
`pause_polling` had (the restart in the `finally` happens outside `_lock`), and
that is why `_closed` came in the same move: `close()` marks it under `_lock`
and `_start_polling` checks it, refusing to bring a reader up on a closed link
-- the same refusal it already makes for a zombie thread.

And the refcount, which is where the risk lives: `owners` is a SET of diagram
ids, and not an integer. Adding or removing the same owner twice is
idempotent, so a double click on Conectar/Desconectar cannot close the telnet
under a live diagram nor leave a connection dangling.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from pacct.web.glv.poll import FirstTimeLog
from pacct.web.glv.state import LiveState
from pacct.web.glv.transport import (  # noqa: F401  (re-exported)
    MODE_FAST_METER,
    MODE_TAR,
    MODE_TARGET,
    SCAN_TELNET,
    pick_transport,
)

# Ceiling for the setup (telnet + login + autoconfig). It does not cover bit
# discovery, which takes minutes of its own accord on an uncached FID.
SETUP_TIMEOUT = 60.0

# How long to wait for the polling thread to die. It has to be LONGER than the
# longest turn of a poll loop: a read sits up to `RESPONSE_DEADLINE_S` (3.0s) +
# `DRAIN_DEADLINE_S` (0.3s) of `poll.py` inside a wait that does not look at
# the stop event. With the 2.0s of before, the join came back with the thread
# STILL ALIVE and the next `_start_polling` put a second reader on the same
# telnet -- which is exactly what stopping the polling exists to avoid.
POLL_JOIN_TIMEOUT = 4.0
# How long `_poll_gave_up` waits for the `_lock` before giving up on it. It
# has to be MUCH smaller than the join above: whoever does the join holds the
# `_lock` during it, so waiting longer is waiting for the join itself to blow.
GIVE_UP_LOCK_TIMEOUT = 0.25


class TooManyLinks(RuntimeError):
    """Ceiling of simultaneous connections reached."""


class PollingWedged(RuntimeError):
    """The previous polling turn did not die, and so nobody else can talk to
    the relay now: two conversations on the same telnet get scrambled."""


def _default_transport(*, ip, port, acc_password, relay_model, logger):
    """The usual transport: SEL Fast Message over telnet.

    A factory, and not an instance, because only in `connect()` are the
    password and the relay model known -- `LinkPool` creates the link before
    that.
    """
    return pick_transport(SCAN_TELNET, ip=ip, port=port,
                          acc_password=acc_password, relay_model=relay_model,
                          logger=logger)


class RelayLink:
    """One connection with a relay, shared by N diagrams."""

    def __init__(self, ip: str, port: int, logger, pool=None, transport=None,
                 make_transport=None):
        self.ip = ip
        self.port = port
        self.pool = pool
        self.key = f"{ip}:{port}"
        self.logger = logger
        self.state = LiveState()
        # The transport may arrive ready (tests, a mode chosen on the screen)
        # or be built in connect(), when the password and the model show up.
        self.transport = transport
        self._make_transport = make_transport or _default_transport
        self.fid = ""
        self.devid = ""
        self.mode = MODE_TARGET
        self.error = ""
        self._connected = False
        # Set when connect() finishes, with or without an error. Whoever
        # joins an existing connection waits here instead of opening a second
        # telnet.
        self.ready = threading.Event()
        # diagram ids; refs == len(owners). Only the LinkPool touches it.
        self.owners: set = set()
        self._lock = threading.RLock()
        # One discovery at a time, and ONLY that -- separate from the
        # lifecycle `_lock` because a bit sweep talks to the relay for minutes.
        self._discovery_lock = threading.RLock()
        # Marked by `close()`. A closed link brings no reader up, not even
        # through the `finally` of a `pause_polling` that was in flight.
        self._closed = False
        # Diagnostics worth logging once per CONNECTION. It lives here, and
        # not in the poll functions, because the GLV opens N diagrams over N
        # relays: a module-level flag gave the logs to the first relay
        # connected after the restart and silence to all the others.
        self._once = FirstTimeLog(logger)
        self._poll_thread: threading.Thread | None = None
        self._poll_stop: threading.Event | None = None
        # Polling thread that did not die inside the join. While it lives,
        # no other one comes up.
        self._poll_dying: threading.Thread | None = None
        self._poll_interval = 0.5
        # owner -> bits of the open page in that diagram. The TAR mode (3xx)
        # reads only what is on screen; with two diagrams on the same relay,
        # one would wipe the other's list if it were not for the union.
        self._wanted: dict = {}

    # -- lifecycle (only the LinkPool creates and destroys) ------------------

    def start_connect(self, **kwargs) -> None:
        """Connects on the LINK's thread, and not on the caller's.

        A `selprotopy` stuck on a read does not wake even when the socket is
        closed (it swallows the exception and tries again), so the caller's
        thread would never come back to release the reference. Here what gets
        stuck is this thread, which owns nothing: the diagrams wait on
        `ready`, which the watchdog sets even when the setup hangs.
        """
        threading.Thread(target=self.connect, kwargs=kwargs, daemon=True,
                         name=f"glv-link-{self.key}").start()

    def connect(self, *, relay_model=None, poll_interval: float,
                acc_password: str = "", job=None,
                setup_timeout: float = SETUP_TIMEOUT) -> None:
        """Connects, discovers the relay's bits and brings the polling up.

        It does not raise: a failure becomes `self.error`, and the caller
        decides what to do. The diagram stays open and disconnected, with the
        reason on the badge -- the same thing the setup did when it fell back
        to "modo desenho".

        `setup_timeout` covers only the setup (telnet + login + autoconfig),
        and not bit discovery, which on an uncached FID takes minutes of its
        own accord. Without it, a peer that accepts the connection and never
        answers leaves the diagram on "conectando" forever -- and holding one
        of the slots of the connection ceiling.
        """
        logger = self.logger
        try:
            self._poll_interval = poll_interval
            if self.transport is None:
                self.transport = self._make_transport(
                    ip=self.ip, port=self.port, acc_password=acc_password,
                    relay_model=relay_model, logger=logger)
            self.mode = self.transport.mode
            if job:
                job.stage("Conectando ao rele...", 8)
            self._connect_with_watchdog(setup_timeout, job)
            with self._lock:
                self._connected = True
            self.fid = self.transport.fid or ""
            self.devid = self.transport.devid or ""
            logger.info("[glv] %s conectado. FID=%s", self.key, self.fid)
            if job:
                # No percentage ON PURPOSE: on a 4xx/3xx the setup has been
                # through `_setup_ascii_reader`, which reports 30, so a 20
                # here made the bar GO BACKWARDS (8 -> 30 -> 20 -> 70). `None`
                # swaps the text and leaves the bar where it is.
                job.stage(f"Conectado ({self.fid or 'rele'})", None)
            self._start_polling()
            logger.info("[glv] %s: polling no modo %s", self.key, self.mode)
        except Exception as e:
            # Failsafe: an IP that does not answer, a timeout, a refused
            # connection, a failed autoconfig. The diagram stays open, only
            # disconnected.
            if not self.error:      # the watchdog may have got there first
                self.error = f"sem conexão com {self.key}: {e}"
            logger.warning("[glv] falha ao conectar em %s: %s", self.key, e)
            self._close_transport()
        finally:
            self.ready.set()

    def _connect_with_watchdog(self, timeout: float, job=None) -> None:
        """`transport.connect` with a stopwatch that aborts it if it hangs.

        The stopwatch is generic; ABORTING is the transport's. On telnet you
        cannot interrupt a blocked read from outside, and closing the socket
        makes it raise -- which is what we want.

        The deadline covers the SETUP, and not bit discovery: what says where
        one ends and the other begins is the transport itself, through
        `setup_done`. Without that event the deadline covers the whole
        `connect()`.
        """
        timed_out = threading.Event()
        setup_done = getattr(self.transport, "setup_done", None)

        def abort():
            if setup_done is not None and setup_done.is_set():
                return          # setup went through; discovery has no deadline
            timed_out.set()
            self.logger.warning(
                "[glv] %s nao respondeu em %.0fs no setup -- abortando.",
                self.key, timeout)
            self.transport.abort()
            # Closing the socket does not always wake the read: selprotopy
            # tries again and can hang. So we release whoever is waiting and
            # give the ceiling slot back right here, without depending on that
            # thread coming back.
            self.error = (f"o rele em {self.key} aceitou a conexao mas nao "
                          f"respondeu em {timeout:.0f}s")
            self.ready.set()
            pool = self.pool
            if pool is not None:
                pool.abandon(self)

        watchdog = threading.Timer(timeout, abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            return self.transport.connect(job)
        except Exception:
            if timed_out.is_set():
                raise TimeoutError(self.error) from None
            raise
        finally:
            watchdog.cancel()

    def close(self) -> None:
        """Stops the polling and closes the connection. Called by the pool,
        outside its lock.

        We do not wait for the zombie thread (the one that survived the join)
        to die: it is a daemon, and giving another deadline to something that
        has already ignored a stop would only delay Desconectar. Closing the
        connection is what really kills it -- its read raises -- and the link
        leaves the pool right after, so nobody reaches this object any more.
        """
        with self._lock:
            self._closed = True
            transport = self.transport
        # Breaks a discovery IN FLIGHT before trying to stop anything else.
        # Without this, disconnecting in the middle of a cold `TAR` (~90s
        # measured on a 3xx) or of an MMS layout fetch only came back when the
        # conversation with the relay ended of its own accord -- on screen,
        # the button looked dead. `abort()` is the transport's because WAKING
        # a hanging read is the protocol's business: on telnet, closing the
        # socket is the only thing that makes selprotopy raise.
        if transport is not None:
            try:
                transport.abort()
            except Exception:
                pass
        with self._lock:
            self._stop_polling()
        self._close_transport()
        with self._lock:
            self._reap_dying()
        self.state.clear()

    def _close_transport(self) -> None:
        with self._lock:
            self._connected = False
            transport = self.transport
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    # -- discovery ----------------------------------------------------------

    def prepare_bits(self, names, job=None) -> int:
        """Discovers/maps bits, with polling stopped FOR AS LONG AS NEEDED.

        Stopping is mandatory on telnet: it is a single stream, and
        interleaving `TAR <name>` with the Fast Meter pipeline scrambles both
        answers. On MMS it was for the same underlying reason -- the py61850
        client is not thread-safe (one socket, one invoke counter) -- but today
        the MMS transport does not ask for the `pause`: since the reading
        became leaf by leaf, its `prepare_bits` no longer talks to the relay,
        and mapping the bits of a second diagram does not cost the first one's
        reader a turn.

        It is also what makes a SECOND diagram work on a connection that
        already exists: it brings bits nobody has asked the relay for yet.

        What stops is the shell (the thread is its own), but WHEN to stop is
        the transport's: what knows whether it will really talk to the relay is
        the transport. That is why `pause` goes as an argument instead of
        wrapping the call -- on a 7xx the digitals come from the Fast Meter and
        there is nothing to discover, and on a FID with a complete cache there
        is no missing bit. In those cases the call returns without entering the
        `pause`, and the polling thread never even finds out. Wrapping here
        brought the reader down and up on every 751 connection, for nothing.
        """
        with self._discovery_lock:
            with self._lock:
                transport, closed = self.transport, self._closed
            if transport is None or closed:
                return 0
            return transport.prepare_bits(names, job=job,
                                          pause=self.pause_polling)

    @contextmanager
    def pause_polling(self):
        """Stops the polling on entry and brings it back up on exit, if it was
        running.

        A thread that has already ignored a stop does not stop: `_poll_dying`
        keeps the one that survived the join, and while it lives nobody talks
        to the relay. Without this check the hole came back from the other side
        -- with `_poll_thread` already cleared, `was_polling` was False, the
        pause did nothing, and the discovery went to the same socket the zombie
        is still reading. You cannot pause what does not listen: you can only
        REFUSE, and say so in the log.
        """
        with self._lock:
            dying = self._reap_dying()
            if dying is not None:
                self.logger.warning(
                    "[glv] %s: a volta de polling anterior ainda nao terminou; "
                    "recusando falar com o rele agora.", self.key)
                raise PollingWedged(
                    f"a leitura anterior de {self.key} não terminou; "
                    f"desconecte e conecte o diagrama de novo")
            was_polling = self._poll_thread is not None
            if was_polling:
                self._stop_polling()
        try:
            yield
        finally:
            if was_polling:
                self._start_polling()

    def _reap_dying(self):
        """Forgets the zombie thread if it has finally died; returns the one
        that is still alive."""
        dying = self._poll_dying
        if dying is not None and not dying.is_alive():
            self._poll_dying = dying = None
        return dying

    def ensure_bits(self, names, job=None) -> int:
        return self.prepare_bits(names, job=job)

    # -- polling ------------------------------------------------------------

    def _start_polling(self) -> None:
        # EVERYTHING under the `_lock`: it is reentrant, so
        # `set_poll_interval` stays atomic, and callers from outside (the
        # `finally` of `pause_polling`, `connect()`) get to read `_closed`
        # without a race.
        with self._lock:
            # A closed link brings no reader up. It is the other half of
            # splitting off the `_discovery_lock`: now that `close()` no longer
            # waits for the discovery to finish, it can arrive BEFORE the
            # restart `pause_polling` does in its `finally` -- and without this
            # guard that restart would put a new thread on a closed transport.
            if self._closed:
                self.logger.info(
                    "[glv] %s: link fechado; nao subo polling.", self.key)
                return
            # One reader per connection. If the previous turn did not die
            # inside the join, bringing another up puts two threads on the same
            # telnet and the same memoised `FastMessageChannel`, and the
            # answers get scrambled. Better no reading at all (and say so in
            # the log) than reading wrong.
            if self._reap_dying() is not None:
                self.logger.warning(
                    "[glv] %s: a volta de polling anterior ainda nao terminou; "
                    "nao subo um segundo leitor no mesmo telnet.", self.key)
                return
            if self.transport is None:
                return
            stop = threading.Event()
            thread = threading.Thread(
                target=self._poll_runner,
                args=(stop, self._poll_interval),
                daemon=True, name=f"glv-poll-{self.key}")
            self._poll_stop, self._poll_thread = stop, thread
            thread.start()

    def _poll_runner(self, stop, interval: float) -> None:
        """The transport's loop, plus what to do if it GIVES UP.

        The three telnet loops set `state.error` and keep spinning; the MMS one
        ends the turn on an `Iec61850Error`, because a dropped association does
        not come back on its own and insisting on a dead socket only fills the
        log. The sentence in the spec ("a read error sets `state.error` and
        stops the loop, as telnet does") is wrong about telnet, and the
        implementation followed the words.

        Stopping is not the problem -- the problem is stopping in SILENCE: the
        link went on with `_connected = True`, the tab stayed LIVE with
        "Desconectar", and `state.digitals` stayed FROZEN on the last reading,
        with the SVG painting those colours under a red badge. Frozen-and-old
        is the closest this branch gets to showing on screen a value nobody
        read.
        """
        try:
            self.transport.poll(self.state, interval, stop, self._once)
        except Exception as e:      # the transport should not let this leak
            with self.state.lock:
                self.state.error = f"leitura: {e}"
            self.logger.exception("[glv] %s: a thread de leitura morreu",
                                  self.key)
        if stop.is_set():
            return                  # stop asked for: nothing to announce
        self._poll_gave_up(stop)

    def _poll_gave_up(self, stop) -> None:
        """The reading ended on its own: the diagram goes back to
        indeterminate.

        The reason (the `state.error` the loop left) is preserved, so the
        screen shows WHY it stopped instead of just going blank. Whoever
        arrives later -- a `close()`, or a restart that has already swapped the
        stop event -- touches nothing.
        """
        # It does NOT wait for the `_lock`. Whoever holds it at this instant
        # is doing one of two things, and both already make this update
        # pointless: `close()` (which sets `_closed`) or a `_stop_polling`
        # followed by a restart (which swaps `_poll_stop`) -- exactly the two
        # guards just below. And waiting would be worse than useless:
        # `_stop_polling` does the `join` INSIDE the lock, so waiting here
        # turns into the join blowing. Measured under forced interleaving:
        # 4.00 s of Desconectar stuck (the whole POLL_JOIN_TIMEOUT), ending in
        # the wedged-reader warning -- which is the symptom that splitting off
        # the `_discovery_lock` had just removed, back through a narrower door.
        if not self._lock.acquire(timeout=GIVE_UP_LOCK_TIMEOUT):
            self.logger.info(
                "[glv] %s: a leitura parou, mas o link ja esta sendo fechado "
                "ou reiniciado por outra thread; deixo com ela.", self.key)
            return
        try:
            if self._closed or self._poll_stop is not stop:
                return
            self._connected = False
            self._poll_stop = self._poll_thread = None
        finally:
            self._lock.release()
        reason = self.state.snapshot().get("error") or "a leitura parou"
        self.state.clear()
        with self.state.lock:
            self.state.error = reason
        self.error = reason
        self.logger.warning(
            "[glv] %s: a leitura parou (%s); o diagrama fica desconectado e "
            "tudo indeterminado.", self.key, reason)

    def set_poll_interval(self, seconds: float) -> bool:
        """Swaps the polling period in flight, restarting the thread if it is
        running -- it is the only way to change the `interval` that a thread
        already started got as an argument and never read again.

        The stop-and-restart happens ALL inside `self._lock`, without letting
        go halfway: it is what stops `close()` from getting in between the stop
        and the restart and seeing "nothing to stop", letting the restart here
        bring a new thread up on a transport `close()` has already closed
        underneath. `close()` also takes this lock to stop the polling, so the
        two never interleave -- one waits for the other to finish the whole
        operation.

        Same guard as `pause_polling`: a turn that survived the join still owns
        the telnet/socket, so we do not bring a second reader up on top of it.
        In that case the new period only applies from the next time the polling
        comes up of its own accord.

        Returns True if it restarted the polling NOW (the new period already
        applies); False if it only stored the value for when the polling comes
        up again on its own (nothing running now, or a zombie reading that has
        not died yet).
        """
        with self._lock:
            self._poll_interval = seconds
            dying = self._reap_dying()
            applies_now = dying is None and self._poll_thread is not None
            if applies_now:
                self._stop_polling()
                self._start_polling()
            return applies_now

    @property
    def poll_interval(self) -> float:
        """The period configured now, in seconds -- for whoever needs to show
        the value in force without twisting `_poll_interval`'s arm."""
        return self._poll_interval

    def _stop_polling(self) -> None:
        stop, thread = self._poll_stop, self._poll_thread
        self._poll_stop = self._poll_thread = None
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=POLL_JOIN_TIMEOUT)
            if thread.is_alive():
                # Kept for `_start_polling` to refuse: a thread that did not
                # die still owns the telnet.
                self._poll_dying = thread
                self.logger.warning(
                    "[glv] %s: a thread de polling nao terminou em %.1fs.",
                    self.key, POLL_JOIN_TIMEOUT)

    # -- state --------------------------------------------------------------

    def set_wanted_bits(self, owner: str, bits) -> None:
        """Bits of the open page in a diagram. Publishes the owners' UNION."""
        with self._lock:
            if bits:
                self._wanted[owner] = set(bits)
            else:
                self._wanted.pop(owner, None)
            union = set()
            for s in self._wanted.values():
                union |= s
        self.state.set_wanted_bits(union)

    @property
    def connected(self) -> bool:
        return self._connected

    def info(self) -> dict:
        return {
            "ip": self.ip, "port": self.port, "key": self.key,
            "fid": self.fid, "devid": self.devid, "mode": self.mode,
            "refs": len(self.owners), "connected": self.connected,
            "error": self.error,
        }

class LinkPool:
    """The process's `ip:porta -> RelayLink` map."""

    def __init__(self, logger, max_links: int = 4):
        self.logger = logger
        self.max_links = max_links
        self._links: dict[str, RelayLink] = {}
        self._lock = threading.RLock()

    def acquire(self, ip: str, port: int, owner: str,
                make_transport=None) -> tuple[RelayLink, bool]:
        """Returns `(link, created_now)`.

        `created_now=True` means the caller has to run `link.connect(...)`;
        `False`, that waiting on `link.ready` is enough. Raises TooManyLinks
        when a NEW key would blow the ceiling -- joining an existing
        connection never blows it, because it costs the relay nothing.

        `make_transport` is the factory the link will use in `connect()`.
        Absent, it is the usual telnet one -- this is how the scan mode gets
        into the live path, and not only into the tests.
        """
        key = f"{ip}:{port}"
        with self._lock:
            link = self._links.get(key)
            if link is not None:
                link.owners.add(owner)
                self.logger.info("[glv] %s: %s entrou na conexao (%d diagrama(s))",
                                 key, owner, len(link.owners))
                return link, False
            if len(self._links) >= self.max_links:
                raise TooManyLinks(
                    f"limite de {self.max_links} conexões simultâneas atingido; "
                    f"desconecte outro diagrama antes")
            link = RelayLink(ip, port, self.logger, pool=self,
                             make_transport=make_transport)
            link.owners.add(owner)
            self._links[key] = link
            self.logger.info("[glv] %s: conexao nova pedida por %s", key, owner)
            return link, True

    def release(self, link: RelayLink, owner: str) -> None:
        """Removes `owner` from the link. At zero it closes -- outside the
        lock."""
        with self._lock:
            link.owners.discard(owner)
            if link.owners:
                self.logger.info("[glv] %s continua com %d diagrama(s)",
                                 link.key, len(link.owners))
                link.set_wanted_bits(owner, set())
                return
            # It leaves the map INSIDE the lock: a concurrent acquire() must
            # not join a link that is closing.
            self._links.pop(link.key, None)
        link.set_wanted_bits(owner, set())
        link.close()
        self.logger.info("[glv] %s fechado (ultimo diagrama saiu)", link.key)

    def abandon(self, link: RelayLink) -> None:
        """Takes out of the map a link whose setup hung, without waiting for
        the owners.

        The `release()` calls that arrive later stay valid: they only touch
        `owners` and a `pop` that no longer finds anything.
        """
        with self._lock:
            if self._links.get(link.key) is link:
                self._links.pop(link.key, None)
                self.logger.warning(
                    "[glv] %s tirado do pool (setup travado); a vaga volta pro "
                    "teto de %d conexoes", link.key, self.max_links)

    def snapshot(self) -> list:
        with self._lock:
            return [lk.info() for lk in self._links.values()]
