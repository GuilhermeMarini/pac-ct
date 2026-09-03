"""Os três temas do toolkit: tokens, casca compartilhada e marcação por direção.

Antes disto cada ferramenta servia o proprio `<style>` com a propria copia da
paleta, e as copias ja tinham derivado. O `theme.py` juntou a paleta num lugar
so -- e foi longe demais: juntou tambem a *marcacao*, e as nove telas passaram
a ser a marcacao da folha pintada de tres jeitos. O menu virou tabela nos tres
temas porque a tabela e' o que o mockup da folha pede; as fichas com clipe do
caderno e os bornes da regua nao tinham onde existir.

Aqui cada direcao e' dona das duas coisas, no mesmo arquivo:

    folha.py    Folha de Dados   -- sumario `.toc` + tabela de referencia
    regua.py    Régua de Bornes  -- régua vertical `.strip`/`.borne` + fichas de fio
    caderno.py  Caderno de Campo -- divisorias `.tabs`/`.tab` + fichas com clipe

`shell.py` guarda so o que os tres mockups escrevem igual, e `tokens.py` guarda
o vocabulario -- um nome faltando quebra no boot, entao os tres nao podem
divergir em silencio.

A escolha do visitante mora no cookie `seltheme`, nao no estado de sessao: o
GLV e' compartilhado de proposito (um rele fisico, um telnet), e mesmo la cada
visitante mantem o proprio tema.
"""

from __future__ import annotations

import re
from http.cookies import SimpleCookie

from pacct.paths import STATIC_DIR
from pacct.web.themes import caderno, folha, regua, shell
from pacct.web.themes.items import MENU_ITEM, NOTES, TOOLS  # noqa: F401
from pacct.web.themes.tokens import DEFAULT_THEME, THEMES, token_css

COOKIE_NAME = "seltheme"

# Um tema e' preferencia, nao sessao: tem que sobreviver ao TTL de 8 h.
COOKIE_MAX_AGE = 365 * 24 * 3600

# slug -> o modulo que sabe pintar e emitir aquela direcao.
_DIRECOES = {"folha": folha, "regua": regua, "caderno": caderno}


# -----------------------------------------------------------------------------
# Fontes embutidas
# -----------------------------------------------------------------------------
#
# Os nove .woff2 acompanham o projeto: a interface tem que abrir com a
# tipografia certa num notebook de subestacao sem internet. Nunca um CDN.
# `fonts.css` declara os @font-face com url *relativa* (pra poder ser servido do
# mesmo diretorio dos arquivos); dobrado no /theme.css ela vira absoluta, que o
# dispatcher responde de qualquer prefixo de montagem.

_FONT_URL_RE = re.compile(r"url\((['\"]?)([^'\")]+)\1\)")


def _font_faces() -> str:
    """O bloco @font-face do projeto, com as urls reescritas p/ /static/fonts/."""
    src = STATIC_DIR / "fonts" / "fonts.css"
    try:
        css = src.read_text(encoding="utf-8")
    except OSError:
        # Fonte faltando nunca pode derrubar a interface: todo token guarda uma
        # pilha de sistema atras da familia embutida.
        return "/* fonts.css ausente -- caindo na pilha de sistema */\n"
    return _FONT_URL_RE.sub(
        lambda m: f"url('/static/fonts/{m.group(2).lstrip('./')}')", css,
    )


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------

def normalize(theme: str | None, fallback: str = DEFAULT_THEME) -> str:
    """Reduz qualquer coisa a um slug de tema conhecido."""
    if theme and theme in THEMES:
        return theme
    return fallback if fallback in THEMES else DEFAULT_THEME


def theme_css(theme: str = DEFAULT_THEME) -> str:
    """A folha de estilo inteira de um tema: fontes, tokens, casca e direcao."""
    theme = normalize(theme)
    return (
        f"/* PAC CT -- tema \"{THEMES[theme]}\" ({theme}).\n"
        f"   Gerado por pacct/web/themes/; nao edite CSS nas ferramentas. */\n"
        f"{_font_faces()}\n"
        f"{token_css(theme)}"
        f"{shell.SHELL}\n{_DIRECOES[theme].DELTA_CSS}"
    )


def nav_html(theme: str = DEFAULT_THEME, active: str = "") -> str:
    """A navegacao da direcao pedida, com `active` marcado como tela atual.

    Cada direcao emite a propria estrutura: `.toc` na folha, `.strip`/`.borne`
    na regua, `.tabs`/`.tab` no caderno. Nao existe uma "nav" comum -- foi
    justamente ela que padronizou as telas.
    """
    return _DIRECOES[normalize(theme)].nav(active)


def home_html(theme: str = DEFAULT_THEME) -> str:
    """O corpo do menu na direcao pedida: tabela, bornes ou fichas com clipe."""
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
    """Set-Cookie da escolha de tema, na mesma forma do `selsid`.

    Sem `HttpOnly`: diferente do id de sessao nao ha o que proteger aqui, e uma
    pagina pode querer ler o proprio tema. Sem `Secure`: o toolkit roda em HTTP
    na LAN da subestacao.
    """
    return (
        f"{COOKIE_NAME}={normalize(theme)}; Path=/; SameSite=Lax; "
        f"Max-Age={COOKIE_MAX_AGE}"
    )
