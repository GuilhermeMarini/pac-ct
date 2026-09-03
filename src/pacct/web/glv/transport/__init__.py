"""The transport seam: what a RelayLink needs from a way of talking to a relay.

`RelayLink` owns identity, the refcount, the `LiveState`, the watchdog and the
poll thread. It owns no protocol. Everything protocol-shaped lives behind this
Protocol, so a bug in one transport cannot reach the other.

`abort()` is a transport method and deliberately not a generic timeout. Telnet
breaks a hung login by CLOSING THE SOCKET, because selprotopy swallows the
exception and retries -- nothing else wakes it. py61850 has a real socket
timeout and needs none of that. Collapsing the two into "rely on the
transport's timeout" would delete the property that makes telnet recover, and
would pass a happy-path test while doing it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

MODE_TARGET = "target_region"
MODE_FAST_METER = "fast_meter_digitals"
MODE_TAR = "tar_digitals"
MODE_MMS = "mms"

SCAN_TELNET = "telnet"
SCAN_MMS = "mms"

# The port each scan mode talks to. MMS is 102, telnet is the relay's ASCII
# port; they differ, which is why LinkPool (keyed on ip:port) can hold both
# views of one relay without them colliding.
DEFAULT_PORTS = {SCAN_TELNET: 23, SCAN_MMS: 102}


def drawing_variables(bits):
    """As of `bits` that name something the relay could have: uppercased, and
    without the drawing's own constants.

    A GLE ties inputs to `0` and to `12`; they are not variables and there is
    nothing to add to a server model for them, so no transport should ever
    report one as unreachable.
    """
    return {b.upper() for b in bits if not b.isdigit()}


@runtime_checkable
class Transport(Protocol):
    mode: str
    fid: str
    devid: str

    def connect(self, job=None) -> None:
        """Open the connection and identify the relay. Raises on failure."""

    def abort(self) -> None:
        """Break a connect() that is hung, from another thread."""

    def close(self) -> None:
        ...

    def prepare_bits(self, names, job=None, pause=None) -> int:
        """Make `names` readable. Returns how many were newly resolved.

        `pause` is a context manager from the shell that stops the poll thread
        for as long as it is held. Enter it only around the calls that actually
        talk to the relay: a transport with nothing to discover (7xx digitals
        come inside the Fast Meter banks) must leave the reader running.
        """

    def poll(self, state, interval, stop, once) -> None:
        """Read into `state` until `stop` is set."""

    def unreachable(self, bits):
        """Which of THESE names this transport cannot read, and why.

        `{"names": [...], "reason": "mms" | "relay_word" | "dna"}`, or None
        while there is no way to know -- disconnected, no map yet, no DNA. The
        distinction matters more than it looks: an empty list says "the relay
        serves all of it", which is the most expensive of the three lies. The
        diagram paints both cases indeterminate, and only the transport knows
        whether a bit is late or absent.
        """

    def coverage_for(self, bits):
        """`{mapped, total, source}` for THESE bits, or None for a transport
        that reads everything.

        `bits` is the open page's, never the link's accumulated union: the
        union grows with every diagram that joins and would flatter a page
        that is badly covered. `source` names where the map came from ("scd",
        "tabela", "scd+tabela"), which is what lets the status strip say
        whether the as-built SCD was used or the factory table.
        """


def pick_transport(scan_mode: str, *, ip: str, port: int, acc_password: str,
                   relay_model, logger, **kwargs) -> Transport:
    from pacct.web.glv.transport.telnet import TelnetTransport
    if scan_mode == SCAN_MMS:
        from pacct.web.glv.transport.mms import MmsTransport
        return MmsTransport(ip, port, relay_model=relay_model, logger=logger,
                            **kwargs)
    return TelnetTransport(ip, port, acc_password=acc_password,
                           relay_model=relay_model, logger=logger)
