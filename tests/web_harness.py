"""Drive a mounted tool's routes without a socket.

Every tool exposes `build_*_handler(logger, sessions) -> type` and declares the
handler class INSIDE that factory (REVIEW.md S2). That is not what stopped the
routes being tested: the factory returns the type, so a test calls the factory
and gets the class. What was missing was a place to build a request.

`tests/test_glv_handler_scan_mode.py` worked one out for the GLV -- `__new__`
the class, hang the session on it, drive `do_POST` -- and replaced
`_send_json` per instance with a recorder. This is that idea with the seam
moved one layer down: nothing on the handler is replaced, and the response is
captured where the socket would be. `_send`, `_send_json`, the `Content-Type`
and `Content-Length` headers, the status line and `inject_head`'s markup
resolution all run for real, so an HTML route can be asserted on as readily as
a JSON one.

What is NOT covered here, by construction: the dispatcher. Prefix stripping,
the class swap, session resolution, the `Set-Cookie` on a first response and
the infrastructure routes (`/library`, `/progress`, `/theme.css`,
`/static/...`) all live in `mount.py` and are reached over a real socket, not
through this.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from pacct.web import theme as themes
from pacct.web.project_files import library as filelib
from pacct.web.session import Session, SessionManager

LOGGER = logging.getLogger("test")


class Response:
    """One captured HTTP response: the bytes the handler meant to send."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        status_line = lines[0].decode("latin-1") if lines and lines[0] else ""
        parts = status_line.split(" ", 2)
        self.status = int(parts[1]) if len(parts) > 1 else 0
        self.headers: dict[str, str] = {}
        for line in lines[1:]:
            name, sep, value = line.decode("latin-1").partition(":")
            if sep:
                self.headers[name.strip().lower()] = value.strip()
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self):
        return json.loads(self.body or b"null")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Response {self.status} {len(self.body)}B>"


class ToolHarness:
    """A built handler class plus the session manager behind it."""

    def __init__(self, handler: type, sessions: SessionManager,
                 session: Session) -> None:
        self.handler = handler
        self.sessions = sessions
        self.session = session

    # -- requests -----------------------------------------------------------

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw) -> Response:
        return self.request("POST", path, body=body, **kw)

    def request(self, verb: str, path: str, body=None,
                headers: dict[str, str] | None = None,
                session: Session | None = None,
                theme: str = themes.DEFAULT_THEME,
                mount_prefix: str = "") -> Response:
        """Run one request against the handler and capture what it wrote.

        `body` is JSON-encoded when it is a dict or a list, sent as-is when it
        is bytes. The `Content-Length` header is derived from it, because
        every route that reads a body trusts that header -- passing the wrong
        one is a test of its own, not the default.
        """
        if isinstance(body, (dict, list)):
            payload = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = body or b""

        h = self.handler.__new__(self.handler)
        h.session = session if session is not None else self.session
        h.mount_prefix = mount_prefix
        h.theme = theme
        h.path = path
        h.command = verb
        h.request_version = "HTTP/1.1"
        h.requestline = f"{verb} {path} HTTP/1.1"
        h.close_connection = True
        h.rfile = io.BytesIO(payload)
        h.wfile = io.BytesIO()
        sent = dict(headers or {})
        sent.setdefault("Content-Length", str(len(payload)))
        h.headers = sent

        handler_method = getattr(h, "do_" + verb)
        handler_method()
        return Response(h.wfile.getvalue())

    # -- the visitor's library ---------------------------------------------

    def library(self, session: Session | None = None) -> filelib.FileLibrary:
        return filelib.library_for(self.sessions, session or self.session)

    def add_scd(self, path: Path, name: str = "projeto.scd",
                sha: str | None = None,
                session: Session | None = None) -> filelib.FileEntry:
        """Put an SCD in the project the way `/files/upload` would.

        `session` names WHOSE project, because a second visitor has a library
        of their own -- putting a file in one is not putting it in the other,
        and a test about two visitors has to say which.
        """
        sess = session or self.session
        sha = sha or ("b" * 64)
        entry = filelib.FileEntry(
            sha256=sha, kind=filelib.KIND_SCD, display_name=name,
            size=path.stat().st_size, path=path,
        )
        with sess.lock:
            stored, _ = self.library(sess).add(entry)
        return stored

    def add_rdb(self, info, name: str | None = None,
                sha: str | None = None,
                session: Session | None = None) -> filelib.FileEntry:
        """Put an already-extracted RDB in the project.

        Takes the `RdbInfo` rather than bytes: `rdb.process_upload` writes into
        the shared content cache, which is process-wide and not a tmp_path, so
        a route test builds the extraction itself (see `fake_rdb`).
        """
        sess = session or self.session
        entry = filelib.FileEntry(
            sha256=sha or info.sha256, kind=filelib.KIND_RDB,
            display_name=name or info.display_name, size=1,
            detail=f"{len(info.relays)} IED(s)", rdb=info,
        )
        with sess.lock:
            stored, _ = self.library(sess).add(entry)
        return stored


def fake_rdb(tmp_path: Path, relays: dict[str, dict[str, bytes]],
             *, name: str = "projeto.rdb",
             sha: str | None = None,
             models: dict[str, str] | None = None,
             ips: dict[str, str] | None = None):
    """An extraction on disk plus the `RdbInfo` that points at it.

    `relays` is `{relay_name: {filename: contents}}`, laid out the way
    `sellib.rdb` extracts a real RDB -- `Relays/<relay>/<file>` -- because
    that is what `dnp_map.discover()` walks, deliberately, instead of reusing
    `RdbInfo.relays` (a relay with settings but no `.gle`, like a SEL-2440
    concentrator, has no `RelayEntry` and its DNP map is exactly what someone
    wants to edit).

    Built by hand rather than through `rdb.process_upload`, which would write
    into the process-wide `cache/rdb/<sha256>/` instead of this `tmp_path`.
    """
    from sellib.rdb import GleEntry, RdbInfo, RelayEntry

    sha = sha or ("c" * 64)
    extract_dir = tmp_path / "extract" / sha[:12]
    entries: list[RelayEntry] = []
    for relay_name, files in relays.items():
        relay_dir = extract_dir / "Relays" / relay_name
        relay_dir.mkdir(parents=True, exist_ok=True)
        gles = []
        for filename, contents in files.items():
            (relay_dir / filename).write_bytes(contents)
            if filename.lower().endswith(".gle"):
                gles.append(GleEntry(
                    name=Path(filename).stem, filename=filename,
                    rel_path=f"Relays/{relay_name}/{filename}",
                    fs_path=relay_dir / filename,
                ))
        # `RelayEntry.model` is the RELAYTYPE string, and it is what
        # `family_from_relaytype` reads -- the Comparador de Ajustes refuses a
        # relay whose family it cannot infer, so a test about the comparator
        # has to supply it.
        entries.append(RelayEntry(name=relay_name, gles=gles,
                                  model=(models or {}).get(relay_name),
                                  ip=(ips or {}).get(relay_name)))

    rdb_path = tmp_path / name
    rdb_path.write_bytes(b"nao e um OLE de verdade")
    return RdbInfo(rdb_path=rdb_path, extract_dir=extract_dir, sha256=sha,
                   reused=True, relays=entries, display_name=name)


def build(factory, tmp_path: Path, *args, **kwargs) -> ToolHarness:
    """`build(build_dnp_map_handler, tmp_path)` -> a harness for that tool.

    Extra positional arguments go to the factory after `(logger, sessions)`,
    which is what the GLV needs for its `GlvDefaults`.
    """
    sessions = SessionManager(root=tmp_path / "sessions", logger=LOGGER)
    handler = factory(LOGGER, sessions, *args, **kwargs)
    session, _ = sessions.resolve(None)
    return ToolHarness(handler, sessions, session)
