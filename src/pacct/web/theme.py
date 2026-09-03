"""A re-export of the `pacct.web.themes` package.

The theme stopped being one file and became a package: each direction owns its
own CSS **and** its own markup, because the three mockups do not agree on the
structure of the screens -- on the home they share 7 classes out of 53. See
`pacct/web/themes/__init__.py`.

This module survives only so `from pacct.web import theme as themes` keeps
working in `mount.py` and in the tools' occasional imports. New code should
import `pacct.web.themes` directly.
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
