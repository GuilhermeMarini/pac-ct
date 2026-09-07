"""Routes for the Organizador de Abas GLE.

Does not start a server: the single dispatcher in `pacct.web.mount` serves it,
mounting this handler at `/gle-tabs/`.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from sellib import rdb as rdb_loader
from sellib.rdb import short_sha as _short_sha

from pacct.web.gle_tabs import load_template, model, state
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
            if path == "/gles":
                self._serve_gles()
                return
            if path == "/pages":
                self._serve_pages()
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

        def _gle_bytes(self, info, relay: str, gle: str) -> bytes | None:
            """The GLE's extracted bytes, or None after sending a 404.

            Read from the EXTRACTION rather than the OLE: it is the same
            content, and this way reading tabs never opens the RDB.
            """
            entry = rdb_loader.find_gle(info, relay, gle)
            if entry is None or not entry.fs_path.is_file():
                self._send_json(404, {"ok": False,
                                      "error": f"GLE {gle} não está em {relay}."})
                return None
            return entry.fs_path.read_bytes()

        def _serve_gles(self):
            """Each relay of the RDB with its GLE files and their page counts."""
            q = self._query()
            key = q.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            st = self.sess()
            lock = self.require_session().lock
            relays = []
            for relay in info.relays:
                gles = []
                for g in relay.gles:
                    try:
                        pages = len(model.read_pages(g.fs_path.read_bytes()))
                    except (OSError, model.GleTabsError) as exc:
                        # One unreadable GLE must not take the whole list
                        # down: the others are still editable.
                        logger.warning("[gle-tabs] %s/%s ilegível: %s",
                                       relay.name, g.filename, exc)
                        pages = None
                    with lock:
                        dirty = (key, relay.name, g.filename) in st.edits
                    gles.append({"gle": g.filename, "pages": pages,
                                 "dirty": dirty})
                relays.append({"relay": relay.name, "model": relay.model,
                               "gles": gles})
            self._send_json(200, {"ok": True, "rdb": key, "relays": relays})

        def _serve_pages(self):
            """One GLE's tabs, plus whatever is staged for it."""
            q = self._query()
            key, relay, gle = q.get("rdb", ""), q.get("relay", ""), q.get("gle", "")
            info = self._rdb(key)
            if info is None:
                return
            raw = self._gle_bytes(info, relay, gle)
            if raw is None:
                return
            try:
                spans = model.read_pages(raw)
            except model.GleTabsError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            st = self.sess()
            with self.require_session().lock:
                edit = st.edits.get((key, relay, gle))
            self._send_json(200, {
                "ok": True, "rdb": key, "relay": relay, "gle": gle,
                "max_name": model.MAX_NAME,
                "pages": [{"index": s.index, "name": s.name,
                           "description": s.description,
                           "elements": s.elements} for s in spans],
                "order": edit.order if edit else [s.index for s in spans],
                "names": {str(i): n for i, n in (edit.names if edit else {}).items()},
                "dirty": edit is not None,
            })

        # -- POST -------------------------------------------------------

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/stage":
                self._do_stage()
                return
            if path == "/reset":
                self._do_reset()
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _do_stage(self):
            """Validate one GLE's edit and hold it. Stages nothing on refusal."""
            body = self._read_json_body()
            key = str(body.get("rdb", ""))
            relay, gle = str(body.get("relay", "")), str(body.get("gle", ""))
            info = self._rdb(key)
            if info is None:
                return
            raw = self._gle_bytes(info, relay, gle)
            if raw is None:
                return
            # Guard the shapes explicitly. _read_json_body guarantees body is a
            # dict, but says nothing about its contents.
            order_raw = body.get("order", [])
            names_raw = body.get("names", {})
            if not isinstance(order_raw, list):
                self._send_json(400, {"ok": False,
                                      "error": "ordem ou nomes malformados."})
                return
            if not isinstance(names_raw, dict):
                self._send_json(400, {"ok": False,
                                      "error": "ordem ou nomes malformados."})
                return
            try:
                order = [int(i) for i in order_raw]
                # JSON object keys are strings; the model indexes by int.
                names = {int(k): str(v) for k, v in names_raw.items()}
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False,
                                      "error": "ordem ou nomes malformados."})
                return
            st = self.sess()
            lock = self.require_session().lock
            try:
                spans = model.read_pages(raw)
                with lock:
                    edit = state.stage_edit(st, (key, relay, gle), spans,
                                            order=order, names=names)
            except model.GleTabsError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {
                "ok": True,
                "dirty": edit is not None,
                "moved": sum(1 for pos, src in enumerate(order) if pos != src),
                "renamed": len(edit.names) if edit else 0,
            })

        def _do_reset(self):
            """Drop the edits of one GLE, or of the whole RDB."""
            body = self._read_json_body()
            key = str(body.get("rdb", ""))
            relay, gle = str(body.get("relay", "")), str(body.get("gle", ""))
            # Require both relay and gle to be absent for whole-RDB clear.
            # Silently wiping every GLE's edits because one is missing is
            # surprising data loss.
            if (relay and not gle) or (not relay and gle):
                self._send_json(400, {"ok": False,
                                      "error": "forneça ambos relay e gle, "
                                               "ou nenhum dos dois."})
                return
            st = self.sess()
            lock = self.require_session().lock
            with lock:
                if relay and gle:
                    st.edits.pop((key, relay, gle), None)
                else:
                    for k in [k for k in st.edits if k[0] == key]:
                        del st.edits[k]
            self._send_json(200, {"ok": True,
                                  "dirty": state.dirty_count(st, key, lock)})

    return Handler
