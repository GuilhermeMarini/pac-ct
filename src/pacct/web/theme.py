"""Reexport do pacote `pacct.web.themes`.

O tema deixou de ser um arquivo e virou um pacote: cada direcao passou a ser
dona do proprio CSS **e** da propria marcacao, porque os tres mockups nao
concordam na estrutura das telas -- so no menu eles compartilham 7 classes de
53. Ver `pacct/web/themes/__init__.py`.

Este modulo sobrevive so pra nao quebrar `from pacct.web import theme as
themes` (`pacct/web/mount.py`) e os imports pontuais das ferramentas. Codigo
novo deve importar `pacct.web.themes` direto.
"""

from __future__ import annotations

from pacct.web.themes import (  # noqa: F401
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    DEFAULT_THEME,
    THEMES,
    build_cookie,
    home_html,
    nav_html,
    normalize,
    resolve,
    theme_css,
)

__all__ = [
    "COOKIE_MAX_AGE", "COOKIE_NAME", "DEFAULT_THEME", "THEMES",
    "build_cookie", "home_html", "nav_html", "normalize", "resolve",
    "theme_css",
]
