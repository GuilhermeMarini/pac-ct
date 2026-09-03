"""Diagnostics that must fire once per CONNECTION, not once per process.

The GLV opens N diagrams over N relays (`LinkPool` refcounts one telnet per
`ip:porta`). Four one-shot log lines were flags hung on the poll functions
themselves -- ``poll_loop._logged_an_keys = True`` -- which is per process. So
the first relay connected after a restart got the diagnostics and every relay
after it got silence.

That mattered because of what the lines are for. ``[poll] 1a leitura FM: N
analog channels: [...]`` is how you check a relay model JSON's
``analog_name_aliases`` against what the firmware actually exposes; the FM
parse and timeout warnings say what the relay on the other end is doing wrong.
Connect a 487E after a 411L and you got nothing for the 487E.

The polling loops themselves talk to a relay over telnet and cannot be tested
without hardware. What is testable is the scoping, which is where the bug was.
"""

from __future__ import annotations

import logging

import pytest

from pacct.web.glv.link import RelayLink
from pacct.web.glv.poll import FirstTimeLog


class _Recorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logger_and_lines():
    log = logging.getLogger(f"test.{id(object())}")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    rec = _Recorder()
    log.addHandler(rec)
    yield log, rec.lines
    log.removeHandler(rec)


class TestFirstTimeLog:

    def test_the_first_call_logs(self, logger_and_lines):
        log, lines = logger_and_lines
        FirstTimeLog(log).info("an_keys", "primeira leitura")
        assert lines == ["primeira leitura"]

    def test_the_second_call_with_the_same_key_is_silent(self, logger_and_lines):
        """Fails if the seen-set is dropped -- the poll loop calls this every
        iteration, several times a second, for the life of the connection."""
        log, lines = logger_and_lines
        once = FirstTimeLog(log)
        for _ in range(50):
            once.info("an_keys", "primeira leitura")
        assert lines == ["primeira leitura"]

    def test_different_keys_are_independent(self, logger_and_lines):
        """The 7xx loop has three: first-parse, parse-error and timeout. A
        parse error must not suppress the timeout warning."""
        log, lines = logger_and_lines
        once = FirstTimeLog(log)
        once.info("fm_first", "ok")
        once.warning("fm_parse_err", "parse falhou")
        once.warning("fm_timeout", "timeout")
        assert lines == ["ok", "parse falhou", "timeout"]

    def test_two_instances_do_not_share_state(self, logger_and_lines):
        """THE regression. Two relays, two `FirstTimeLog`s, two log lines.

        Fails if the seen-set ever becomes class-level or module-level -- which
        is exactly what the old `getattr(poll_loop, "_logged_an_keys")` was."""
        log, lines = logger_and_lines
        FirstTimeLog(log).info("an_keys", "rele A: 12 canais")
        FirstTimeLog(log).info("an_keys", "rele B: 30 canais")
        assert lines == ["rele A: 12 canais", "rele B: 30 canais"]


class TestRelayLinkScoping:

    def test_each_link_gets_its_own(self):
        """A `RelayLink` is one telnet to one relay, created by `LinkPool` and
        dropped when the last diagram lets go. Scoping the diagnostics to it
        means a genuine reconnect logs again, while `ensure_bits()` restarting
        the polling thread for a second diagram does not -- same connection,
        same relay."""
        log = logging.getLogger("test.links")
        a = RelayLink("10.0.0.1", 23, log)
        b = RelayLink("10.0.0.2", 23, log)
        assert a._once is not b._once

    def test_all_three_poll_modes_receive_it(self):
        """One relay family per loop (4xx target_region, 7xx
        fast_meter_digitals, 3xx tar_digitals) and all three carried a flag.
        Fails if a new mode is added that forgets to pass `once` -- it would
        silently fall back to per-thread scoping.

        The mode dispatch moved to `TelnetTransport.poll` when the transport
        seam was extracted; the flag still belongs to the `RelayLink`, which is
        what makes it one per connection, and reaches the loops as the `once`
        argument of `poll()`. `_start_polling` now hands the thread to
        `_poll_runner`, which is where the flag is read."""
        import inspect

        from pacct.web.glv.transport.telnet import TelnetTransport
        shell = inspect.getsource(RelayLink._poll_runner)
        assert shell.count("self._once") == 1
        body = "\n".join(
            inspect.getsource(TelnetTransport.poll).split("\n")[1:])
        assert body.count("once)") == 3

    def test_a_loop_called_without_one_still_works(self):
        """`once` defaults to None and the loop builds its own, so a direct
        call (a test, a script) does not crash on `None.info`. Same idiom as
        `SessionHandler.job()` being a no-op without an X-Job-Id."""
        import inspect

        from pacct.web.glv import poll
        for name in ("poll_loop", "poll_loop_fastmeter", "poll_loop_tar"):
            src = inspect.getsource(getattr(poll, name))
            assert "if once is None:" in src, name
