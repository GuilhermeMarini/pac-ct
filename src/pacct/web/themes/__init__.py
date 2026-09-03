"""The toolkit's three themes: tokens, a shared shell, and markup per direction.

Before this, each tool served its own `<style>` with its own copy of the
palette, and the copies had already drifted. Pulling the palette into one
place fixed that -- and then went too far: it pulled in the *markup* as well,
and the nine screens became one direction's markup painted three ways. The
menu became a table in all three themes because a table is what the Folha
mockup asks for; Caderno's clipped index cards and Régua's terminal blocks had
nowhere to exist.

Here each direction owns both, in one file:

    folha.py    Folha de Dados   -- a `.toc` summary plus a reference table
    regua.py    Régua de Bornes  -- a vertical `.strip`/`.borne` rail with wire cards
    caderno.py  Caderno de Campo -- `.tabs`/`.tab` dividers plus clipped cards

`shell.py` holds only what all three mockups write identically, and
`tokens.py` holds the vocabulary -- a missing name breaks at import, so the
three cannot diverge in silence.

The visitor's choice lives in the `seltheme` cookie, not in session state: the
GLV is shared on purpose (one physical relay, one telnet), and even there each
visitor keeps their own theme.
"""

from __future__ import annotations

import re
from http.cookies import SimpleCookie

from pacct.paths import STATIC_DIR
from pacct.web.themes import caderno, folha, regua, shell
from pacct.web.themes.items import MENU_ITEM, NOTES, TOOLS  # noqa: F401
from pacct.web.themes.tokens import DEFAULT_THEME, THEMES, token_css

COOKIE_NAME = "seltheme"

# A theme is a preference, not session state: it has to outlive the 8 h TTL.
COOKIE_MAX_AGE = 365 * 24 * 3600

# slug -> o modulo que sabe pintar e emitir aquela direcao.
_DIRECOES = {"folha": folha, "regua": regua, "caderno": caderno}


# -----------------------------------------------------------------------------
# The embedded fonts
# -----------------------------------------------------------------------------
#
# The nine .woff2 files ship with the project: the interface has to open with
# the right typography on a substation laptop with no internet. Never a CDN.
# `fonts.css` declares its @font-face rules with *relative* urls, so it can be
# served from the same directory as the files; folded into /theme.css they
# become absolute, which the dispatcher answers under any mount prefix.

_FONT_URL_RE = re.compile(r"url\((['\"]?)([^'\")]+)\1\)")


def _font_faces() -> str:
    """The project's @font-face block, with its urls rewritten to /static/fonts/."""
    src = STATIC_DIR / "fonts" / "fonts.css"
    try:
        css = src.read_text(encoding="utf-8")
    except OSError:
        # A missing font must never take the interface down: every token
        # keeps a system stack behind the embedded family.
        return "/* fonts.css ausente -- caindo na pilha de sistema */\n"
    return _FONT_URL_RE.sub(
        lambda m: f"url('/static/fonts/{m.group(2).lstrip('./')}')", css,
    )


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------

def normalize(theme: str | None, fallback: str = DEFAULT_THEME) -> str:
    """Reduce anything to a known theme slug."""
    if theme and theme in THEMES:
        return theme
    return fallback if fallback in THEMES else DEFAULT_THEME


def theme_css(theme: str = DEFAULT_THEME) -> str:
    """One theme's whole stylesheet: fonts, tokens, shell and direction."""
    theme = normalize(theme)
    return (
        f"/* PAC CT -- tema \"{THEMES[theme]}\" ({theme}).\n"
        f"   Gerado por pacct/web/themes/; nao edite CSS nas ferramentas. */\n"
        f"{_font_faces()}\n"
        f"{token_css(theme)}"
        f"{shell.SHELL}\n{_DIRECOES[theme].DELTA_CSS}"
    )


def nav_html(theme: str = DEFAULT_THEME, active: str = "") -> str:
    """The requested direction's navigation, with `active` marked as the current
    screen.

    Each direction emits its own structure: `.toc` in Folha, `.strip`/`.borne`
    in Régua, `.tabs`/`.tab` in Caderno. There is no common "nav" -- that is
    precisely what had flattened the screens into one another.
    """
    return _DIRECOES[normalize(theme)].nav(active)


def home_html(theme: str = DEFAULT_THEME) -> str:
    """The menu body in the requested direction: a table, terminal blocks, or
    clipped cards."""
    return _DIRECOES[normalize(theme)].home()


def resolve(cookie_header: str | None, default: str = DEFAULT_THEME) -> str:
    """O tema do visitante a partir do cabecalho Cookie da requisicao."""
    if not cookie_header:
        return normalize(None, default)
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
    except Exception:
        return normalize(None, default)
    morsel = jar.get(COOKIE_NAME)
    return normalize(morsel.value if morsel else None, default)


def build_cookie(theme: str) -> str:
    """The Set-Cookie for a theme choice, shaped like the `selsid` one.

    No `HttpOnly`: unlike a session id there is nothing to protect here, and a
    page may want to read its own theme. No `Secure`: the toolkit runs over
    HTTP on the substation's LAN.
    """
    return (
        f"{COOKIE_NAME}={normalize(theme)}; Path=/; SameSite=Lax; "
        f"Max-Age={COOKIE_MAX_AGE}"
    )
