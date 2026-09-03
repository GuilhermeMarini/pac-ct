"""Relay connection tweaks, and the seam with the vendored `selprotopy`.

=============================================================================
CONTRACT WITH `selprotopy` -- read this before updating the vendored library
=============================================================================

`selprotopy/` is vendored and patched (a PreToolUse hook blocks edits in
there), so ALL coupling with its private names lives in this file, inside
`FastMessageChannel`. If an upstream resync breaks something, here -- and
only here -- is where it gets fixed.

What the toolkit uses from a `SELClient`, and what each thing is:

  PRIVATE (starts with "_", may vanish in an upgrade without warning):
    client._write(bytes)              sends raw bytes to the relay
    client._read_clean_prompt()       consumes up to the prompt (setup only)
    client._read_command_response(c)  reads an ASCII command's response
    client._read_to_prompt()          the same, without filtering by command

  PUBLIC in practice, but not documented as API:
    client.conn                       the `telnetlib.Telnet` underneath
    client.fm_command_1               Fast Meter marker (e.g. b'\\xa5\\xd1')
    client.fm_config_command_1        A5C1 marker (Fast Meter config)
    client.fast_meter_definition      analog layout, from the autoconfig
    client.dnaDef                     Relay Word names, from the DNA block

  NOT `selprotopy`'s -- it was a monkey-patch of ours:
    client._buffer_clean              "is the buffer clean at the prompt?"

That last one deserves the story: the poll loops hung this attribute on the
selprotopy object and `AsciiTargetReader` read/wrote the SAME attribute --
that was how the two agreed on who had to drain the buffer before sending
the next command. A handshake between two modules through a field invented
on a third one's object. It is `FastMessageChannel` state now, and the
handshake still holds because both sides take the SAME channel via
`channel_for(client)`.

Who did NOT go through here: `pacct/cli/runner.py` still talks straight to
the privates. It is the CLI mode, which runs alone against a single relay
and shares its connection with nobody; migrating it too would be good, but
it is not what Phase 6B asked for and every line of it needs a bench to
verify.
"""

from __future__ import annotations

import select
import time
import weakref

from selprotopy.protocol import commands


def wait_readable(conn, timeout: float) -> None:
    """Sleeps until the socket has bytes, or until `timeout`. Never raises.

    Replaces the `time.sleep(0.005)` / `time.sleep(0.01)` with which the read
    loops waited for the relay's answer. The fixed sleep wakes the thread
    100-200 times a second whether there is data or not; measured on the
    Exemplo bench, 76% of a SEL-411L's socket reads came back empty, and a
    SEL-311C reached 1913 empty reads in 20 s of polling.

    It lives here, and not in `poll.py`, for two reasons: `pacct/core/` is the
    side that already talks to the relay's socket (`drain_login_banner` is in
    this file), and the other path that needed it is `_send_ascii` of
    `core/target_region.py` -- if the function lived in `web/glv/poll.py`, the
    core would have to import from the web layer to use it.

    IMPORTANT for whoever touches this: call it ONLY after a read that came
    back empty. `Telnet.read_very_eager()` first drains the already-cooked
    queue (`cookedq`), so there can be data available to the application
    without the socket being readable -- selecting before reading would make
    the thread wait for bytes that are already in hand.
    """
    if timeout <= 0:
        return
    try:
        sock = conn.get_socket()
    except Exception:
        sock = None
    if sock is None:
        # No socket (connection closed, or a test double): keeps the old
        # behaviour so this does not become a pure busy-loop.
        time.sleep(min(timeout, 0.005))
        return
    try:
        select.select([sock], [], [], timeout)
    except (OSError, ValueError):
        # fd closed under the thread -- return and let the next read decide
        # what to do, exactly as before.
        return


def drain_login_banner(tn, logger=None, timeout: float = 8.0) -> bytes:
    """Consumes the login banner until the "=" prompt shows up.

    selprotopy's `_verify_connection()` does ONE `read_until(b"\\r\\n")` per
    attempt, with at most 5 attempts (`__num_con_check__`) -- that is, it
    crosses at most 5 LINES of banner before giving up.

    A relay with a short banner passes easily:

        b'TERMINAL SERVER:\\r\\n='                   -> finds "=" on the 2nd read

    A SEL-311C, on the other hand, announces device name, date/time, station
    and model:

        b'TERMINAL SERVER\\r\\n\\x02\\r\\nQPC1-LT2-UPC2  Date: ... Time: ...\\r\\n'
        b'SE EXEMPLO\\r\\n\\r\\nSEL-311C\\r\\n\\x03\\x02\\r\\n=\\x03'

    ...and the 5 attempts run out still inside the banner, never reaching the
    "=". The connection failed with `ConnVerificationFail` even with the relay
    answering perfectly -- which tracked the BANNER LENGTH, not the model nor
    the network.

    By draining the banner BEFORE building the client, its verification starts
    with a clean buffer and passes on any relay, with any banner.

    The fix lives here (and not in `_verify_connection`) because `selprotopy`
    is vendored: a patch there would be lost in the next resync.
    """
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = tn.read_very_eager()
        except EOFError:
            break
        if chunk:
            buf += chunk
            if commands.LEVEL_0 in chunk:
                break
        else:
            # Nothing pending: nudge it with a CR to provoke the prompt.
            tn.write(commands.CR)
            time.sleep(0.3)
    if logger is not None:
        if commands.LEVEL_0 in buf:
            logger.info("  banner de login drenado (%d bytes, prompt encontrado)",
                        len(buf))
        else:
            logger.warning(
                "  prompt '=' nao apareceu em %.0fs (%d bytes lidos); "
                "seguindo assim mesmo", timeout, len(buf))
    return buf


# =============================================================================
# The seam: the only class that knows selprotopy's private names
# =============================================================================

# One channel per client. A WeakKeyDictionary and not an attribute on the
# client because the whole idea is to stop writing on other people's objects
# -- and this way the channel dies with the client, holding nothing alive.
_CHANNELS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def channel_for(client) -> FastMessageChannel:
    """THIS client's channel, created on first use.

    It has to be the same object for both sides: the poll loop and the
    `AsciiTargetReader` agree between themselves, through `is_clean`, on who
    has to drain the buffer before the next command. Two channels for one
    connection would be two guesses about the same socket.
    """
    ch = _CHANNELS.get(client)
    if ch is None:
        ch = FastMessageChannel(client)
        _CHANNELS[client] = ch
    return ch


class FastMessageChannel:
    """Speaks SEL Fast Message on top of a selprotopy `SELClient`.

    It exists so that the three `poll_loop*` and the `AsciiTargetReader` do
    not have to know `_write`, `conn`, `fm_command_1`, `dnaDef` or
    `_buffer_clean`. That whole vocabulary is listed in the module docstring.

    It has no lock: a channel belongs to ONE telnet connection, and the
    `RelayLink` already guarantees that only one polling thread speaks on it
    at a time (`ensure_bits()` stops the thread before running the discovery
    precisely for that reason).
    """

    __slots__ = ("_client", "_clean")

    def __init__(self, client):
        self._client = client
        # Starts dirty: right after login/autoconfig there is still an ASCII
        # command echo on the way, and the poll's first turn has to drain it.
        self._clean = False

    # --- what the relay exposes (from selprotopy's autoconfig) -------------

    @property
    def fm_marker(self) -> bytes:
        """The bytes that open a Fast Meter frame (e.g. b'\\xa5\\xd1').

        It serves both sides: it is what you WRITE to ask for one, and it is
        what you LOOK FOR in the stream to find the start of the response.
        """
        return self._client.fm_command_1

    @property
    def fast_meter_definition(self):
        return self._client.fast_meter_definition

    @property
    def dna_definition(self):
        return self._client.dnaDef

    # --- buffer state -----------------------------------------------------

    @property
    def is_clean(self) -> bool:
        """Is the socket parked at the prompt, with nothing left over?"""
        return self._clean

    def mark_clean(self) -> None:
        self._clean = True

    def mark_dirty(self) -> None:
        self._clean = False

    # --- writing ----------------------------------------------------------

    def send_fast_meter(self) -> None:
        """Asks for a Fast Meter frame (A5D1)."""
        self._client._write(self._client.fm_command_1 + commands.CR)

    def send_target_region(self) -> None:
        """Asks for the whole TARGET region. Only the 4xx answer: on a 311C
        this comes back `Invalid Command` (measured on a 311C-1-R509)."""
        self._client._write(b"VIEW 1:TARGET\r\n")

    def send_ascii(self, cmd: bytes) -> None:
        """Sends an ASCII command with CRLF (e.g. b'TAR 13')."""
        self._client._write(cmd + b"\r\n")

    # --- reading ----------------------------------------------------------

    def read_available(self) -> bytes:
        """Everything readable without blocking. b'' when there is nothing.

        It may raise `EOFError` when the connection is down -- on purpose: the
        loops handle that in their own `except` and mark the error in the
        LiveState, which is what shows up in the diagram's header.
        """
        return self._client.conn.read_very_eager()

    def wait_readable(self, timeout: float) -> None:
        """Sleeps until there is a byte on the socket, or until `timeout`."""
        wait_readable(self._client.conn, timeout)

    def drain(self, deadline_s: float = 0.3, settle_s: float = 0.02) -> None:
        """Eats the leftovers of an earlier ASCII response.

        It is not a busy-wait: the short `sleep` only happens when data STILL
        arrived, and the loop stops at the first empty read.
        """
        if self._clean:
            return
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if not self.read_available():
                time.sleep(settle_s)
                break
            time.sleep(0.005)
