"""Routes for the Organizador de Abas GLE.

Does not start a server: the single dispatcher in `pacct.web.mount` serves it,
mounting this handler at `/gle-tabs/`.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from sellib.rdb import short_sha as _short_sha

from pacct.web.gle_tabs import load_template, state
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

LANDING_HTML = load_template("landing.html")


def build_gle_tabs_handler(logger: logging.Logger, sessions) -> type:
    """Return the tab organiser's handler class."""

    class Handler(SessionHandler):
        session_key = "gle-tabs"
        state_factory = state.GleTabsState
        server_sessions = sessions

        # -- helpers ----------------------------------------------------

        def _query(self) -> dict:
            return {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items()}

        def _rdb(self, key: str):
            """The RdbInfo for the key, adopting from the library if needed.

            `st.rdbs` is a cache, not a gate: an RDB in the visitor's library
            counts for this tool with no extra step. The 404 is left only for
            a key that names no file at all.
            """
            st = self.sess()
            info = st.rdbs.get(key)
            if info is not None:
                return info
            entry = self.library_entry(key, filelib.KIND_RDB)
            if entry is not None and entry.rdb is not None:
                with self.require_session().lock:
                    st.rdbs[_short_sha(entry.rdb.sha256)] = entry.rdb
                return entry.rdb
            self._send_json(404, {"ok": False,
                                  "error": "RDB não está no projeto."})
            return None

        # -- GET --------------------------------------------------------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", ""):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/rdbs":
                self._serve_rdbs()
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _serve_rdbs(self):
            """Every RDB in the visitor's project, newest first.

            Listing also registers each one in `st.rdbs`, which is what makes
            picking a file a single click rather than a select round trip.
            """
            st = self.sess()
            lock = self.require_session().lock
            lib = filelib.library_for(sessions, self.require_session())
            with lock:
                entries = [e for e in lib.list(filelib.KIND_RDB)
                           if e.rdb is not None]
                for e in entries:
                    st.rdbs[_short_sha(e.require_rdb().sha256)] = e.require_rdb()
            out = []
            for e in entries:
                key = _short_sha(e.require_rdb().sha256)
                out.append({
                    "rdb": key, "sha256": e.sha256, "name": e.display_name,
                    "size": e.size, "detail": e.detail, "origin": e.origin,
                    "dirty": state.dirty_count(st, key, lock),
                })
            # `FileLibrary.list` is in arrival order, so the last upload is last.
            out.reverse()
            self._send_json(200, {"ok": True, "rdbs": out})

        # -- POST -------------------------------------------------------

        def do_POST(self):
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

    return Handler
