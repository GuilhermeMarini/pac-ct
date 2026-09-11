"""Routes for the VLAN Mapper.

Absolute routes, like every other tool: `mount.py`'s single dispatcher strips
the `/vlan-mapper` prefix before delegating, and the shim rewrites the client's
`fetch` calls.

Four routes and no export: the tool answers questions about one SCD and writes
nothing. Everything it computes lives in `model.py`; what is left here is
reading the chosen file out of the project library, reporting progress, and
turning a dict into JSON.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler
from pacct.web.vlan_mapper import load_template, model

# The `<!--NAV:vlan-mapper-->` marker inside the shell is resolved per request
# by `mount.py:_resolve_markup()`, with the visitor's theme in hand. It must
# NOT be substituted here: the three directions do not share nav markup, so
# resolving at import time would freeze one direction's markup into all three.
LANDING_HTML = load_template("landing.html")


def build_vlan_mapper_handler(logger: logging.Logger, sessions) -> type:
    """Return the VLAN Mapper handler class.

    Opens no server: serving is done by the single dispatcher of
    `pacct.web.mount`, which mounts this handler at `/vlan-mapper/`. State
    and uploads are per session (`self.sess()` / `self.sdir()`), not per
    process.
    """

    class Handler(SessionHandler):
        session_key = "vlan-mapper"
        state_factory = model.VlanMapperState
        server_sessions = sessions

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/vlan-state":
                # Sentinel the home uses to detect that this tool is up.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                self._send_json(200, model.state_payload(self.sess()))
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/select-scd":
                # The body of the old /scd-upload, from `load_scd` onward:
                # the file was already received and validated in /files/.
                body = self._read_json_body()
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.require_session())
                with self.require_session().lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != filelib.KIND_SCD:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                job = self.job()
                job.stage("Lendo IEDs e VLANs do SCD", 10)
                try:
                    payload = model.build_payload(entry.require_scd_path(),
                                                  entry.display_name)
                except Exception as e:
                    logger.exception("falha computando VLAN map: %s", e)
                    self._send_json(500, {
                        "error": f"falha computando VLAN map: {e}"})
                    return
                st = self.sess()
                with self.require_session().lock:
                    st.scd_path = entry.scd_path
                    st.scd_name = entry.display_name
                    st.payload = payload
                job.finish(f"{payload['ied_count']} IED(s), "
                           f"{payload['vlan_count']} VLAN(s)")
                logger.info(
                    "[vlan-mapper] SCD '%s': %d IED(s), %d VLAN(s) distintos",
                    entry.display_name, payload["ied_count"],
                    payload["vlan_count"],
                )
                self._send_json(200, payload)
                return

            self._send(404, "not found", "text/plain")

    return Handler
