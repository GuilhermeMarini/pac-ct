"""Routes for the Comparador de Ajustes.

Absolute routes, like every other tool: `mount.py`'s single dispatcher strips
the `/settings-compare` prefix before delegating, and the shim rewrites the
client's `fetch` calls.

Five routes and no export: the tool answers questions about RDBs that are
already in the project library and writes no file of its own. The comparison
lives in `model.py` and the per-visitor state in `state.py`; what is left here
is reading the chosen files out of the library, reporting progress, and
turning a dict into JSON.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from sellib.rdb import short_sha as _short_sha

from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler
from pacct.web.settings_compare import load_template, state

INDEX_HTML = load_template("index.html")

# The numbered navigation is the same on the nine screens -- lives in theme.py.


def build_settings_compare_handler(logger: logging.Logger, sessions) -> type:
    """Return the Settings Compare handler class.

    Opens no server: serving is done by the single dispatcher of
    `pacct.web.mount`, which mounts this handler at `/settings-compare/`.
    State and uploads are per session (`self.sess()` / `self.sdir()`), not
    per process -- cleanup included: the session's whole directory goes away
    when it expires.
    """

    class Handler(SessionHandler):
        session_key = "settings-compare"
        state_factory = state.SettingsCompareState
        server_sessions = sessions

        def _read_json(self) -> dict | None:
            """The request's JSON, under `MAX_JSON_BODY`.

            `None` means malformed; `{}` means empty. The ceiling comes from
            `SessionHandler.read_body` -- this used to allocate whatever the
            client's `Content-Length` claimed.
            """
            raw = self.read_body()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None

        def _ensure_rdbs(self, keys) -> None:
            """Ensure every short key is in `st.rdbs`.

            A safety net on the same principle as `/state`: the RDB is in
            the visitor's library, so no route has to demand that the page
            "adopted" it first. Without this, a tab left open while the
            session lost its state would answer "rele nao encontrado" for a
            file that is right there.
            """
            st = self.sess()
            for key in keys:
                with self.require_session().lock:
                    if key in st.rdbs:
                        continue
                entry = self.library_entry(key, filelib.KIND_RDB)
                if entry is not None and entry.rdb is not None:
                    with self.require_session().lock:
                        st.rdbs[_short_sha(entry.rdb.sha256)] = entry.rdb

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                return
            if path == "/settings-state":
                # Sentinel the home uses to detect that this tool is up.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                # The PROJECT's library, not "the RDBs this tool has
                # adopted". There used to be two lists on screen -- the
                # project's files in a selector and the adopted ones in a
                # list beside it -- and a "Usar" button that only copied a
                # pointer from one to the other. It was the leftover of when
                # the comparator had an upload of its own. Listing also
                # registers into `st.rdbs`, which is what makes choosing a
                # file a single click: `/groups` and `/diff` still find the
                # RDB by its short key with no step at all.
                st = self.sess()
                lib = filelib.library_for(sessions, self.require_session())
                with self.require_session().lock:
                    entries = [e for e in lib.list(filelib.KIND_RDB)
                               if e.rdb is not None]
                    for e in entries:
                        st.rdbs[_short_sha(e.require_rdb().sha256)] = e.require_rdb()
                    rdbs = [state.rdb_summary(_short_sha(e.require_rdb().sha256), e.require_rdb())
                            for e in entries]
                rdbs.reverse()
                self._send_json(200, {"rdbs": rdbs})
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/groups":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                pairs = [(r["rdb_key"], r["relay_name"]) for r in refs]
                self._ensure_rdbs({k for k, _ in pairs})
                fam, groups = state.list_groups_for_relays(self.sess(), self.require_session().lock, pairs)
                if fam is None:
                    self._send_json(400, {"error": "familia inconsistente ou rele invalido"})
                    return
                self._send_json(200, {"family": fam, "groups": groups})
                return

            if path == "/diff":
                job = self.job()
                job.stage("Lendo ajustes dos reles", 10)
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                group_keys = body.get("groups") or []
                self._ensure_rdbs({r.get("rdb_key") for r in refs
                                   if r.get("rdb_key")})
                try:
                    payload = state.compute_diff_payload(
                        self.sess(), self.require_session().lock, refs, group_keys,
                        on_progress=lambda d, t, st: job.fraction(st, d, t),
                    )
                except Exception as e:
                    logger.exception("[settings-compare] erro computando diff")
                    job.fail(str(e))
                    self._send_json(500, {"error": f"erro interno: {e}"})
                    return
                if "error" in payload:
                    job.fail(payload["error"])
                    self._send_json(400, payload)
                    return
                job.finish("Diff pronto")
                self._send_json(200, payload)
                return

            self._send(404, "not found", "text/plain")

    return Handler
