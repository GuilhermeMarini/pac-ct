"""Mount several tools on a single HTTP server, routed by path prefix.

Each tool used to bring up its own `ThreadingHTTPServer` on the same port, and
`main()` ran one at a time: opening the VB Updater meant taking the home's
server down, and the other way round. Here one server stays up on `:8765` and
routes by prefix:

    /                       -> home (menu)
    /vb-updater/...         -> VB Updater
    /vlan-mapper/...        -> VLAN Mapper
    /gle-exporter/...       -> GLE Variable Comment Exporter
    /settings-compare/...   -> Settings Compare
    /glv/...                -> Graphical Logic Viewer

So several tools are usable at once, each in its own browser tab, and only
one port has to be open in the firewall or the WSL portproxy.

Two pieces make that work:

1. `make_dispatcher()` -- the server side. Each tool still defines its own
   `BaseHTTPRequestHandler` with absolute routes (`/state`, `/download`, ...).
   The dispatcher strips the prefix from `self.path` and swaps
   `self.__class__` for the tool's class, so the original handler runs without
   ever seeing the prefix -- not one route had to be rewritten.

2. `inject_head()` -- the client side. The JavaScript embedded in the pages
   uses absolute paths (`fetch('/state')`), which under a prefix would hit
   `/state` and 404. A shim injected into the `<head>` rewrites `fetch` and
   `XMLHttpRequest.open` to prefix absolute paths, so the existing JavaScript
   stayed intact too. The same injection adds the theme stylesheet
   (`/theme.css`) and stamps `data-theme` on `<html>` for EVERY screen --
   which is why it is no longer just the shim, and runs on the home (empty
   prefix) as well. The theme PICKER, though, is mounted only on the home: the
   theme is a one-year cookie, chosen once, in the menu.

The dispatcher also serves four routes common to every tool, before matching
any prefix: `/progress`, `/theme.css`, `/static/...` (the .woff2 fonts that
ship with the project) and `/library` (the visitor's project files, which each
tool's picker reads). None of them belongs to a single tool.
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

# Rewrites absolute paths ("/state") to "<prefix>/state" in `fetch` and
# `XMLHttpRequest`. Leaves alone: absolute URLs with a scheme (http://...),
# protocol-relative paths ("//host/x"), and relative paths.
#
# A bare `/` is preserved on purpose: that is the "<- Menu" link, which must
# go to the real home rather than to the tool's own root.
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

# The picker is mounted by JavaScript because the home has a header of its
# own: rather than editing the markup, it hangs itself off the page's
# `<header>` (or `.masthead`). With no header it falls back to a fixed corner,
# below the progress bar that occupies the top of the viewport.
#
# It goes on the HOME only (empty prefix). It used to go on all nine screens,
# and in an application header -- the GLV's, which already carries a title, a
# state, a search box, tools and a zoom -- it became one more group fighting
# for a band that already wrapped onto several lines. The theme is a one-year
# cookie: chosen once, in the menu, and it follows the visitor everywhere.
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


# Markers the direction resolves. They sit in the screens' HTML instead of
# finished markup because the three directions do NOT emit the same structure:
# the navigation is `.toc` in Folha, `.strip`/`.borne` in Régua and
# `.tabs`/`.tab` in Caderno, and the menu is a table, a terminal block or a
# clipped card. This used to be resolved at each tool's import, which froze
# Folha's markup into all three themes.
_NAV_RE = re.compile(r"<!--NAV:([a-z0-9-]*)-->")
_HOME_RE = re.compile(r"<!--HOME-->")


def _resolve_markup(html: str, theme: str) -> str:
    """Replace the screen markers with the active direction's markup."""
    html = _NAV_RE.sub(lambda m: themes.nav_html(theme, m.group(1)), html)
    return _HOME_RE.sub(lambda _m: themes.home_html(theme), html)


def inject_head(html: str, prefix: str, theme: str = themes.DEFAULT_THEME) -> str:
    """Inject the prefix shim, the theme stylesheet and the theme picker into
    `<head>`.

    This used to be the shim alone, which is why it did not run on the home
    (empty prefix). Now it always runs: the home needs a theme too.

    The PICKER, though, goes only on the home. The stylesheet and the
    `data-theme` go on every screen -- what no longer travels is the group of
    switch buttons.

    The stylesheet lands immediately after `<head>`, BEFORE the tool's own
    `<style>`, so the tokens already exist when the tool uses them and
    anything not yet converted still wins by cascade order.
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


# The old name, from when this was only the prefix shim. Kept so an outside
# import does not break.
inject_prefix_shim = inject_head


# -----------------------------------------------------------------------------
# Dispatcher
# -----------------------------------------------------------------------------

class Mount:
    """Uma ferramenta montada: prefixo + classe de handler + rotulo pro log."""

    __slots__ = ("prefix", "handler", "label")

    def __init__(self, prefix: str, handler: type, label: str):
        # Normalised to "/name" with no trailing slash; the root becomes "".
        prefix = "/" + prefix.strip("/")
        self.prefix = "" if prefix == "/" else prefix
        self.handler = handler
        self.label = label
        # The handler uses this to build absolute URLs the client navigates
        # to directly (download_url), which the `fetch` shim cannot reach.
        handler.mount_prefix = self.prefix


def make_dispatcher(mounts: list[Mount], sessions=None,
                    default_theme: str = themes.DEFAULT_THEME) -> type:
    """Build the handler that routes by prefix between the mounted tools.

    The mount at the root (empty prefix) is the fallback: any path matching no
    prefix lands there.

    `sessions` is a `SessionManager`, or None. When present, the dispatcher
    resolves the visitor's session BEFORE delegating and leaves `session` and
    `_set_cookie` on the instance -- which is where handlers inheriting from
    `SessionHandler` read them.

    `default_theme` is the theme for someone who has not chosen ([web] theme
    in config.ini). The visitor's theme comes from the `seltheme` cookie and
    also lands on the instance, as `self.theme`, before delegation.
    """
    default_theme = themes.normalize(default_theme)
    prefixed = [m for m in mounts if m.prefix]
    root = next((m for m in mounts if not m.prefix), None)

    class Dispatcher(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silence stderr; each tool logs what matters

        def end_headers(self):
            """Emit the new session's `Set-Cookie`, exactly as `SessionHandler` does.

            It has to be here because of the "/vb-updater" -> "/vb-updater/"
            redirect: that one is answered by the dispatcher itself, AFTER
            `resolve()` has created the session, and without swapping
            `self.__class__` -- so it fell through to
            `BaseHTTPRequestHandler.end_headers`, which knows nothing about
            cookies, and the visitor reached the tool still without an
            identity.

            The infrastructure routes do not depend on this: they create no
            session at all (see `_dispatch`). Once `self.__class__` has been
            swapped for the tool's, `SessionHandler.end_headers` is what
            answers; the two never run on the same response.
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

            # -- infrastructure routes ---------------------------------------
            # They are served BEFORE the session is resolved, and on purpose:
            # they own no identity at all. A stylesheet, a .woff2 or a
            # progress-bar poll arriving without a cookie cannot invent a
            # visitor -- that filled the server with phantom sessions and,
            # worse, each of those responses started handing out a fresh
            # `selsid`, so concurrent requests traded the browser's identity
            # between them and the project's file list looked like it was
            # erasing itself. Only a tool's own page creates a session, which
            # is the one that has state to keep.
            #
            # /progress comes first because it has to answer in parallel with
            # the upload POST that is still running (ThreadingHTTPServer gives
            # one thread per request).
            if path.endswith("/progress") or path == "/progress":
                self._serve_progress()
                return
            # Theme and statics belong to everyone too. Accepted with and
            # without a prefix: the VB Updater's page asks for
            # "/vb-updater/theme.css", the home asks for "/theme.css", and
            # both have to answer.
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
                # Reads the session if there is one; with no cookie it
                # answers an empty library, which is the truth -- no session,
                # no files.
                if sessions is not None:
                    self.session = sessions.peek(cookie)
                self._serve_library()
                return

            # -- from here down, a tool's page -------------------------------
            if sessions is not None:
                sess, is_new = sessions.resolve(cookie, path)
                self.session = sess
                if is_new:
                    from pacct.web.session import build_cookie
                    self._set_cookie = build_cookie(sess.sid, sessions.ttl)
            for m in prefixed:
                if path == m.prefix:
                    # "/vb-updater" -> "/vb-updater/", so that relative
                    # paths in the page resolve inside the tool.
                    self._redirect(m.prefix + "/")
                    return
                if path.startswith(m.prefix + "/"):
                    # Strips the prefix, preserving the query string. The
                    # tool's handler sees exactly what it saw before.
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
            """Path without the mount prefix, for the common routes."""
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
            # No cache while the theme system is still being iterated on:
            # the CSS is generated by Python, so a reload has to reflect the
            # edit without the user clearing their cache.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_theme_choice(self):
            """POST /theme {"theme": "..."} -- sets the cookie, answers 204."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
                # `{"theme": "caderno"}` -- a body bigger than this is not a
                # theme choice, and the Content-Length comes from the client.
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

        # Extension -> Content-Type. Deliberately short: only the embedded
        # fonts, the OFL licences and the NOTICE that has to travel with them
        # live here.
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
            # Same sandbox as /download: a ".." in the path must not escape
            # STATIC_DIR.
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
                # The fonts do not change: fixed name, fixed content.
                self.send_header("Cache-Control",
                                 "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_library(self):
            """The visitor's library, served at ANY prefix.

            It lives here, and not in a tool, for the same reason as /progress
            and /theme.css: the six pages need it, and one tool does not own
            another's file list.
            """
            from urllib.parse import parse_qs

            from pacct.web.project_files import library as filelib

            kind = (parse_qs(urlparse(self.path).query).get("kind") or [""])[0]
            files = []
            # `self.session` is None when the request arrived with no
            # cookie: the library of someone with no session is empty, and
            # inventing a session here just to answer that is what caused the
            # identity swap described in `_dispatch`.
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
    """Start the single server and return it (serving, in a daemon thread)."""
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
