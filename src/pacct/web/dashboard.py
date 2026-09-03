"""The toolkit's home, and the web server's entry point.

Serves the tool menu at `/` and provides `main()`, which mounts the tools on a
single `ThreadingHTTPServer` (see `pacct/web/mount.py`).

The Graphical Logic Viewer used to live here -- 4,150 of this file's 4,462
lines. It moved to `pacct/web/glv/`, which now opens N diagrams at once, each
with its own relay.

Usage:
    python3 app.py --web
"""

from __future__ import annotations

import argparse
import configparser
import logging
import time
from pathlib import Path

from selfiles import rdb_cache

from pacct.paths import CACHE_DIR, DEFAULT_CONFIG_FILE, ensure_config_file
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import SCAN_MMS, SCAN_TELNET
from pacct.web.mount import Mount, serve
from pacct.web.session import SessionHandler, SessionManager

# -----------------------------------------------------------------------------
# Home / menu: lista de ferramentas de comissionamento
# -----------------------------------------------------------------------------

# The home no longer serves its own `<style>`: the tokens and the shell come
# from /theme.css (`pacct/web/themes/`), injected into the `<head>` by the
# dispatcher.
#
# Nor does it serve its own LIST any more: the three directions do not agree
# on the menu's structure (a numbered table in Folha, wire-coloured terminal
# blocks in Régua, clipped cards in Caderno), so the body is the `<!--HOME-->`
# marker, resolved per request with the visitor's theme in hand. The catalogue
# itself is data, in `pacct/web/themes/items.py`. What is left here is the
# screen's shell.
HOME_HTML = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PAC CT &mdash; Ferramentas</title>
<style>
  main a { color: var(--accent); }
</style>
</head>
<body>
<div class="page">
<header>
  <div>
    <h1>PAC CT</h1>
    <div class="sub">Comissionamento de prote&ccedil;&atilde;o, automa&ccedil;&atilde;o e controle</div>
  </div>
  <span class="spacer"></span>
</header>
<div class="shell">
<!--NAV:menu-->
<!--HOME-->
</div>
</div>
</body>
</html>
"""


def build_home_handler(logger: logging.Logger) -> type:
    """Return the home's handler class -- the tool menu.

    Mounted at the root by `pacct.web.mount`'s dispatcher, and up for the whole
    session, so the cards are direct links to `/<tool>/` rather than the old
    POST /go that took the home down to free the port.
    """

    class HomeHandler(SessionHandler):
        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                # The nav and the menu body are resolved by the dispatcher,
                # which is what knows the visitor's theme
                # (`mount.py:_resolve_markup`).
                self._send(200, HOME_HTML, "text/html; charset=utf-8")
            elif path == "/home-state":
                self._send(200, '{"ok": true}', "application/json")
            else:
                self._send(404, "not found", "text/plain")

    return HomeHandler


def _glv_scan_mode(cfg, logger) -> str:
    """`[web] glv_scan_mode`: which mode comes pre-selected on the picker.

    Only the screen's DEFAULT -- the mode is per diagram and the user changes
    it with the radio. It exists so a substation that has standardised on
    61850 does not have to change that radio for every diagram it opens.

    An unknown value must not pass in silence: `pick_transport` would fall back
    to telnet and the screen would show a radio selected on a mode that does
    not exist.
    """
    raw = (cfg.get("web", "glv_scan_mode", fallback=SCAN_TELNET) or "").strip().lower()
    if raw in (SCAN_TELNET, SCAN_MMS):
        return raw
    logger.warning("[web] glv_scan_mode=%r desconhecido; usando %r.",
                   raw, SCAN_TELNET)
    return SCAN_TELNET


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_FILE))
    ap.add_argument("--gle", default=None,
                    help="Arquivo GLE (override do [gle] file no config.ini)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--poll-interval", type=float, default=0.5)
    ap.add_argument("--session-ttl-hours", type=float, default=None,
                    help="Horas de ociosidade antes de expirar a sessao de um "
                         "usuario e apagar os uploads dela (default: 8, ou "
                         "[web] session_ttl_hours do config.ini).")
    ap.add_argument("--no-relay", action="store_true",
                    help="Modo visualizacao: desabilita o botao Conectar do GLV. "
                         "Os diagramas abrem com todos os bits indeterminados.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("dashboard")

    # `config/config.ini` is not versioned -- it is where the relay's real
    # ACC/2AC passwords are typed. A clean clone has none, and ConfigParser
    # would read the nothing without complaining: every `cfg.get(...)` would
    # take its fallback and the app would come up misconfigured, in silence.
    # So we seed one from the versioned model before reading.
    try:
        ensure_config_file(Path(args.config), logger)
    except FileNotFoundError as exc:
        raise SystemExit(f"[ERRO] {exc}") from exc

    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(args.config, encoding="utf-8")

    # One server serves every tool, each under its own prefix. They used to
    # take turns on the port -- opening one took the other down -- and now they
    # are all up at once, with a single port to open in the firewall or the
    # WSL portproxy.
    from pacct.web import gle_exporter, settings_compare, vb_updater, vlan_mapper
    from pacct.web.dnp_map.handler import build_dnp_map_handler
    from pacct.web.project_files.handler import build_project_files_handler

    # Every visitor gets their own state and upload directory, identified by
    # a cookie. Without that, one person's upload replaced another's in
    # silence -- and since relay names repeat across substations, you could
    # export the wrong job's data with no visible error at all.
    ttl_hours = args.session_ttl_hours
    if ttl_hours is None:
        ttl_hours = cfg.getfloat("web", "session_ttl_hours", fallback=8.0)
    sessions = SessionManager(
        root=CACHE_DIR / "sessions", logger=logger,
        ttl_seconds=ttl_hours * 3600,
    )
    # No cookie survives a restart, so whatever an earlier run left on disk
    # is rubbish.
    sessions.purge_root()

    # The RDB cache has no owner: no session is responsible for it and it
    # survives a restart on purpose. What prunes it is the sessions' own
    # sweeper, plus one pass at start-up.
    cache_gb = cfg.getfloat("web", "rdb_cache_max_gb", fallback=8.0)
    cache_days = cfg.getfloat("web", "rdb_cache_max_age_days", fallback=30.0)

    def _sweep_rdb_cache():
        rdb_cache.sweep(logger, max_gb=cache_gb, max_age_days=cache_days,
                        min_age_seconds=ttl_hours * 3600)

    sessions.on_sweep = _sweep_rdb_cache
    _sweep_rdb_cache()
    sessions.start_sweeper()

    # config.ini is the source of the GLV's DEFAULT values, read once here.
    # Each diagram carries its own IP: opening a second one pointed at another
    # relay must not rewrite the first one's.
    glv_defaults = GlvDefaults(
        ip=cfg.get("tcp", "ip_address", fallback=""),
        port=cfg.getint("tcp", "port", fallback=23),
        acc_password=cfg.get("auth", "acc_password", fallback="OTTER"),
        poll_interval=args.poll_interval,
        relay_name=cfg.get("relay", "name", fallback="(relé)"),
        gle_file=args.gle,
        no_relay=args.no_relay,
        max_links=cfg.getint("web", "glv_max_links", fallback=10),
        max_diagrams=cfg.getint("web", "glv_max_diagrams", fallback=10),
        setup_timeout=cfg.getfloat("web", "glv_setup_timeout", fallback=60.0),
        mms_interval_ms=cfg.getint("web", "glv_mms_interval_ms", fallback=100),
        scan_mode=_glv_scan_mode(cfg, logger),
    )

    mounts = [
        Mount("/", build_home_handler(logger), "Home"),
        Mount("/files",
              build_project_files_handler(logger, sessions),
              "Project Files"),
        Mount("/glv", build_glv_handler(logger, sessions, glv_defaults),
              "Graphical Logic Viewer"),
        Mount("/vb-updater", vb_updater.build_vb_updater_handler(logger, sessions),
              "VB Updater"),
        Mount("/vlan-mapper", vlan_mapper.build_vlan_mapper_handler(logger, sessions),
              "VLAN Mapper"),
        Mount("/gle-exporter",
              gle_exporter.build_gle_exporter_handler(logger, sessions),
              "GLE Variable Comment Exporter"),
        Mount("/settings-compare",
              settings_compare.build_settings_compare_handler(logger, sessions),
              "Settings Compare"),
        Mount("/dnp-map",
              build_dnp_map_handler(logger, sessions),
              "DNP Map Editor"),
    ]
    # The default theme for someone who has not chosen. The factory setting
    # is "caderno" (10 Caderno de Campo); the key exists so a team can
    # standardise on another. An absent or invalid value falls back to it.
    from pacct.web import theme as themes
    default_theme = themes.normalize(
        cfg.get("web", "theme", fallback=themes.DEFAULT_THEME).strip().lower())

    srv = serve(args.port, mounts, logger, sessions=sessions,
                default_theme=default_theme)
    logger.info("Sessoes por usuario: TTL de %.1f h, uploads em %s",
                ttl_hours, (CACHE_DIR / "sessions"))
    logger.info("Tema padrao: %s (%s) -- cada visitante troca no cabecalho.",
                default_theme, themes.THEMES[default_theme])
    logger.info("GLV: ate %d diagramas por visitante e %d conexoes simultaneas "
                "no processo (diagramas no mesmo ip:porta dividem uma).",
                glv_defaults.max_diagrams, glv_defaults.max_links)
    if args.no_relay:
        logger.info("Modo --no-relay: o botao Conectar do GLV fica desabilitado.")

    logger.info("Ctrl+C para encerrar.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Encerrando...")
    finally:
        srv.shutdown()
        srv.server_close()
        # Delete the session directories: they are scratch, and no cookie
        # stays valid past this point.
        sessions.shutdown()


if __name__ == "__main__":
    main()
