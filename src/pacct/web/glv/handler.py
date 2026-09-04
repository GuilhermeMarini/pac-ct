"""The GLV routes.

One page only (`/glv/`), with a tab strip: one diagram per tab, each with its
own relay. Switching tabs does not reload -- the client fetches `/meta?d=` and
re-renders the page strip and the viewer.

The list of diagrams belongs to the visitor's SESSION (cookie `selsid`), like
the other four tools. The connection to the relay does not: it belongs to the
process and is refcounted (see `link.py`), because there is only one relay and
it accepts few simultaneous sessions.

There is no more `GlvMount.active` and no session thread blocked on an Event:
the GLE selector became the page `/novo`, and connecting became
`POST /connect?d=`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, quote, urlparse

from selfiles.models import relay_models
from selfiles.rdb import find_gle, relays_to_dict

from pacct.paths import resolve_gle_path
from pacct.web.glv import load_template
from pacct.web.glv.diagram import build_diagram
from pacct.web.glv.link import LinkPool
from pacct.web.glv.notes import NOTE_MAX_BYTES
from pacct.web.glv.transport import DEFAULT_PORTS, SCAN_MMS
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

DASHBOARD_HTML = load_template("dashboard.html")
# The `<!--NAV:glv-->` inside `.shell` is resolved per request in
# `mount.py:_resolve_markup()`, with the visitor's theme in hand. Do not
# substitute it here: the three directions do not share the nav markup.
LANDING_HTML = load_template("landing.html")

_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _valid_ipv4(s: str) -> bool:
    if not _IP_RE.match(s):
        return False
    return all(0 <= int(p) <= 255 for p in s.split("."))


@dataclass(frozen=True)
class GlvDefaults:
    """Default values read from config.ini ONCE, at boot.

    `setup_relay` used to read the cfg directly and the selection screen wrote
    into it -- with two diagrams, opening the second rewrote the first's IP.
    """

    ip: str = ""
    port: int = 23
    acc_password: str = "OTTER"
    poll_interval: float = 0.5
    relay_name: str = "(relé)"
    gle_file: str | None = None    # --gle: semeia um diagrama na sessao nova
    no_relay: bool = False           # --no-relay: sem botao Conectar
    max_links: int = 4
    max_diagrams: int = 8
    setup_timeout: float = 60.0      # caps login+autoconfig, not discovery
    mms_interval_ms: int = 100       # periodo inicial do modo MMS
    scan_mode: str = "telnet"        # padrao da tela de selecao


class _GlvSession:
    """The diagrams a visitor has open."""

    def __init__(self):
        self.diagrams: dict = {}
        self.order: list = []
        self.active: str | None = None
        self.counter: int = 0
        self.rdb = None              # the picker's most recent RDB


def build_glv_handler(logger, sessions, defaults: GlvDefaults) -> type:
    """Return the GLV handler class, mounted at `/glv/` by the dispatcher."""

    pool = LinkPool(logger, max_links=defaults.max_links)

    def _new_session():
        st = _GlvSession()
        if defaults.gle_file:
            # `--gle`: opens that file directly, skipping the selector.
            path = resolve_gle_path(defaults.gle_file)
            if path.is_file():
                _open_diagram(st, path, defaults.relay_name, path.stem,
                              defaults.ip, defaults.port, None)
            else:
                logger.error("Arquivo GLE nao encontrado: %s -- abrindo o "
                             "seletor.", path)
        return st

    def _open_diagram(st, gle_path, relay_name, gle_name, ip, port, relay_model,
                      scan_mode=defaults.scan_mode, scd_sha=None, scd_path=None):
        st.counter += 1
        did = f"d{st.counter}"
        d = build_diagram(did, gle_path, relay_name, gle_name, ip, port,
                          relay_model, logger, scan_mode=scan_mode,
                          scd_sha=scd_sha, scd_path=scd_path)
        st.diagrams[did] = d
        st.order.append(did)
        st.active = did
        return d

    def _close_session(sess):
        """Session expired: drop the connections before the dir rmtree."""
        st = sess.data.get("glv")
        if st is None:
            return
        for d in list(st.diagrams.values()):
            d.close(pool)
        st.diagrams.clear()
        st.order.clear()
        st.active = None

    sessions.on_expire = _close_session

    class Handler(SessionHandler):
        session_key = "glv"
        state_factory = staticmethod(_new_session)
        server_sessions = sessions

        # -- helpers --------------------------------------------------------

        def _diagram(self, qs):
            """Diagrama de `?d=<id>`, ou o ativo. None -> ja respondeu 404."""
            st = self.sess()
            did = (qs.get("d") or [""])[0] or st.active
            d = st.diagrams.get(did) if did else None
            if d is None:
                self._send_json(404, {"error": "diagrama não encontrado"})
                return None, None
            return st, d

        def _tabs_payload(self, st) -> dict:
            return {
                "diagrams": [st.diagrams[i].tab(defaults) for i in st.order
                             if i in st.diagrams],
                "active": st.active,
                "max_diagrams": defaults.max_diagrams,
                "max_links": defaults.max_links,
                "no_relay": defaults.no_relay,
                "links": pool.snapshot(),
            }

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            return self.rfile.read(length) if length > 0 else b"", length

        def _landing_state(self) -> dict:
            st = self.sess()
            info = st.rdb
            base = {
                "no_relay_default": defaults.no_relay,
                "no_relay_locked": defaults.no_relay,
                "open_diagrams": len(st.diagrams),
                # Default for every row of the selection screen. The landing
                # hardcoded 'telnet' in the HTML and always sent an explicit
                # mode in the POST, so `[web] glv_scan_mode` was only a
                # fallback for a client that said nothing -- and this client
                # always says. Setting `mms` in config.ini changed nothing on
                # the screen.
                "scan_mode": defaults.scan_mode,
                "max_diagrams": defaults.max_diagrams,
            }
            if info is None:
                base["has_rdb"] = False
                return base
            base.update({
                "has_rdb": True,
                "rdb_name": info.display_name,
                "sha256": info.sha256,
                "reused": info.reused,
                "relays": relays_to_dict(info.relays),
            })
            return base

        # -- GET ------------------------------------------------------------

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                st = self.sess()
                if not st.order:
                    # A shell with no tabs at all has nothing to show.
                    self._redirect(f"{self.mount_prefix}/novo")
                    return
                did = (qs.get("d") or [""])[0]
                if did in st.diagrams:
                    st.active = did
                html = DASHBOARD_HTML.replace(
                    "${BOOT_JSON}",
                    json.dumps(self._tabs_payload(st), ensure_ascii=False))
                self._send(200, html, "text/html; charset=utf-8")
                return

            if path == "/novo":
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return

            if path == "/landing-state":
                self._send_json(200, self._landing_state())
                return

            if path == "/diagrams":
                self._send_json(200, self._tabs_payload(self.sess()))
                return

            if path == "/meta":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_json(200, d.meta(defaults))
                return

            if path == "/values":
                st, d = self._diagram(qs)
                if d is None:
                    return
                page = (qs.get("page") or [""])[0]
                self._send(200, json.dumps(d.values(page)), "application/json")
                return

            if path.startswith("/pages/"):
                st, d = self._diagram(qs)
                if d is None:
                    return
                safe_id = path[len("/pages/"):]
                svg = d.svgs.get(safe_id)
                if svg is None:
                    self._send(404, "not found", "text/plain")
                else:
                    # This is where we learn which page the visitor opened:
                    # this GET is the only path to the SVG, and both the page
                    # strip click and the variable search's navigation go
                    # through it. See `GlvDiagram.remember_page`.
                    d.remember_page(safe_id)
                    self._send(200, svg, "image/svg+xml")
                return

            if path == "/group-state":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_json(200, d.notes.group_payload())
                return

            if path == "/note":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_json(200, d.notes.note_payload())
                return

            if path == "/highlights":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_json(200, d.notes.highlight_payload())
                return

            if path == "/unreachable":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_json(200, d.unreachable())
                return

            if path == "/unreachable.txt":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send_unreachable_txt(d)
                return

            if path == "/debug/analogs":
                st, d = self._diagram(qs)
                if d is None:
                    return
                self._send(200, json.dumps(d.debug_analogs(), default=str),
                           "application/json")
                return

            self._send(404, "not found", "text/plain")

        def _send_unreachable_txt(self, d) -> None:
            """The same list as a file, to be pasted into the IED model's
            editor. Three hundred names do not copy off the screen, and the
            header commented with `#` says which relay and which drawing they
            came from without becoming a name by accident."""
            out = d.unreachable()
            head = [
                f"# GLV — variáveis fora do alcance da leitura {out['scan_mode']}",
                f"# relé: {d.relay_name}   desenho: {d.gle_name}",
                f"# {out['count']} de {out['total']} variáveis do desenho",
                f"# gerado em {datetime.now():%Y-%m-%d %H:%M}",
            ]
            if not out["available"]:
                head.append("# (diagrama desconectado — lista indisponível)")
            body = ("\n".join(head + list(out["names"])) + "\n").encode("utf-8")
            name = f"{d.relay_name}_{d.gle_name}_fora_do_modelo.txt"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # RFC 5987: relay names have accents (see `project_files/handler`).
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''"
                             + quote(name, safe=""))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # -- POST -----------------------------------------------------------

        def do_POST(self):
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query)

            if route == "/select-rdb":
                self._select_rdb()
                return
            if route == "/diagrams":
                self._create_diagram()
                return
            if route == "/diagrams/batch":
                self._create_diagrams_batch()
                return
            if route == "/diagrams/close":
                st, d = self._diagram(qs)
                if d is None:
                    return
                d.close(pool)
                st.diagrams.pop(d.id, None)
                if d.id in st.order:
                    st.order.remove(d.id)
                if st.active == d.id:
                    st.active = st.order[-1] if st.order else None
                self._send_json(200, self._tabs_payload(st))
                return
            if route == "/diagrams/activate":
                st, d = self._diagram(qs)
                if d is None:
                    return
                st.active = d.id
                self._send_json(200, {"active": st.active})
                return
            if route == "/connect":
                st, d = self._diagram(qs)
                if d is None:
                    return
                if defaults.no_relay:
                    self._send_json(409, {
                        "error": "modo visualização (--no-relay): conexão "
                                 "desabilitada nesta execução"})
                    return
                job_id = d.connect_async(pool, defaults)
                self._send_json(202, {"job": job_id, "diagram": d.id})
                return
            if route == "/disconnect":
                st, d = self._diagram(qs)
                if d is None:
                    return
                d.disconnect(pool)
                self._send_json(200, d.tab(defaults))
                return
            if route == "/period":
                st, d = self._diagram(qs)
                if d is None:
                    return
                body, _ = self._body()
                try:
                    ms = int(json.loads(body or b"{}").get("interval_ms"))
                except (TypeError, ValueError, AttributeError,
                        json.JSONDecodeError):
                    self._send_json(400, {"error": "intervalo inválido"})
                    return
                self._send_json(200, d.set_interval_ms(ms))
                return
            if route in ("/group-state", "/note", "/highlights"):
                self._notes_post(route, qs)
                return

            self._send(404, "not found", "text/plain")

        # -- escolha do RDB e criacao ---------------------------------------

        def _select_rdb(self):
            """The body of the old /rdb-upload, from `process_upload` on.

            The file already entered the project at `/files/` and has already
            been extracted once; here we only point at it.
            """
            body, _ = self._body()
            try:
                payload = json.loads(body or b"{}")
            except (json.JSONDecodeError, ValueError):
                payload = {}
            sha = str(payload.get("sha256") or "").strip()
            lib = filelib.library_for(sessions, self.require_session())
            with self.require_session().lock:
                entry = lib.get(sha)
            if entry is None or entry.kind != filelib.KIND_RDB:
                self._send_json(404, {
                    "error": "Arquivo nao esta mais no projeto."})
                return
            info = entry.require_rdb()
            self.sess().rdb = info
            logger.info("[glv] RDB '%s' (%s) escolhido; %d rele(s) com GLE",
                        info.display_name, info.sha256[:16], len(info.relays))
            self._send_json(200, self._landing_state())

        def _resolve_scd_path(self, scd_sha: str | None):
            """SCD chosen for this MMS diagram, from the same library as
            `/files/`. Resolved HERE, in the request, because this handler is
            the only place with the session (`self.session`) in hand -- the
            diagram is built without touching the network and connects in a
            thread of its own that has only what this method stored on it.

            Absence (empty sha, file left the project, or not an SCD) is not
            an error: the MMS transport still has the factory table as a
            second source, and a diagram without an SCD is a valid case, just
            less covered.
            """
            if not scd_sha:
                return None
            lib = filelib.library_for(sessions, self.require_session())
            with self.require_session().lock:
                entry = lib.get(scd_sha)
            if entry is None or entry.kind != filelib.KIND_SCD:
                logger.warning(
                    "[glv] SCD %s não está mais no projeto; MMS segue só com "
                    "a tabela de fábrica.", scd_sha[:16])
                return None
            return entry.scd_path

        def _resolve_gle(self, st, relay: str, gle: str, ip_raw: str):
            """Validate a (relay, GLE) pair + IP and resolve the relay model.

            Returns `((path, model, ip), None)` or `(None, (status, msg))`.
            It is the only place these checks live: `/diagrams` opens one and
            `/diagrams/batch` opens several, and both have to refuse for the
            same reasons.
            """
            info = st.rdb
            if info is None:
                return None, (409, "nenhum RDB carregado")
            entry = find_gle(info, relay, gle)
            if entry is None or not entry.fs_path.is_file():
                return None, (404, f"GLE não encontrado: {relay} / {gle}")
            ip = (ip_raw or "").strip()
            if ip and not _valid_ipv4(ip):
                return None, (400, f"IP inválido em {relay}: {ip_raw}")
            if not ip and not defaults.no_relay:
                # Sem IP o diagrama abre igual, so nunca vai poder conectar --
                # e' melhor dizer agora do que deixar o botao morto na tela.
                return None, (400, f"IP do relé {relay} é obrigatório")

            # Resolve the relay model (governs derived_bits, layout and read
            # mode). With no JSON in relay_models/, we carry on with defaults.
            relay_model = None
            rel = next((r for r in info.relays if r.name == relay), None)
            if rel is not None and rel.model:
                relay_model = relay_models.lookup(rel.model)
                if relay_model is not None:
                    logger.info("[glv] modelo do rele: %s -> %s", rel.model,
                                relay_model.source_path.name
                                if relay_model.source_path else relay_model.model)
                else:
                    logger.info("[glv] modelo %r sem JSON em relay_models/; "
                                "usando defaults.", rel.model)
            return (entry.fs_path, relay_model, ip), None

        def _create_diagram(self):
            body, _ = self._body()
            try:
                payload = json.loads(body or b"{}")
                relay = str(payload["relay"])
                gle = str(payload["gle"])
                ip_raw = str(payload.get("ip") or "")
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                self._send_json(400, {"error": "bad request"})
                return
            scan_mode = str(payload.get("scan_mode") or defaults.scan_mode)
            scd_sha = (str(payload.get("scd_sha") or "").strip() or None)
            st = self.sess()
            if len(st.diagrams) >= defaults.max_diagrams:
                self._send_json(409, {
                    "error": f"limite de {defaults.max_diagrams} diagramas "
                             f"abertos; feche um antes de abrir outro"})
                return
            resolved, err = self._resolve_gle(st, relay, gle, ip_raw)
            if err is not None:
                self._send_json(err[0], {"error": err[1]})
                return
            gle_path, relay_model, ip = resolved
            # Only MMS overrides the port. `DEFAULT_PORTS.get(scan_mode, ...)`
            # returned 23 for telnet mode and rode over config.ini's
            # `[tcp] port` -- a substation behind a terminal server or a
            # port-forward (`port = 2001`) started connecting on 23, in
            # silence. The paradox gave the accident away: an UNKNOWN
            # scan_mode honoured config.ini and the documented "telnet" did
            # not.
            port = (DEFAULT_PORTS[SCAN_MMS] if scan_mode == SCAN_MMS
                    else defaults.port)
            scd_path = self._resolve_scd_path(scd_sha)
            d = _open_diagram(st, gle_path, relay, gle, ip, port, relay_model,
                              scan_mode=scan_mode, scd_sha=scd_sha,
                              scd_path=scd_path)
            logger.info("[glv] diagrama %s aberto: %s / %s em %s:%d (modo "
                        "%s, desconectado)", d.id, relay, gle,
                        ip or "(sem IP)", port, scan_mode)
            self._send_json(200, {"id": d.id, **self._tabs_payload(st)})

        def _create_diagrams_batch(self):
            """Open several GLEs at once, ticked in the selector.

            Validates the WHOLE list before opening anything: a half-done
            batch is worse than no batch -- nobody knows what got in without
            counting the tabs. Past validation, only a render failure drops
            one item, and the others keep opening.
            """
            body, _ = self._body()
            try:
                payload = json.loads(body or b"{}")
                raw = payload["items"]
                if not isinstance(raw, list) or not raw:
                    raise ValueError("lista vazia")
                items = [(str(it["relay"]), str(it["gle"]),
                          str(it.get("ip") or ""),
                          str(it.get("scan_mode") or defaults.scan_mode),
                          (str(it.get("scd_sha") or "").strip() or None))
                         for it in raw]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                self._send_json(400, {"error": "bad request"})
                return

            st = self.sess()
            free = defaults.max_diagrams - len(st.diagrams)
            if len(items) > free:
                self._send_json(409, {
                    "error": f"{len(items)} GLE selecionados e {free} vaga(s) "
                             f"de {defaults.max_diagrams}; feche uma aba ou "
                             f"desmarque itens"})
                return

            resolved, problems = [], []
            for relay, gle, ip_raw, scan_mode, scd_sha in items:
                data, err = self._resolve_gle(st, relay, gle, ip_raw)
                if err is None:
                    resolved.append((relay, gle, data, scan_mode, scd_sha))
                else:
                    problems.append(err[1])
            if problems:
                self._send_json(400, {"error": problems[0],
                                      "errors": problems})
                return

            job = self.job()
            ids, errors = [], []
            total = len(resolved)
            for i, (relay, gle, data, scan_mode, scd_sha) in enumerate(resolved):
                gle_path, relay_model, ip = data
                # Mesma regra do `_create_diagram`: so' o MMS troca a porta,
                # o resto respeita o `[tcp] port` do config.ini.
                port = (DEFAULT_PORTS[SCAN_MMS] if scan_mode == SCAN_MMS
                        else defaults.port)
                scd_path = self._resolve_scd_path(scd_sha)
                job.fraction(f"Renderizando {gle} ({i + 1}/{total})", i, total)
                try:
                    d = _open_diagram(st, gle_path, relay, gle, ip, port,
                                      relay_model, scan_mode=scan_mode,
                                      scd_sha=scd_sha, scd_path=scd_path)
                except Exception as e:   # one bad GLE must not take the batch with it
                    logger.exception("[glv] falha ao abrir %s / %s", relay, gle)
                    errors.append(f"{relay} / {gle}: {e}")
                    continue
                ids.append(d.id)
                logger.info("[glv] diagrama %s aberto: %s / %s em %s:%d (modo "
                            "%s, desconectado)", d.id, relay, gle,
                            ip or "(sem IP)", port, scan_mode)
            job.finish(f"{len(ids)} diagrama(s) aberto(s)")
            if ids:
                # `_open_diagram` left the last one active; the tab the client
                # opens is the batch's first, so that is the one that stays.
                st.active = ids[0]
            self._send_json(200, {"ids": ids, "errors": errors,
                                  **self._tabs_payload(st)})

        # -- notas -----------------------------------------------------------

        def _notes_post(self, route: str, qs):
            st, d = self._diagram(qs)
            if d is None:
                return
            body, length = self._body()
            if route == "/note" and length > NOTE_MAX_BYTES:
                self._send(413, '{"error":"payload too large"}',
                           "application/json")
                return

            if route == "/group-state":
                try:
                    data = json.loads(body or b"{}")
                    gid = str(data["group_id"])
                    checked = bool(data["checked"])
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    self._send(400, '{"error":"bad request"}', "application/json")
                    return
                try:
                    d.notes.set_group(gid, checked)
                except OSError as e:
                    self._send(500, json.dumps({"error": str(e)}),
                               "application/json")
                    return
                self._send(200, "{}", "application/json")
                return

            if route == "/note":
                try:
                    data = json.loads(body or b"{}")
                    scope = str(data.get("scope", "relay"))
                    html_body = str(data.get("html", ""))
                    page_id = str(data.get("page", "")) if scope == "page" else ""
                except (json.JSONDecodeError, ValueError, TypeError):
                    self._send(400, '{"error":"bad request"}', "application/json")
                    return
                if scope not in ("relay", "page"):
                    self._send(400, '{"error":"invalid scope"}', "application/json")
                    return
                if scope == "page" and not page_id:
                    self._send(400, '{"error":"missing page id"}',
                               "application/json")
                    return
                if len(html_body) > NOTE_MAX_BYTES:
                    self._send(413, '{"error":"payload too large"}',
                               "application/json")
                    return
                try:
                    d.notes.set_note(scope, page_id, html_body)
                except OSError as e:
                    self._send(500, json.dumps({"error": str(e)}),
                               "application/json")
                    return
                self._send(200, "{}", "application/json")
                return

            # /highlights
            try:
                data = json.loads(body or b"{}")
                page = str(data["page"])
                item_id = str(data["item_id"])
                highlighted = bool(data["highlighted"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                self._send(400, '{"error":"bad request"}', "application/json")
                return
            try:
                d.notes.set_highlight(page, item_id, highlighted)
            except OSError as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
                return
            self._send(200, "{}", "application/json")

    return Handler
