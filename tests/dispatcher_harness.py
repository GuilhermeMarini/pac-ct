"""Drive the dispatcher over a real socket, which is the only way to drive it.

`tests/web_harness.py` reaches a TOOL by building its handler class and calling
`do_GET` on an instance it assembled by hand. That works because a tool handler
only ever reads attributes -- `self.path`, `self.headers`, `self.session` --
that the harness can set. The dispatcher cannot be reached that way and be
worth reaching: what it does IS the assembly. It resolves the session from the
`Cookie` header, strips the mount prefix off `self.path`, swaps
`self.__class__` for the tool's, and writes a `Set-Cookie` from
`end_headers`. A harness that set those attributes itself would be asserting
its own arrangement.

There is a second reason, and it is the one that decided the shape. The tool
harness hands the handler a plain `dict` for `self.headers`, while the real
`BaseHTTPRequestHandler` parses them into an `email.message.Message`, which is
**case-insensitive**. Every route that reads `Content-Length` or `Cookie` goes
through that difference, and the dispatcher is where those two headers actually
decide something. Testing the dispatcher through a dict would carry the
shortcut into the one place it matters.

So this binds a real `ThreadingHTTPServer` on an ephemeral port and talks to it
with `http.client`. It is slower than the tool harness and it is not a
replacement for it -- a tool's routes are still cheaper and clearer to drive
without a socket. Use this for the dispatcher, and for anything whose subject
is a header, a status line or a cookie.

**What it still is not, and the document says so too**: a browser. Nothing here
runs JavaScript, so the `fetch` shim that re-prefixes absolute paths on the
client is exercised by nothing in this suite -- only the server half of that
arrangement is covered.
"""

from __future__ import annotations

import http.client
import logging
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from pacct.web import theme as themes
from pacct.web.mount import Mount, make_dispatcher
from pacct.web.session import SessionHandler, SessionManager

LOGGER = logging.getLogger("test")


class Response:
    """One real HTTP response, headers parsed by the standard library."""

    def __init__(self, raw: http.client.HTTPResponse) -> None:
        self.status = raw.status
        # `HTTPMessage` is case-insensitive, which a dict is not -- keeping the
        # object rather than flattening it is half the point of this harness.
        self.headers = raw.headers
        self.body = raw.read()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        import json
        return json.loads(self.body or b"null")

    @property
    def cookie(self) -> str | None:
        """The `Set-Cookie` this response carried, if any."""
        return self.headers.get("Set-Cookie")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Response {self.status} {len(self.body)}B>"


class Client:
    """A caller for one running dispatcher."""

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def request(self, verb: str, path: str, body: bytes | None = None,
                cookie: str | None = None,
                headers: dict[str, str] | None = None) -> Response:
        """One request, on its own connection.

        A connection each because `BaseHTTPRequestHandler` leaves
        `protocol_version` at HTTP/1.0, so the server closes after every
        response. That is itself part of the contract and is recorded in the
        document; reusing a connection here would be testing a keep-alive the
        server does not offer.
        """
        sent = dict(headers or {})
        if cookie:
            sent["Cookie"] = cookie
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(verb, path, body=body, headers=sent)
            return Response(conn.getresponse())
        finally:
            conn.close()

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: bytes | None = None, **kw) -> Response:
        return self.request("POST", path, body=body, **kw)


class ProbeHandler(SessionHandler):
    """A tool that reports what the dispatcher handed it, and nothing else.

    Real tools are driven through `web_harness`; what the dispatcher tests need
    is a mount whose answer IS the arrangement -- the path after stripping, the
    class the request ended up as, and whether a session was resolved. A real
    tool would answer with its own screen and prove the same thing less
    directly.
    """

    session_key = "probe"
    state_factory = dict

    def _report(self):
        import json
        self._send(200, json.dumps({
            "path": self.path,
            "became": type(self).__name__,
            # A session is the thing infrastructure routes must never mint, so
            # whether one arrived is the observable the tests turn on.
            "has_session": getattr(self, "session", None) is not None,
            "theme": getattr(self, "theme", None),
        }), "application/json")

    def do_GET(self):
        self._report()

    def do_POST(self):
        self._report()


def probe_mount(prefix: str, label: str = "Probe") -> Mount:
    """A `Mount` of `ProbeHandler` at `prefix`.

    `Mount.__init__` writes `mount_prefix` onto the handler CLASS, so each
    mount needs a class of its own -- sharing one would have the last mount
    built silently decide the prefix every earlier mount reports.
    """
    handler = type(f"Probe_{prefix.strip('/') or 'root'}", (ProbeHandler,), {})
    return Mount(prefix, handler, label)


@contextmanager
def serve(mounts: list[Mount], sessions: SessionManager | None = None,
          default_theme: str = themes.DEFAULT_THEME):
    """Run a dispatcher on an ephemeral port for the body of the `with`.

    Port 0 lets the OS choose, so concurrent test runs cannot collide on a
    fixed number. The server is shut down and closed on the way out whatever
    happened inside, because a leaked `ThreadingHTTPServer` holds its port and
    the next test to bind is the one that fails.
    """
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_dispatcher(mounts, sessions, default_theme),
    )
    # `shutdown()` blocks until `serve_forever` notices, and it only looks
    # between polls -- at the 0.5 s default that is half a second of pure
    # waiting per test, which measured at 15.7 s across this file alone
    # against 1.6 s at 10 ms. It paces nothing else: the loop is a `select`
    # with this as its timeout, so a shorter one costs a few more wakeups on
    # an idle socket and buys back the whole teardown.
    thread = threading.Thread(target=srv.serve_forever, args=(0.01,),
                              name="test-http", daemon=True)
    thread.start()
    try:
        yield Client(*srv.server_address[:2])
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def sessions_for(tmp_path) -> SessionManager:
    """A `SessionManager` rooted in a test's own temporary directory."""
    return SessionManager(root=tmp_path / "sessions", logger=LOGGER)
