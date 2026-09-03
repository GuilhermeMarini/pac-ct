"""The CLI poller measures durations on a clock that cannot jump.

docs/ENGINEERING-NOTES.md records the measurement that forced this everywhere else: on this
machine the WSL wall clock sat 82,5 s behind the Windows host and periodically
resynced, so `time.time()` moved in BOTH directions. A duration taken across a
forward jump reads 82,3 s (every deadline expires at once); across a backward
jump it reads NEGATIVE, and a deadline computed from it sits 82 s in the
future -- the read hangs while a packet capture of the same session shows a
perfect metronome.

The web polling path was converted to `time.monotonic()` when that was
measured. `pacct/cli/runner.py` was not, and carried 23 wall-clock calls, every
one of them a duration or a deadline. This pins the fix.
"""

from __future__ import annotations

import pytest

from pacct.cli import runner


class _Jumpy:
    """A clock whose wall time lurches once, mid-loop; its monotonic never does.

    The jump has to land BETWEEN two readings to reproduce anything: a constant
    offset cancels out in `t1 - t0`, which is why a naive fake clock passes on
    the broken code. The resync happens after `jump_after` calls, exactly as a
    real one does -- somewhere in the middle of a loop that is already running.

    `sleep()` advances both clocks, so a loop that sleeps makes real progress
    and the only question under test is which clock the deadline is read from.
    """

    JUMP = 82.5

    def __init__(self, backwards: bool, jump_after: int = 4):
        self._mono = 1000.0
        self._sign = -1.0 if backwards else 1.0
        self._calls = 0
        self._jump_after = jump_after

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        self._calls += 1
        offset = self._sign * self.JUMP if self._calls > self._jump_after else 0.0
        return self._mono + offset

    def sleep(self, seconds: float) -> None:
        self._mono += seconds


class _Quiet:
    """A connection that never has anything to say."""

    def __init__(self):
        self.reads = 0

    def read_very_eager(self) -> bytes:
        self.reads += 1
        return b""


@pytest.mark.parametrize("backwards", [True, False],
                         ids=["clock-jumps-back", "clock-jumps-forward"])
def test_draining_ends_within_its_own_budget(monkeypatch, backwards):
    """`_drain_connection` must give up after `max_drain`, whatever the wall
    clock does. On the old code a backward jump pushed the deadline 82 s out
    and the loop span ~16.000 more times before returning."""
    clock = _Jumpy(backwards=backwards)
    monkeypatch.setattr(runner, "time", clock)
    conn = _Quiet()

    start = clock.monotonic()
    runner._drain_connection(conn, quiet_period=0.08, max_drain=0.5)
    elapsed = clock.monotonic() - start

    assert elapsed <= 0.5 + 1e-9, (
        f"o dreno levou {elapsed:.2f}s de relogio monotonico, teto 0,50s"
    )
    # 0,08 s of silence at 5 ms per turn: ~16 reads, never thousands.
    assert conn.reads < 200, f"{conn.reads} leituras -- o prazo escorregou"


def test_the_module_reads_no_wall_clock_for_a_duration():
    """The blanket guard: every clock call in the file is monotonic.

    There is no 'hour of the day' anywhere in this module -- it polls a relay
    and prints -- so any `time.time()` here is a duration on the wrong clock.
    """
    import inspect
    import re

    src = inspect.getsource(runner)
    code = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    assert not re.search(r"\btime\.time\(\)", code), (
        "time.time() de volta em cli/runner.py -- ver o docstring deste modulo"
    )
