"""Home do toolkit e ponto de entrada do servidor web.

Serve o menu de ferramentas em `/` e sobe o `main()`, que monta as seis
ferramentas num unico `ThreadingHTTPServer` (ver `pacct/web/mount.py`).

O Graphical Logic Viewer morava aqui -- eram 4.150 das 4.462 linhas deste
arquivo. Foi pra `pacct/web/glv/`, que agora abre N diagramas ao mesmo tempo,
cada um com o seu rele.

Uso:
    python3 app.py --web
"""

from __future__ import annotations

import argparse
import configparser
import logging
import time
from pathlib import Path

from pacct.parsers import rdb_cache
from pacct.paths import CACHE_DIR, DEFAULT_CONFIG_FILE, ensure_config_file
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import SCAN_MMS, SCAN_TELNET
from pacct.web.mount import Mount, serve
from pacct.web.session import SessionHandler, SessionManager

# -----------------------------------------------------------------------------
# Home / menu: lista de ferramentas de comissionamento
# -----------------------------------------------------------------------------

# A home nao serve mais o proprio `<style>`: os tokens e a casca vem de
# /theme.css (`pacct/web/themes/`), injetado no `<head>` pelo dispatcher.
#
# E nao serve mais a propria LISTA: as tres direcoes nao concordam na estrutura
# do menu (tabela numerada na folha, bornes com cor de fio na regua, fichas com
# clipe no caderno), entao o corpo e' o marcador `<!--HOME-->`, resolvido por
# requisicao com o tema do visitante. O catalogo em si e' dado, em
# `pacct/web/themes/items.py`. Aqui sobrou so a casca da tela.
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
    """Devolve a classe de handler da home (menu de ferramentas).

    Montada na raiz pelo dispatcher de `pacct.web.mount`; fica no ar durante
    toda a sessao, entao os cards sao links diretos pra `/<ferramenta>/` em vez
    do antigo POST /go que derrubava a home pra ceder a porta.
    """

    class HomeHandler(SessionHandler):
        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                # A nav e o corpo do menu sao resolvidos pelo dispatcher, que e'
                # quem sabe o tema do visitante (`mount.py:_resolve_markup`).
                self._send(200, HOME_HTML, "text/html; charset=utf-8")
            elif path == "/home-state":
                self._send(200, '{"ok": true}', "application/json")
            else:
                self._send(404, "not found", "text/plain")

    return HomeHandler


def _glv_scan_mode(cfg, logger) -> str:
    """`[web] glv_scan_mode`: qual modo vem marcado na tela de selecao.

    E' so' o PADRAO da tela -- o modo e' por diagrama, e o usuario troca no
    radio. Existe pra uma subestacao que ja padronizou o 61850 nao ter que
    trocar o radio a cada diagrama aberto.

    Um valor desconhecido nao pode passar em silencio: `pick_transport` cai
    pro telnet e a tela mostraria um radio marcado num modo que nao existe.
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

    # `config/config.ini` nao e' versionado -- e' o arquivo onde se digitam as
    # senhas ACC/2AC reais do rele. Num clone limpo ele nao existe, e o
    # ConfigParser leria o vazio sem reclamar: cada `cfg.get(...)` cairia no
    # fallback e a app subiria com a configuracao errada em silencio. Entao
    # semeamos do modelo versionado antes de ler.
    try:
        ensure_config_file(Path(args.config), logger)
    except FileNotFoundError as exc:
        raise SystemExit(f"[ERRO] {exc}") from exc

    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(args.config, encoding="utf-8")

    # Um unico servidor serve todas as ferramentas, cada uma sob seu prefixo.
    # Antes elas se revezavam na porta (abrir uma derrubava a outra); agora
    # ficam todas no ar ao mesmo tempo, e so uma porta precisa estar liberada
    # no firewall / portproxy do WSL.
    from pacct.web import gle_exporter, settings_compare, vb_updater, vlan_mapper
    from pacct.web.dnp_map.handler import build_dnp_map_handler
    from pacct.web.project_files.handler import build_project_files_handler

    # Cada visitante ganha estado e diretorio de upload proprios, identificados
    # por um cookie. Sem isso, o upload de um substituia o do outro em silencio
    # -- e como nomes de rele se repetem entre subestacoes, dava pra exportar
    # dados da obra errada sem nenhum erro aparente.
    ttl_hours = args.session_ttl_hours
    if ttl_hours is None:
        ttl_hours = cfg.getfloat("web", "session_ttl_hours", fallback=8.0)
    sessions = SessionManager(
        root=CACHE_DIR / "sessions", logger=logger,
        ttl_seconds=ttl_hours * 3600,
    )
    # Nenhum cookie sobrevive a um restart, entao o que sobrou no disco de uma
    # execucao anterior e' lixo.
    sessions.purge_root()

    # O cache de RDB (`cache/rdb/<sha256>/`) nao tem dono: nenhuma sessao e'
    # responsavel por ele e ele sobrevive ao restart de proposito. Quem o poda
    # e' o mesmo sweeper das sessoes, mais uma passada no boot.
    cache_gb = cfg.getfloat("web", "rdb_cache_max_gb", fallback=8.0)
    cache_days = cfg.getfloat("web", "rdb_cache_max_age_days", fallback=30.0)

    def _sweep_rdb_cache():
        rdb_cache.sweep(logger, max_gb=cache_gb, max_age_days=cache_days,
                        min_age_seconds=ttl_hours * 3600)

    sessions.on_sweep = _sweep_rdb_cache
    _sweep_rdb_cache()
    sessions.start_sweeper()

    # O config.ini e' a fonte dos valores PADRAO do GLV, lida uma vez aqui.
    # Cada diagrama carrega o proprio IP: abrir o segundo apontando pra outro
    # rele nao pode reescrever o do primeiro.
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
    # Tema padrao de quem ainda nao escolheu. De fabrica e' "caderno" (10
    # Caderno de Campo); a chave existe pra uma equipe padronizar outra. Valor
    # ausente ou invalido cai no padrao.
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
        # Apaga os diretorios de sessao: sao scratch, e nenhum cookie continua
        # valido depois daqui.
        sessions.shutdown()


if __name__ == "__main__":
    main()
