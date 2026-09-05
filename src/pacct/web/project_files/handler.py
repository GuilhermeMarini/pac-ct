"""Routes for the Arquivos do Projeto tab.

Absolute routes, like every other tool: the single dispatcher in `mount.py`
strips the mount prefix before delegating, and the injected shim re-adds it to
the page's `fetch` calls.

Listing is NOT served here. `GET <prefix>/library` is served by the dispatcher
for every mount, next to `/progress` and `/theme.css`, because every tool's
picker needs it -- see `mount.py`.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from olefile.olefile import OleFileError
from selfiles import rdb as rdb_loader
from selfiles.scl import read as scd_loader

from pacct.web.project_files import library, load_template
from pacct.web.session import SessionHandler

# The `<!--NAV:files-->` marker inside `.shell` is resolved per request by
# `mount.py:_resolve_markup()`, with the visitor's theme in hand. It must NOT
# be substituted here: the three directions do not share nav markup (`.toc`,
# `.strip`/`.borne`, `.tabs`), so resolving at import time would freeze one
# direction's markup into all three.
LIBRARY_HTML = load_template("library.html")


def build_project_files_handler(logger: logging.Logger, sessions) -> type:
    """Return the tab's handler class. Opens no socket: `mount.py` serves it."""

    class Handler(SessionHandler):
        # The same key the library itself lives under. Intentional: this tab's
        # state IS the library.
        session_key = library.LIBRARY_KEY
        state_factory = library.FileLibrary
        server_sessions = sessions

        # -- GET ------------------------------------------------------------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "", "/index.html"):
                self._send(200, LIBRARY_HTML, "text/html; charset=utf-8")
                return
            if path == "/download":
                self._do_download()
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _do_download(self):
            """`GET /download?sha256=` -- hand back a file of THIS project.

            It takes a sha256 and looks it up in the caller's own library, and
            never a path: there is nothing to sandbox because there is nothing
            for the client to point anywhere. That is what lets it serve an
            RDB out of the shared content cache, which a tool's
            `/download?file=<path>` deliberately refuses to do -- that route
            takes the path from the request, so widening its sandbox to the
            cache would let one visitor ask for another's generated file.
            """
            sha = (parse_qs(urlparse(self.path).query).get("sha256")
                   or [""])[0].strip()
            lib = self.sess()
            with self.require_session().lock:
                entry = lib.get(sha)
            if entry is None:
                self._send(404, "Arquivo não está no projeto.",
                           "text/plain; charset=utf-8")
                return
            src = entry.file_path()
            src = Path(src) if src is not None else None
            if src is None or not src.is_file():
                self._send(410, "Arquivo não está mais em disco.",
                           "text/plain; charset=utf-8")
                return

            size = src.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            # RFC 5987: the name can carry accents, and QuickSet users name
            # their files in Portuguese.
            self.send_header(
                "Content-Disposition",
                "attachment; filename*=UTF-8''"
                + quote(entry.display_name, safe=""))
            self.end_headers()
            # In chunks: an RDB is 40-140 MB and `read_bytes()` would hold all
            # of it in memory per concurrent download.
            with open(src, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            logger.info("[files] download de '%s' (%s)",
                        entry.display_name, entry.short_sha)

        # -- POST -----------------------------------------------------------

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/upload":
                self._do_upload()
            elif path == "/remove":
                self._do_remove()
            else:
                self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _do_upload(self):
            job = self.job()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                job.fail("Arquivo vazio.")
                self._send_json(400, {"ok": False, "error": "Arquivo vazio."})
                return

            filename = unquote(self.headers.get("X-Filename") or "")
            kind = library.kind_for(filename)
            if kind is None:
                msg = "Tipo não reconhecido — envie .rdb, .scd ou .xml."
                job.fail(msg)
                self._send_json(400, {"ok": False, "error": msg})
                return

            cap = library.max_bytes_for(kind)
            if length > cap:
                msg = f"Arquivo grande demais (limite {cap // (1024*1024)} MB)."
                job.fail(msg)
                self._send_json(413, {"ok": False, "error": msg})
                return

            # The body is NOT read in one go. An RDB runs 40 to 140 MB and
            # the ceiling is 500; `self.rfile.read(length)` kept that whole
            # size resident, and the server is threaded -- two simultaneous
            # uploads at the ceiling would be a gigabyte of RSS on a field
            # laptop. Each path below streams in 1 MB chunks, with the sha256
            # growing along with it.
            job.stage("Recebendo arquivo", 0)
            if kind == library.KIND_RDB:
                built = self._build_rdb_entry(filename, length, job)
            else:
                built = self._build_scd_entry(filename, length, job)
            if built is None:
                return  # the builder already answered
            entry, sha = built

            # The duplicate check can only come HERE: the sha256 is of the
            # content, and the content has only just arrived. A second upload
            # of the same bytes reuses the extraction in the cache (`reused`),
            # so the cost of this order is re-reading the file, not
            # reprocessing it.
            lib = self.sess()
            with self.require_session().lock:
                existing = lib.get(sha)
            if existing is not None:
                self._discard(entry, keep=existing)
                job.finish("Arquivo já estava no projeto")
                self._send_json(200, {"ok": True, "duplicate": True,
                                      "entry": existing.to_json()})
                return

            with self.require_session().lock:
                entry, duplicate = lib.add(entry)
            logger.info("[files] %s '%s' (%s) no projeto: %s",
                        entry.kind.upper(), entry.display_name,
                        entry.short_sha, entry.detail)
            job.finish(entry.detail or "Arquivo carregado")
            self._send_json(200, {"ok": True, "duplicate": duplicate,
                                  "entry": entry.to_json()})

        def _build_rdb_entry(self, filename, length, job):
            """The RDB's bytes never reach the session directory: the
            content-addressed cache already holds them at
            `cache/rdb/<sha256>/source.rdb`, shared between visitors.

            Reads straight from the request body into that cache, a megabyte
            at a time. Returns `(entry, sha256)`."""
            try:
                info = rdb_loader.process_upload_stream(
                    self.rfile, length, filename,
                    on_progress=lambda d, t, s: job.fraction(s, d, t),
                )
            except (OleFileError, ValueError) as e:
                # NotOleFileError (corrupt / not-an-RDB) and the empty-file
                # ValueError are the user's mistake, not the server's --
                # OleFileError subclasses OSError (it is not re-exported at
                # the olefile package's top level in this pinned version,
                # hence importing it from the olefile.olefile submodule), so
                # it MUST be caught here, before the bare OSError clause
                # below, or it gets misclassified as a 500.
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": f"RDB inválido: {e}"})
                return None
            except OSError as e:
                job.fail(str(e))
                self._send_json(500, {"ok": False,
                                      "error": f"falha ao salvar/extrair: {e}"})
                return None
            except Exception as e:      # anything else olefile might raise
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": f"RDB inválido: {e}"})
                return None
            return library.FileEntry(
                sha256=info.sha256, kind=library.KIND_RDB,
                display_name=info.display_name, size=length,
                detail=f"{len(info.relays)} IED(s)", rdb=info,
            ), info.sha256

        def _build_scd_entry(self, filename, length, job):
            """An SCD is up to 200 MB and is the one upload whose bytes DO stay
            in the session (`files/<sha12>.scd`).

            The final name is the hash, which is not known until the last byte,
            so it lands in a temp file in the same directory and is renamed
            once. Same directory means `os.replace` is atomic: no tool ever
            sees a half-written `.scd`. Returns `(entry, sha256)`."""
            files_dir = library.files_dir(self.require_session())
            files_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(files_dir),
                                            suffix=".scd-part")
            os.close(fd)
            tmp = Path(tmp_name)
            # `os.replace` below moves this file into the library; until it
            # does, the `finally` owns it. A flag rather than clearing `tmp`,
            # so the path stays a Path all the way down.
            moved = False
            target = None
            try:
                try:
                    sha = rdb_loader.stream_to_file(
                        self.rfile, length, tmp,
                        on_progress=lambda d, t, s: job.fraction(s, d, t))
                except (OSError, ValueError) as e:
                    job.fail(str(e))
                    self._send_json(500, {"ok": False,
                                          "error": f"falha ao salvar SCD: {e}"})
                    return None

                job.stage("Lendo SCD", 60)
                try:
                    ieds = scd_loader.load_scd(tmp)
                except Exception as e:
                    job.fail(str(e))
                    self._send_json(400, {"ok": False,
                                          "error": f"SCD inválido: {e}"})
                    return None
                if not ieds:
                    # A file that does not validate never enters the library --
                    # no half-entries for a tool to trip over later.
                    job.fail("nenhum IED encontrado no SCD")
                    self._send_json(400, {"ok": False,
                                          "error": "nenhum IED encontrado no SCD"})
                    return None

                target = library.scd_path_for(files_dir, sha)
                os.replace(tmp, target)
                moved = True
            finally:
                if not moved:
                    tmp.unlink(missing_ok=True)

            return library.FileEntry(
                sha256=sha, kind=library.KIND_SCD,
                display_name=library.display_name_for(filename, "arquivo.scd"),
                size=length, detail=f"{len(ieds)} IED(s)", path=target,
            ), sha

        def _discard(self, entry, keep=None):
            """Throw away what an upload just built, because those bytes were
            already in this project.

            Only the SCD has anything to throw away: it is a file in this
            session's `files/`. An RDB lives in the shared content cache, which
            has no owner and is swept on age and size -- deleting it here would
            pull the extraction out from under whoever else is using it.

            `keep` is the entry the library already holds for these bytes, and
            when it has a file that file is THE SAME FILE: `files/<sha12>.scd`
            is named after the content, so re-uploading an SCD the project
            already has just rewrote, byte for byte, the path the existing
            entry points at. Deleting it left the entry listed with nothing
            behind it -- the picker still offered the SCD, `/select-scd`
            answered "Arquivo não está mais no projeto", and the log said
            `SCD nao encontrado` for a file the screen was showing. Uploading
            the same SCD twice is the ordinary way to hit it: re-dropping a
            pair of files to add the RDB beside it does exactly that.
            """
            if entry.kind != library.KIND_SCD or entry.path is None:
                return
            if keep is not None and keep.path is not None \
                    and Path(keep.path) == Path(entry.path):
                return
            Path(entry.path).unlink(missing_ok=True)

        def _do_remove(self):
            sha = (self._read_json_body().get("sha256") or "").strip()
            lib = self.sess()
            with self.require_session().lock:
                removed = lib.remove(sha)
            if removed is None:
                self._send_json(404, {"ok": False,
                                      "error": "Arquivo não está no projeto."})
                return
            logger.info("[files] %s '%s' removido do projeto",
                        removed.kind.upper(), removed.display_name)
            self._send_json(200, {"ok": True, "removed": removed.to_json()})

    return Handler
