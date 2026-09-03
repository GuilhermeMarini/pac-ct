"""Routes for the DNP map editor.

Absolute routes, like every other tool: `mount.py`'s single dispatcher strips
the prefix before delegating, and the shim rewrites the client's `fetch`
calls. The two exceptions the shim can't reach -- `download_url` and the link
to the other page -- carry `self.mount_prefix` by hand and `./` respectively.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import selfiles
from selfiles import dnp_map as set_dnp
from selfiles import dnp_profile
from selfiles.models import wordbits
from selfiles.rdb import short_sha as _short_sha

from pacct.paths import is_within
from pacct.web.dnp_map import export as exporter
from pacct.web.dnp_map import load_template, model
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

# A device profile bundle is a few hundred KB of XML plus the XSLT/XSD that
# render it; 20 MB is far above any real one and far below anything that
# would hurt to buffer.
_PROFILE_MAX_BYTES = 20 * 1024 * 1024

# Explains a rebuilt RDB's much smaller size: QuickSet never compacts its OLE
# container, so a real file carries thousands of orphaned directory entries
# that a rebuild drops. Without this a user reads a 4x smaller file as data
# loss. Applies only when ExportResult.method == "rebuild"; an in-place write
# needs no such note.
_REBUILD_NOTE = (
    "O RDB reconstruído costuma sair bem menor que o original: o QuickSet "
    "nunca compacta o arquivo, então ele acumula milhares de entradas de "
    "diretório que já não são referenciadas por nada. A reconstrução mantém "
    "tudo o que ainda é alcançável e descarta esse peso morto — não é perda "
    "de dados."
)

# The `<!--NAV:dnp-map-->` marker inside `.shell` is resolved per request by
# `mount.py:_resolve_markup()`, with the visitor's theme in hand -- exactly as
# the other five tools do it. It must NOT be substituted here: the three
# directions do not share nav markup (`.toc`, `.strip`/`.borne`, `.tabs`), so
# resolving at import time freezes one direction's markup into all three, and
# the active-screen highlight is lost with it.
LANDING_HTML = load_template("landing.html")
EDITOR_HTML = load_template("editor.html")
COPY_HTML = load_template("copy.html")


def build_dnp_map_handler(logger: logging.Logger, sessions) -> type:
    """Return the DNP map editor's handler class.

    Does not start a server: the single dispatcher in `pacct.web.mount`
    serves it, mounting this handler at `/dnp-map/`.
    """

    class Handler(SessionHandler):
        session_key = "dnp-map"
        state_factory = model.DnpMapState
        server_sessions = sessions

        # -- helpers ----------------------------------------------------

        def _query(self) -> dict:
            return {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items()}

        def _rdb(self, key: str):
            """O RdbInfo da chave, adotando do acervo se preciso.

            `st.rdbs` e' cache, nao portaria: um RDB que esta no acervo do
            visitante vale para esta ferramenta sem etapa nenhuma (ver
            `SessionHandler.library_entry`). O 404 sobrou so' para uma chave
            que nao nomeia arquivo nenhum -- um link velho, de um arquivo que
            saiu do projeto.
            """
            st = self.sess()
            info = st.rdbs.get(key)
            if info is not None:
                return info
            entry = self.library_entry(key, filelib.KIND_RDB)
            if entry is not None and entry.rdb is not None:
                with self.session.lock:
                    st.rdbs[_short_sha(entry.rdb.sha256)] = entry.rdb
                return entry.rdb
            self._send_json(404, {"ok": False,
                                  "error": "RDB não está no projeto."})
            return None

        def _discover(self, key: str, info):
            """Cached `set_dnp.discover()`.

            `discover()` walks and parses every SET_D file of every relay in
            the whole RDB; without a cache, `/map` and `/edit` would redo
            that on every keystroke just to resolve one relay's sessions.
            Caching it for the session's lifetime is sound because `key`
            names a sha256-addressed extraction directory
            (`cache/rdb/<sha256>/`, see `selfiles.rdb`): the files
            under it cannot change while this key is in use -- any change in
            content would produce a different sha256 and therefore a
            different key. See `DnpMapState.relay_cache` for the same note.
            """
            st = self.sess()
            with self.session.lock:
                cached = st.relay_cache.get(key)
                if cached is None:
                    cached = set_dnp.discover(info.extract_dir)
                    st.relay_cache[key] = cached
                return cached

        def _relay_payload(self, key: str, info) -> list[dict]:
            out = []
            for r in self._discover(key, info):
                out.append({
                    "name": r.name,
                    "relaytype": r.relaytype,
                    "sessions": [s.name for s in r.sessions],
                    "groups": set_dnp.identical_groups(r),
                })
            return out

        def _parsed_sessions(self, key: str, info, relay_name: str):
            for r in self._discover(key, info):
                if r.name == relay_name:
                    return r, {s.name: set_dnp.parse(s.fs_path.read_bytes())
                               for s in r.sessions}
            return None, {}

        # -- GET --------------------------------------------------------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", ""):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/editor":
                self._send(200, EDITOR_HTML, "text/html; charset=utf-8")
                return
            if path == "/copiar":
                self._send(200, COPY_HTML, "text/html; charset=utf-8")
                return
            if path == "/rdbs":
                self._serve_rdbs()
                return
            if path == "/relays":
                self._serve_relays()
                return
            if path == "/wordbits":
                self._send_json(200, {"ok": True,
                                      "models": wordbits.loaded_models()})
                return
            if path == "/map":
                self._serve_map()
                return
            if path == "/download":
                self._serve_download()
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _serve_rdbs(self):
            """Every RDB in the visitor's project, newest first.

            The project's list, not "the RDBs this tool has adopted": there is
            one list of files, it lives in `/files/`, and a tool page shows
            it as it is. What this route adds is what only this tool knows --
            how many pending edits each RDB is carrying.

            Listing also registers each one in `st.rdbs`, which is what makes
            picking a file a single click: no `/select-rdb` round trip stands
            between choosing a row and seeing its IEDs.
            """
            st = self.sess()
            lib = filelib.library_for(sessions, self.session)
            with self.session.lock:
                entries = [e for e in lib.list(filelib.KIND_RDB)
                           if e.rdb is not None]
                for e in entries:
                    st.rdbs[_short_sha(e.rdb.sha256)] = e.rdb
            out = []
            for e in entries:
                key = _short_sha(e.rdb.sha256)
                out.append({
                    "rdb": key,
                    "sha256": e.sha256,
                    "name": e.display_name,
                    "size": e.size,
                    "detail": e.detail,
                    "origin": e.origin,
                    "dirty": model.dirty_summary(st, key, self.session.lock),
                })
            # `FileLibrary.list` is in arrival order, so the last upload is last.
            out.reverse()
            self._send_json(200, {"ok": True, "rdbs": out})

        def _serve_relays(self):
            q = self._query()
            key = q.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            self._send_json(200, {
                "ok": True,
                "relays": self._relay_payload(key, info),
                "dirty": model.dirty_summary(self.sess(), key, self.session.lock),
            })

        def _serve_map(self):
            q = self._query()
            key = q.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = q.get("relay", "")
            session_name = q.get("d", "")
            relay, parsed = self._parsed_sessions(key, info, relay_name)
            if relay is None or session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Relé ou sessão inexistente."})
                return

            st = self.sess()
            f = parsed[session_name]
            pending = model.edits_for(st, key, relay_name, session_name)
            wbs = wordbits.lookup(relay.relaytype)
            always = wordbits.always_valid_for(wbs)

            blocks = {}
            for kind, points in f.blocks().items():
                values = [pending.get(p.key, p.value) for p in points]
                dups = wordbits.duplicates(values, always)
                # Which blocks carry a trustworthy name domain is per model
                # and lives in the model's own wordbits file (`check_kinds`),
                # because it depends on which sources that file actually has:
                # BI needs the Relay Word, BO/AO/CO come from the DNP device
                # profile, and AI is nobody's complete domain. See
                # `pacct/core/wordbits.py` for the corpus measurements.
                validates = wbs is not None and wbs.validates(kind)
                rows = []
                for p, value in zip(points, values, strict=True):
                    warning = ""
                    if validates and wbs.check(value, kind) == "unknown":
                        warning = "unknown"
                    elif value.strip().upper() in dups:
                        warning = "duplicate"
                    rows.append({
                        "key": p.key, "index": p.index, "value": value,
                        "sca_key": p.sca_key,
                        "sca": pending.get(p.sca_key or "", p.sca),
                        "dbd_key": p.dbd_key,
                        "dbd": pending.get(p.dbd_key or "", p.dbd),
                        "warning": warning,
                    })
                blocks[kind] = rows

            self._send_json(200, {
                "ok": True,
                "relay": relay_name,
                "relaytype": relay.relaytype,
                "session": session_name,
                "sessions": [s.name for s in relay.sessions],
                "groups": set_dnp.identical_groups(relay),
                "blocks": blocks,
                "extras": [{"key": k, "value": v} for k, v in f.extras()],
                "wordbits_ok": wbs is not None,
                "wordbits_model": wbs.model if wbs is not None else "",
                "check_kinds": sorted(wbs.check_kinds) if wbs else [],
                "dirty": model.dirty_summary(st, key, self.session.lock),
            })

        def _serve_download(self):
            q = self._query()
            # `_query()` already ran the request through `parse_qs`, which
            # unquotes -- a second `unquote()` here double-decodes.
            target = Path(q.get("f", ""))
            if not is_within(target, [self.sdir("out")]) or not target.is_file():
                self._send(403, "Proibido", "text/plain; charset=utf-8")
                return
            data = target.read_bytes()
            # Defence in depth: the name on disk should already be sanitized
            # (`export.py` runs it through `rdb.sanitize_name`), but nothing
            # here should ever put CR/LF into a response header.
            safe_name = target.name.replace("\r", "").replace("\n", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{safe_name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # -- POST -------------------------------------------------------

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/edit":
                self._do_edit()
            elif path == "/swap":
                self._do_swap()
            elif path == "/copy-session":
                self._do_copy_session()
            elif path == "/copy-to-relays":
                self._do_copy_to_relays()
            elif path == "/import-profile":
                self._do_import_profile()
            elif path == "/export":
                self._do_export()
            else:
                self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _do_import_profile(self):
            """Install a SEL DNP3 device profile as this model's name domain.

            This is the route for a relay the toolkit has never seen: the user
            downloads the model's device profile from SEL and drops it here,
            and the warnings turn on for that model from the next request.

            The file lands in `data/wordbits/`, not in the session, on purpose
            -- a device profile describes a *relay model*, not this visitor's
            RDB, so it is worth exactly as much to the next engineer. Merging
            follows `tools/wordbits_from_dnp_profile.py`: whatever the file
            already held (a Relay Word harvest, hand-tuned patterns) survives,
            and only the profile-derived half is rewritten.
            """
            job = self.job()
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > _PROFILE_MAX_BYTES:
                job.fail("Arquivo vazio ou grande demais.")
                self._send_json(400, {"ok": False,
                                      "error": "Arquivo vazio ou grande demais."})
                return
            data = self.rfile.read(length)
            filename = unquote(self.headers.get("X-Filename") or "perfil.zip")
            try:
                prof = dnp_profile.parse(data, filename)
            except dnp_profile.DnpProfileError as e:
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            except Exception as e:
                logger.exception("[dnp-map] falha ao ler perfil DNP")
                job.fail(str(e))
                self._send_json(400, {
                    "ok": False,
                    "error": f"Não foi possível ler o perfil: {e}"})
                return

            # The model name reaches a filename, so it is rebuilt from the
            # regex's own capture rather than trusted: `_MODEL_RE` already
            # admits only [0-9A-Z-], but the file write is the one place where
            # being wrong about that would matter.
            base = "".join(c for c in wordbits.base_model(prof)
                           if c.isalnum() or c == "-")
            if not base:
                self._send_json(400, {
                    "ok": False,
                    "error": "O perfil não identifica um modelo de relé."})
                return
            # The library owns the registry; the host owns the overlay it
            # writes into, and `writable_data_dir` is where the two meet.
            dest = selfiles.writable_data_dir("wordbits") / f"SEL-{base}.json"
            existing = {}
            if dest.is_file():
                try:
                    existing = json.loads(dest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = {}
            entry = wordbits.entry_from_profiles([prof], existing,
                                                 merge_kinds=True)
            try:
                dest.write_text(
                    json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            except OSError as e:
                logger.exception("[dnp-map] falha ao gravar %s", dest)
                job.fail(str(e))
                self._send_json(500, {
                    "ok": False,
                    "error": f"Não foi possível gravar {dest.name}: {e}"})
                return
            # The registry is loaded once and cached; without this the model
            # stays invisible until a restart and the page keeps saying the
            # validation is off for a profile the user just supplied.
            wordbits.invalidate()
            logger.info("[dnp-map] perfil DNP importado: %s -> %s",
                        filename, dest.name)
            job.finish("Perfil importado")
            self._send_json(200, {
                "ok": True,
                "model": entry["model"],
                "aliases": entry["model_aliases"],
                "device_name": prof.device_name,
                "file": dest.name,
                "check_kinds": entry["check_kinds"],
                "counts": {k: len(v) for k, v in entry["kinds"].items()},
                "bits": len(entry["bits"]),
            })

        def _validated_changes(self, body: dict, allowed: set[str]):
            """``(changes, None)`` or ``(None, error_message)``.

            Two independent rejections, both against values a browser
            `<input>` cannot produce but the JSON API can send verbatim:
            a key outside the file's own point/SCA/DBD set (MINDIST,
            MAXDIST, an unknown key -- this tool has no business changing
            session configuration), and a value that would corrupt the
            SET_D's byte contract (`set_dnp.check_value`: not Latin-1, or
            carrying a control character that would inject an extra
            physical line).
            """
            changes = {str(k): str(v)
                      for k, v in (body.get("changes") or {}).items()}
            for k, v in changes.items():
                if k not in allowed:
                    return None, f'Campo "{k}" não é um ponto editável.'
                problem = set_dnp.check_value(v)
                if problem:
                    return None, f'Valor do campo "{k}" {problem}.'
            return changes, None

        def _do_edit(self):
            body = self._read_json_body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            session_name = body.get("session", "")
            _relay, parsed = self._parsed_sessions(key, info, relay_name)
            if session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            changes, error = self._validated_changes(
                body, parsed[session_name].point_keys())
            if error:
                self._send_json(400, {"ok": False, "error": error})
                return
            stored = model.record_edits(
                self.sess(), self.session.lock, key, relay_name, session_name,
                changes, parsed[session_name])
            self._send_json(200, {
                "ok": True, "stored": stored,
                "dirty": model.dirty_summary(self.sess(), key, self.session.lock),
            })

        def _do_swap(self):
            body = self._read_json_body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            session_name = body.get("session", "")
            _relay, parsed = self._parsed_sessions(key, info, relay_name)
            if session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            a, b = str(body.get("a", "")), str(body.get("b", ""))
            allowed = parsed[session_name].point_keys()
            if a not in allowed or b not in allowed:
                self._send_json(400, {"ok": False,
                                      "error": "Somente pontos do mapa podem "
                                               "ser trocados."})
                return
            model.swap(self.sess(), self.session.lock, key, relay_name,
                       session_name, a, b, parsed[session_name])
            self._send_json(200, {
                "ok": True,
                "dirty": model.dirty_summary(self.sess(), key, self.session.lock),
            })

        def _do_copy_session(self):
            body = self._read_json_body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            src = body.get("session", "")
            _relay, parsed = self._parsed_sessions(key, info, relay_name)
            if src not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            targets = [s for s in parsed if s != src]
            touched = model.copy_session(self.sess(), self.session.lock, key,
                                         relay_name, src, targets, parsed)
            self._send_json(200, {"ok": True, "touched": touched,
                                  "sessions": targets})

        def _do_copy_to_relays(self):
            """Write one session's map onto other relays of the same model.

            A substation bank is several identical relays, and until this
            route the only way to give them one map was to retype it relay by
            relay. The destination RDB may be a DIFFERENT one from the source
            (`dst_rdb`, defaulting to the source): the map is often right in
            the file from last week and needed in the one from today. The copy
            lands as pending edits on the DESTINATION rdb, so it shows up in
            that RDB's "alterações pendentes" and leaves in that RDB's export.

            Every refusal here names what it refused and why, because the
            failure this route has to make impossible is a map half-written
            onto a relay that is not the one the user thought: a target that
            is not in that RDB, a session that relay does not have, and above
            all a different model -- an option suffix is what changes the I/O
            board, and with it which BI/BO points the file even has.
            """
            body = self._read_json_body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            dst_key = body.get("dst_rdb") or key
            dst_info = info if dst_key == key else self._rdb(dst_key)
            if dst_info is None:
                return
            src_relay = body.get("relay", "")
            src_session = body.get("session", "")
            source, parsed_src = self._parsed_sessions(key, info, src_relay)
            if source is None or src_session not in parsed_src:
                self._send_json(404, {"ok": False,
                                      "error": "Relé ou sessão inexistente."})
                return
            if not (source.relaytype or "").strip():
                self._send_json(400, {
                    "ok": False,
                    "error": f'O relé "{src_relay}" não declara modelo '
                             f'(RELAYTYPE): sem isso não há como garantir '
                             f'que o destino é do mesmo modelo.'})
                return

            by_name = {r.name: r for r in self._discover(dst_key, dst_info)}
            parsed_by_relay: dict[str, dict] = {}
            pairs: list[tuple[str, str]] = []
            for item in (body.get("targets") or []):
                relay_name = str((item or {}).get("relay", ""))
                session_name = str((item or {}).get("session", ""))
                target = by_name.get(relay_name)
                if target is None:
                    self._send_json(404, {
                        "ok": False,
                        "error": f'O relé "{relay_name}" não está em '
                                 f'{dst_info.display_name}.'})
                    return
                if not set_dnp.same_model(target.relaytype, source.relaytype):
                    self._send_json(400, {
                        "ok": False,
                        "error": f'"{relay_name}" é '
                                 f'{target.relaytype or "de modelo desconhecido"}'
                                 f' e a origem é {source.relaytype}: a cópia '
                                 f'só vale entre relés do mesmo modelo.'})
                    return
                if session_name not in {s.name for s in target.sessions}:
                    self._send_json(404, {
                        "ok": False,
                        "error": f'O relé "{relay_name}" não tem a sessão '
                                 f'"{session_name}".'})
                    return
                if relay_name not in parsed_by_relay:
                    _r, parsed_by_relay[relay_name] = self._parsed_sessions(
                        dst_key, dst_info, relay_name)
                pairs.append((relay_name, session_name))

            if not pairs:
                self._send_json(400, {"ok": False,
                                      "error": "Nenhum destino selecionado."})
                return

            # `points` ausente = o mapa inteiro (e' o que `copy_session` faz).
            # Presente, sao as chaves BASE que o passo 2 marcou; uma chave que
            # nao e' ponto desta sessao e' recusada em vez de ignorada -- vinda
            # da API, ela significa que o cliente esta falando de outro mapa,
            # e copiar "o que sobrou" seria pior que nao copiar.
            point_keys = None
            if body.get("points") is not None:
                base = {p.key for p in parsed_src[src_session].points()}
                point_keys = {str(k) for k in (body.get("points") or [])}
                estranhos = sorted(point_keys - base)
                if estranhos:
                    self._send_json(400, {
                        "ok": False,
                        "error": f'"{estranhos[0]}" não é um ponto de '
                                 f'{src_relay}/{src_session}.'})
                    return
                if not point_keys:
                    self._send_json(400, {
                        "ok": False,
                        "error": "Nenhum ponto selecionado para copiar."})
                    return

            outcomes = model.copy_map_to(
                self.sess(), self.session.lock, key, src_relay, src_session,
                parsed_src[src_session], dst_key, pairs, parsed_by_relay,
                point_keys)
            logger.info(
                "[dnp-map] %s/%s (%s ponto(s)) copiado para %d destino(s) em %s",
                src_relay, src_session,
                "todos" if point_keys is None else len(point_keys),
                len(outcomes), dst_info.display_name)
            self._send_json(200, {
                "ok": True,
                "dst_rdb": dst_key,
                "points": (len(point_keys) if point_keys is not None
                           else len(parsed_src[src_session].points())),
                "touched": sum(o.touched for o in outcomes),
                "targets": [{"relay": o.relay, "session": o.session,
                             "touched": o.touched, "missing": o.missing,
                             "extra": o.extra} for o in outcomes],
                "dirty": model.dirty_summary(self.sess(), dst_key,
                                             self.session.lock),
            })

        def _do_export(self):
            job = self.job()
            body = self._read_json_body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                # `_rdb()` already answered the 404; the job must not be
                # left running on the client's progress bar.
                job.fail("RDB não está nesta sessão.")
                return
            st = self.sess()
            # A snapshot, not the live dict: exporting copies a 40-140 MB
            # file, and a concurrent `/edit` from a second tab mutating
            # `st.edits` mid-iteration is a "dictionary changed size during
            # iteration" crash at best and a RDB/TXT pair that disagree at
            # worst. Locking and deep-copying here is the whole fix; nothing
            # downstream touches session state again.
            with self.session.lock:
                edits = copy.deepcopy(st.edits.get(key, {}))
            out_dir = self.sdir("out")
            stem = Path(info.display_name).stem
            out_path = out_dir / f"{stem}_dnp_updated.rdb"

            try:
                result = exporter.export(info.rdb_path, info.extract_dir,
                                         edits, out_path, job=job)
                if not result.ok:
                    job.fail(result.error)
                    self._send_json(400, {"ok": False, "error": result.error})
                    return

                txt_dir = out_dir / "txt"
                txts = exporter.export_txt(info.extract_dir, edits, txt_dir)
            except Exception as e:
                # The seam finding #1 closes: `export()`'s own try/except
                # covers `build_streams`/`OleFileIO`, but this route had none
                # at all -- an uncaught exception here previously left the
                # request unanswered and the progress bar spinning forever.
                logger.exception("[dnp-map] falha inesperada ao exportar")
                job.fail(str(e))
                self._send_json(500, {"ok": False,
                                      "error": f"Falha inesperada ao exportar: {e}"})
                return

            # A saida entra no acervo do projeto: um RDB com o mapa DNP
            # corrigido e' exatamente o RDB que as outras ferramentas querem
            # abrir em seguida. Antes o unico caminho entre duas abas do mesmo
            # servidor era baixar e subir de novo os mesmos 140 MB.
            project = self.publish_output(out_path, "DNP Map Editor",
                                          job=job, logger=logger)
            job.finish("Exportado")
            self._send_json(200, {
                "ok": True,
                "method": result.method,
                "project_file": project,
                "streams": result.streams,
                # A rebuild lands at a fraction of the original size on
                # purpose (see _REBUILD_NOTE); in-place needs no explanation.
                "note": _REBUILD_NOTE if result.method == "rebuild" else "",
                "download_url": self.mount_prefix + "/download?f="
                                + quote(str(out_path), safe=""),
                "txt_urls": [
                    {"name": p.name,
                     "url": self.mount_prefix + "/download?f="
                            + quote(str(p), safe="")}
                    for p in txts
                ],
            })

    return Handler
