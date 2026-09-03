"""Monta varias ferramentas num unico servidor HTTP, por prefixo de caminho.

Antes, cada ferramenta subia o proprio `ThreadingHTTPServer` na mesma porta e
o `main()` rodava uma de cada vez: pra abrir o VB Updater era preciso derrubar
o servidor da home, e vice-versa. Aqui um unico servidor fica no ar em
`:8765` e roteia por prefixo:

    /                       -> home (menu)
    /vb-updater/...         -> VB Updater
    /vlan-mapper/...        -> VLAN Mapper
    /gle-exporter/...       -> GLE Variable Comment Exporter
    /settings-compare/...   -> Settings Compare
    /glv/...                -> Graphical Logic Viewer

Assim varias ferramentas ficam disponiveis ao mesmo tempo, cada uma na sua
aba, e so uma porta precisa ser liberada no firewall / portproxy do WSL.

Duas pecas fazem isso funcionar:

1. `make_dispatcher()` -- do lado do servidor. Cada ferramenta continua
   definindo o proprio `BaseHTTPRequestHandler` com rotas absolutas
   (`/state`, `/download`, ...). O dispatcher tira o prefixo de `self.path` e
   troca `self.__class__` pela classe da ferramenta, entao o handler original
   roda sem enxergar o prefixo -- nenhuma rota precisou ser reescrita.

2. `inject_head()` -- do lado do cliente. O JS embutido nas paginas usa
   caminhos absolutos (`fetch('/state')`), que sob prefixo bateriam em
   `/state` e dariam 404. O shim injetado no `<head>` reescreve `fetch` e
   `XMLHttpRequest.open` pra prefixar caminhos absolutos, entao o JS existente
   tambem ficou intacto. A mesma injecao poe a folha de tema (`/theme.css`) e
   marca `data-theme` no `<html>` em TODAS as telas -- por isso ela nao e' mais
   so o shim, e roda tambem na home (prefixo vazio). O seletor de tema, esse,
   so e' montado na home: o tema e' um cookie de um ano, escolhido no menu.

O dispatcher tambem serve quatro rotas comuns a todas as ferramentas, antes de
casar prefixo: `/progress`, `/theme.css`, `/static/...` (as fontes .woff2 que
acompanham o projeto) e `/library` (a lista de arquivos do projeto do
visitante, que o seletor de cada ferramenta le). Nenhuma delas pertence a uma
ferramenta so.
"""

from __future__ import annotations

import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from pacct.paths import STATIC_DIR, is_within
from pacct.web import theme as themes

# -----------------------------------------------------------------------------
# Shim de cliente
# -----------------------------------------------------------------------------

# Reescreve caminhos absolutos ("/state") pra "<prefixo>/state" em `fetch` e
# `XMLHttpRequest`. Nao mexe em URLs absolutas com esquema (http://...), em
# caminhos protocol-relative ("//host/x") nem em caminhos relativos.
#
# `/` sozinho e' preservado de proposito: e' o link "<- Menu", que deve ir pra
# home de verdade, e nao pra raiz da ferramenta.
_PREFIX_SHIM = """<script>
(function () {
  var P = %s;
  window.MOUNT_PREFIX = P;
  function fix(u) {
    if (typeof u !== 'string') return u;
    if (u.charAt(0) !== '/' || u.charAt(1) === '/') return u;
    if (u === '/') return u;              // "<- Menu" vai pra home real
    if (u.indexOf(P + '/') === 0) return u;  // ja prefixado
    return P + u;
  }
  window.MOUNT_URL = fix;
  var of = window.fetch;
  if (of) {
    window.fetch = function (input, init) {
      if (typeof input === 'string') input = fix(input);
      else if (input && input.url) input = new Request(fix(input.url), input);
      return of.call(this, input, init);
    };
  }
  var oo = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, url) {
    var rest = Array.prototype.slice.call(arguments, 2);
    return oo.apply(this, [m, fix(url)].concat(rest));
  };
})();
</script>
"""

_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HTML_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)

# -----------------------------------------------------------------------------
# Tema
# -----------------------------------------------------------------------------

# O seletor e' montado por JS porque a home tem cabecalho proprio: em vez de
# editar a marcacao, ele se pendura no `<header>` (ou `.masthead`) da pagina.
# Sem cabecalho, cai num canto fixo -- abaixo da barra de progresso, que ocupa
# o topo da viewport.
#
# Ele so vai pra HOME (prefixo vazio). Antes ia pras nove telas, e nos
# cabecalhos de aplicacao -- o do GLV, que ja carrega titulo, estado, busca,
# ferramentas e zoom -- virava mais um grupo espremido numa faixa que ja
# quebrava em varias linhas. O tema e' um cookie de um ano: escolhe-se uma vez,
# no menu, e ele segue o visitante por todas as telas.
_THEME_PICKER = """<script>
(function () {
  var THEMES = %s, CURRENT = %s;
  function mount() {
    var box = document.createElement('div');
    box.className = 'theme-picker';
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', 'Tema da interface');
    var cap = document.createElement('span');
    cap.className = 'lbl';
    cap.textContent = 'Tema';
    box.appendChild(cap);
    THEMES.forEach(function (t) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = t.short;
      b.title = t.name;
      b.setAttribute('aria-label', t.name);
      b.setAttribute('aria-pressed', t.slug === CURRENT ? 'true' : 'false');
      b.addEventListener('click', function () {
        if (t.slug === CURRENT) return;
        var x = new XMLHttpRequest();
        // Rota comum a todas as montagens: sempre na raiz, nunca no prefixo.
        x.open('POST', '/theme', true);
        x.setRequestHeader('Content-Type', 'application/json');
        x.onloadend = function () { location.reload(); };
        x.send(JSON.stringify({theme: t.slug}));
      });
      box.appendChild(b);
    });
    var host = document.querySelector('header, .masthead, .top, .hdr, .head');
    if (host) {
      host.appendChild(box);
    } else {
      box.style.cssText = 'position:fixed;top:8px;right:10px;z-index:9999';
      document.body.appendChild(box);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
</script>
"""


def _picker_script(active: str) -> str:
    listing = [
        {"slug": slug, "name": name, "short": name.split(" ")[0]}
        for slug, name in themes.THEMES.items()
    ]
    return _THEME_PICKER % (json.dumps(listing, ensure_ascii=False),
                            json.dumps(active))


# Marcadores que a direcao resolve. Ficam no HTML das telas em vez da marcacao
# pronta porque as tres direcoes NAO emitem a mesma estrutura: a navegacao e'
# `.toc` na folha, `.strip`/`.borne` na regua e `.tabs`/`.tab` no caderno, e o
# menu e' tabela, borne ou ficha com clipe. Antes isto era resolvido no import
# de cada ferramenta, o que congelava a marcacao da folha nos tres temas.
_NAV_RE = re.compile(r"<!--NAV:([a-z0-9-]*)-->")
_HOME_RE = re.compile(r"<!--HOME-->")


def _resolve_markup(html: str, theme: str) -> str:
    """Troca os marcadores de tela pela marcacao da direcao ativa."""
    html = _NAV_RE.sub(lambda m: themes.nav_html(theme, m.group(1)), html)
    return _HOME_RE.sub(lambda _m: themes.home_html(theme), html)


def inject_head(html: str, prefix: str, theme: str = themes.DEFAULT_THEME) -> str:
    """Injeta shim de prefixo, folha de tema e seletor de tema no `<head>`.

    Antes isto era so o shim, e por isso nao rodava na home (prefixo vazio).
    Agora roda sempre: a home tambem precisa de tema.

    O SELETOR, porem, so entra na home. A folha de tema e o `data-theme` vao
    em todas as telas -- o que nao vai mais e' o grupo de botoes de troca.

    A folha entra logo apos `<head>`, ANTES do `<style>` da ferramenta -- assim
    os tokens ja existem quando a ferramenta os usa, e o que ainda nao foi
    convertido continua vencendo por ordem de cascata.
    """
    theme = themes.normalize(theme)
    html = _resolve_markup(html, theme)
    head = f'<link rel="stylesheet" href="{prefix}/theme.css">\n'
    if prefix:
        head += _PREFIX_SHIM % json.dumps(prefix)
    else:
        # So a home (prefixo vazio) monta o seletor de tema.
        head += _picker_script(theme)

    m = _HEAD_RE.search(html)
    html = (html[: m.end()] + "\n" + head + html[m.end():]) if m else head + html

    # `data-theme` no `<html>`: e' o seletor dos blocos por tema do theme.css.
    def stamp(mo):
        tag = mo.group(0)
        if "data-theme" in tag.lower():
            return tag
        return tag[:-1] + f' data-theme="{theme}">'

    return _HTML_RE.sub(stamp, html, count=1)


# Nome antigo: era so o shim de prefixo. Mantido pra nao quebrar import de fora.
inject_prefix_shim = inject_head


# -----------------------------------------------------------------------------
# Dispatcher
# -----------------------------------------------------------------------------

class Mount:
    """Uma ferramenta montada: prefixo + classe de handler + rotulo pro log."""

    __slots__ = ("prefix", "handler", "label")

    def __init__(self, prefix: str, handler: type, label: str):
        # Normaliza pra "/nome" (sem barra final); raiz vira "".
        prefix = "/" + prefix.strip("/")
        self.prefix = "" if prefix == "/" else prefix
        self.handler = handler
        self.label = label
        # O handler usa isso pra montar URLs absolutas que o cliente navega
        # direto (download_url), fora do alcance do shim de `fetch`.
        handler.mount_prefix = self.prefix


def make_dispatcher(mounts: list[Mount], sessions=None,
                    default_theme: str = themes.DEFAULT_THEME) -> type:
    """Cria o handler que roteia por prefixo entre as ferramentas montadas.

    A montagem na raiz (prefixo "") e' o fallback: qualquer caminho que nao
    casar com um prefixo cai nela.

    `sessions` e' um `SessionManager` (ou None). Quando presente, o dispatcher
    resolve a sessao do visitante ANTES de delegar e deixa `session` e
    `_set_cookie` na instancia -- os handlers que herdam de `SessionHandler`
    leem dali.

    `default_theme` e' o tema de quem ainda nao escolheu ([web] theme do
    config.ini). O tema do visitante sai do cookie `seltheme` e tambem fica na
    instancia, em `self.theme`, antes da delegacao.
    """
    default_theme = themes.normalize(default_theme)
    prefixed = [m for m in mounts if m.prefix]
    root = next((m for m in mounts if not m.prefix), None)

    class Dispatcher(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silencia stderr; cada tool loga o que interessa

        def end_headers(self):
            """Emite o `Set-Cookie` da sessao nova, igual `SessionHandler`.

            Precisa estar aqui por causa do redirecionamento
            "/vb-updater" -> "/vb-updater/": ele e' respondido pelo proprio
            dispatcher, DEPOIS de `resolve()` ter criado a sessao, e sem
            trocar `self.__class__` -- entao caia no `end_headers` do
            `BaseHTTPRequestHandler`, que nao sabe de cookie, e o visitante
            chegava na ferramenta ainda sem identidade.

            As rotas de infraestrutura nao dependem disto: elas nao criam
            sessao nenhuma (ver `_dispatch`). Quando `self.__class__` e'
            trocado pela ferramenta, quem responde e' o `end_headers` do
            `SessionHandler`; os dois nunca rodam na mesma resposta.
            """
            cookie = getattr(self, "_set_cookie", None)
            if cookie:
                self.send_header("Set-Cookie", cookie)
                self._set_cookie = None
            super().end_headers()

        def _redirect(self, location: str):
            self.send_response(301)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _dispatch(self, verb: str):
            cookie = self.headers.get("Cookie")
            self.theme = themes.resolve(cookie, default_theme)
            path = urlparse(self.path).path
            tail = self._strip_prefix(path)

            # -- rotas de infraestrutura -------------------------------------
            # Sao servidas ANTES de resolver a sessao, e de proposito: elas
            # nao sao donas de identidade nenhuma. Uma folha de estilo, um
            # .woff2 ou um poll da barra de progresso que chegue sem cookie
            # nao pode inventar um visitante -- isso enchia o servidor de
            # sessoes fantasma e, pior, cada resposta dessas passava a mandar
            # um `selsid` novo, entao requisicoes concorrentes ficavam
            # trocando a identidade do navegador entre si e o acervo do
            # projeto parecia se apagar sozinho. Quem cria sessao e' so a
            # pagina de uma ferramenta, que e' quem tem estado pra guardar.
            #
            # /progress vem primeiro porque precisa responder em paralelo ao
            # POST de upload que ainda esta rodando (ThreadingHTTPServer da
            # uma thread por requisicao).
            if path.endswith("/progress") or path == "/progress":
                self._serve_progress()
                return
            # Tema e estaticos tambem sao de todo mundo. Aceitos com e sem
            # prefixo: a pagina do VB Updater pede "/vb-updater/theme.css",
            # a home pede "/theme.css", e as duas tem que responder.
            if tail == "/theme.css":
                self._serve_theme_css()
                return
            if tail == "/theme" and verb == "do_POST":
                self._serve_theme_choice()
                return
            if tail.startswith("/static/"):
                self._serve_static(tail[len("/static/"):])
                return
            if tail == "/library" and verb == "do_GET":
                # Le a sessao se houver uma; sem cookie responde acervo vazio,
                # que e' a verdade -- quem nao tem sessao nao tem arquivo.
                if sessions is not None:
                    self.session = sessions.peek(cookie)
                self._serve_library()
                return

            # -- daqui pra baixo, a pagina de uma ferramenta -----------------
            if sessions is not None:
                sess, is_new = sessions.resolve(cookie, path)
                self.session = sess
                if is_new:
                    from pacct.web.session import build_cookie
                    self._set_cookie = build_cookie(sess.sid, sessions.ttl)
            for m in prefixed:
                if path == m.prefix:
                    # "/vb-updater" -> "/vb-updater/", pra que caminhos
                    # relativos na pagina resolvam dentro da ferramenta.
                    self._redirect(m.prefix + "/")
                    return
                if path.startswith(m.prefix + "/"):
                    # Tira o prefixo preservando a query string. O handler da
                    # ferramenta ve exatamente o que via antes.
                    self.path = self.path[len(m.prefix):]
                    self.__class__ = m.handler
                    getattr(self, verb)()
                    return
            if root is not None:
                self.__class__ = root.handler
                getattr(self, verb)()
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _strip_prefix(self, path: str) -> str:
            """Caminho sem o prefixo de montagem, pras rotas comuns."""
            for m in prefixed:
                if path == m.prefix:
                    return "/"
                if path.startswith(m.prefix + "/"):
                    return path[len(m.prefix):]
            return path

        def _serve_theme_css(self):
            body = themes.theme_css(self.theme).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            # Sem cache enquanto o sistema de temas ainda esta em iteracao: o
            # CSS e' gerado por Python, entao um reload tem que refletir a
            # edicao sem o usuario limpar cache.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_theme_choice(self):
            """POST /theme {"theme": "..."} -- grava o cookie e responde 204."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
                # `{"theme": "caderno"}` -- um corpo maior que isto nao e' a
                # escolha de um tema, e o Content-Length e' do cliente.
                if n > 4096:
                    raise ValueError("corpo grande demais para /theme")
                payload = json.loads(self.rfile.read(n) or b"{}")
                chosen = themes.normalize(payload.get("theme"), default_theme)
            except Exception:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Set-Cookie", themes.build_cookie(chosen))
            self.send_header("Content-Length", "0")
            self.end_headers()

        # Extensao -> Content-Type. Curto de proposito: aqui so moram as fontes
        # embarcadas, as licencas OFL e o NOTICE que precisam acompanha-las.
        _STATIC_TYPES = {
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".css": "text/css; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".md": "text/plain; charset=utf-8",
            ".svg": "image/svg+xml",
        }

        def _serve_static(self, rel: str):
            from urllib.parse import unquote
            target = (STATIC_DIR / unquote(rel)).resolve()
            # Sandbox igual ao do /download: um ".." no caminho nao pode sair
            # de STATIC_DIR.
            if not is_within(target, (STATIC_DIR,)) or not target.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            ctype = self._STATIC_TYPES.get(target.suffix.lower(),
                                           "application/octet-stream")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if target.suffix.lower() in (".woff2", ".woff"):
                # As fontes nao mudam: nome fixo, conteudo fixo.
                self.send_header("Cache-Control",
                                 "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_library(self):
            """O acervo do visitante, servido em QUALQUER prefixo.

            Fica aqui, e nao numa ferramenta, pelo mesmo motivo de /progress e
            /theme.css: as seis paginas precisam dele, e uma ferramenta nao e'
            dona da lista de arquivos de outra.
            """
            from urllib.parse import parse_qs

            from pacct.web.project_files import library as filelib

            kind = (parse_qs(urlparse(self.path).query).get("kind") or [""])[0]
            files = []
            # `self.session` e' None quando a requisicao chegou sem cookie: o
            # acervo de quem nao tem sessao e' vazio, e inventar uma sessao
            # aqui so pra responder isso e' o que causava a troca de
            # identidade descrita em `_dispatch`.
            if sessions is not None and getattr(self, "session", None) is not None:
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    files = [e.to_json() for e in lib.list(kind or None)]
            body = json.dumps({"files": files}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_progress(self):
            from urllib.parse import parse_qs

            from pacct.web.progress import progress_response
            job = (parse_qs(urlparse(self.path).query).get("job") or [""])[0]
            body = progress_response(job)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._dispatch("do_GET")

        def do_POST(self):
            self._dispatch("do_POST")

    return Dispatcher


def serve(port: int, mounts: list[Mount], logger: logging.Logger,
          sessions=None, default_theme: str = themes.DEFAULT_THEME
          ) -> ThreadingHTTPServer:
    """Sobe o servidor unico e devolve ele (ja servindo, em thread daemon)."""
    import threading

    srv = ThreadingHTTPServer(
        ("0.0.0.0", port),
        make_dispatcher(mounts, sessions, default_theme),
    )
    t = threading.Thread(target=srv.serve_forever, name="toolkit-http", daemon=True)
    t.start()
    base = f"http://localhost:{port}"
    logger.info(f"Toolkit no ar em {base}/")
    for m in mounts:
        if m.prefix:
            logger.info(f"  {m.label:<34} {base}{m.prefix}/")
    return srv
