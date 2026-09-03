"""The three token vocabularies, one per direction.

The NAMES live here and only here; `_TOKENS` carries values, not names. Before
this the same palette was copied across six files, and the copies had drifted.

What paints with these tokens is `shell.py` (everything the three directions
do identically) and each direction's own `DELTA_CSS` (`folha.py`, `regua.py`,
`caderno.py`).
"""

from __future__ import annotations

# The theme for someone who has not chosen (no `seltheme` cookie and no
# [web] theme in config.ini). "caderno" is Caderno de Campo.
DEFAULT_THEME = "caderno"

# slug -> display name. Portuguese and accented: this is what the picker
# shows, and the people using it read Portuguese.
THEMES: dict[str, str] = {
    "folha": "Folha de Dados",
    "regua": "Régua de Bornes",
    "caderno": "Caderno de Campo",
}


# -----------------------------------------------------------------------------
# Tokens
# -----------------------------------------------------------------------------
#
# Every theme defines every name. Where the three directions disagreed, the
# disagreement is resolved here and nowhere else:
#
# * Four border roles, not two. 06 needs a hairline between table rows that is
#   lighter than the panel rule; 03 makes the strong rule out of width rather
#   than colour; 10 makes it out of the text colour. Collapsing to two tokens
#   loses a filete in 06 and the control border in 03.
# * Two accents. 03 uses brass for focus and primary action; the selection mark
#   was wire-blue, which also means "difere VB" -- that collision is undone here
#   by giving 03 brass for both marks.
# * A fourth text level (--text-val) for the value column of the comparator: in
#   03 the gap between --text-2 (#8d8880) and the mockup's #b3ada3 is too large
#   to fold, and that is the densest screen in the product.
# * Cell/control padding are their own tokens: all three mockups put them off
#   the --s scale (6px 10px / 8px 12px / 9px 12px), because density is not the
#   same axis as rhythm.
# * --cond exists in all three. Only 03 has a condensed family; in 06/10 it
#   falls back to --sans and the weight token carries the emphasis.

_TOKENS: dict[str, dict[str, str]] = {

    # -- 06 FOLHA DE DADOS (default) ------------------------------------------
    # The relay manual as a language: light paper, 1 px rule, live corners.
    # The body is the desk; the sheet floats on it.
    "folha": {
        "bg": "#e6e4de",
        "surface": "#fbfaf7",
        "surface-2": "#f2f0ea",
        "surface-3": "#ffffff",
        "viewer-bg": "#e9e6de",
        "bg-image": "none",
        "bg-size": "auto",

        "border-hair": "#e6e3db",
        "border": "#d8d5cd",
        "border-strong": "#9a978e",
        "border-ctl": "#9a978e",

        "text": "#14161a",
        "text-2": "#4b5058",
        "text-3": "#767b83",
        "text-val": "#3f444b",

        "accent": "#2b4a6f",
        "accent-strong": "#14161a",
        "accent-fg": "#fbfaf7",
        "focus": "#2b4a6f",

        "ok": "#1f7a45",
        "equiv": "#a86a00",
        "comment": "#8a7400",
        "displaced": "#20627f",
        "vb": "#6b3fa0",
        "err": "#b3261e",
        "missing": "#767b83",
        "warn": "#a86a00",

        "tint-ok": "rgba(31,122,69,.07)",
        "tint-equiv": "rgba(168,106,0,.07)",
        "tint-comment": "rgba(138,116,0,.05)",
        "tint-displaced": "rgba(32,98,127,.07)",
        "tint-vb": "rgba(107,63,160,.07)",
        "tint-err": "rgba(179,38,30,.07)",
        "tint-missing": "rgba(118,123,131,.08)",
        # Selection and field rules: not a verdict, just interface state.
        "tint-sel": "rgba(43,74,111,.09)",
        "tint-field": "rgba(0,0,0,.02)",

        "s1": "4px", "s2": "8px", "s3": "14px", "s4": "22px", "s5": "34px",
        "pad-main": "16px",
        "pad-cell": "6px 10px",
        "pad-ctl": "6px 13px",
        "pad-field": "6px 10px",

        "sans": "'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif",
        "cond": "'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif",
        "mono": "'IBM Plex Mono',ui-monospace,'SFMono-Regular',Menlo,monospace",
        "fs-1": "9.5px", "fs-2": "11px", "fs-3": "12.5px",
        "fs-4": "14px", "fs-5": "20px",
        "lh": "1.6",
        "track": ".16em",
        "track-wide": ".2em",
        "w-label": "600",
        "w-bold": "600",

        "radius": "0",
        "shadow": "none",
        "shadow-strong": "none",
        "shadow-page": "0 1px 0 var(--border-strong),0 10px 30px rgba(0,0,0,.13)",
        "page-max": "1760px",
        "nav-w": "0px",
    },

    # -- 03 REGUA DE BORNES ---------------------------------------------------
    # Dark, industrial, flat: no radius and no shadow anywhere. Colour is wire
    # colour and always carries meaning.
    "regua": {
        "bg": "#1b1a18",
        "surface": "#232220",
        "surface-2": "#2b2926",
        "surface-3": "#232220",
        # The GLE bed stays light on purpose: it is a technical drawing, not UI.
        "viewer-bg": "#f6f5f1",
        "bg-image": "none",
        "bg-size": "auto",

        "border-hair": "#2b2926",
        "border": "#33312d",
        "border-strong": "#33312d",
        "border-ctl": "#3f3c37",

        "text": "#eae7e0",
        "text-2": "#8d8880",
        "text-3": "#666159",
        "text-val": "#b3ada3",

        # Brass for both marks: wire-blue is "difere VB" and nothing else.
        "accent": "#c9a227",
        "accent-strong": "#c9a227",
        "accent-fg": "#241d05",
        "focus": "#c9a227",

        "ok": "#2f9e58",
        "equiv": "#e0a91b",
        "comment": "#b8b04a",
        "displaced": "#8b6ad9",
        "vb": "#2f6fed",
        "err": "#d9433f",
        "missing": "#8d8880",
        "warn": "#e0a91b",

        "tint-ok": "rgba(47,158,88,.14)",
        "tint-equiv": "rgba(224,169,27,.12)",
        "tint-comment": "rgba(184,176,74,.08)",
        "tint-displaced": "rgba(139,106,217,.13)",
        "tint-vb": "rgba(47,111,237,.13)",
        "tint-err": "rgba(217,67,63,.13)",
        "tint-missing": "rgba(141,136,128,.10)",
        "tint-sel": "rgba(201,162,39,.14)",
        "tint-field": "rgba(255,255,255,.02)",

        "s1": "4px", "s2": "8px", "s3": "12px", "s4": "18px", "s5": "26px",
        "pad-main": "18px",
        "pad-cell": "8px 12px",
        "pad-ctl": "8px 15px",
        "pad-field": "7px 11px",

        "sans": "'Roboto',system-ui,-apple-system,'Segoe UI',sans-serif",
        "cond": "'Roboto Condensed','Arial Narrow',system-ui,sans-serif",
        "mono": "ui-monospace,'SFMono-Regular',Menlo,'Courier New',monospace",
        "fs-1": "10px", "fs-2": "11px", "fs-3": "12.5px",
        "fs-4": "14px", "fs-5": "18px",
        "lh": "1.55",
        "track": ".14em",
        "track-wide": ".2em",
        "w-label": "700",
        "w-bold": "600",

        "radius": "0",
        "shadow": "none",
        "shadow-strong": "none",
        "shadow-page": "none",
        "page-max": "none",
        "nav-w": "236px",
    },

    # -- 10 CADERNO DE CAMPO --------------------------------------------------
    # 5 mm graph paper, cards clipped on top, hard offset shadows. There is no
    # chromatic accent: focus, active and primary are all ink.
    "caderno": {
        "bg": "#f4f6f2",
        "surface": "#ffffff",
        "surface-2": "#f7f9f5",
        "surface-3": "#ffffff",
        "viewer-bg": "#ffffff",
        # The graph paper itself: two 5 mm rules plus two 25 mm ones.
        "bg-image": (
            "linear-gradient(#dfe8dc 1px,transparent 1px),"
            "linear-gradient(90deg,#dfe8dc 1px,transparent 1px),"
            "linear-gradient(#c7d8c3 1px,transparent 1px),"
            "linear-gradient(90deg,#c7d8c3 1px,transparent 1px)"
        ),
        "bg-size": "5mm 5mm,5mm 5mm,25mm 25mm,25mm 25mm",

        "border-hair": "#e2e8e0",
        "border": "#c9d2c7",
        "border-strong": "#1b2a3a",
        "border-ctl": "#c9d2c7",

        "text": "#1b2a3a",
        "text-2": "#6b7280",
        "text-3": "#9aa3ad",
        "text-val": "#3d4a58",

        "accent": "#1b2a3a",
        "accent-strong": "#1b2a3a",
        "accent-fg": "#f4f6f2",
        "focus": "#1b2a3a",

        "ok": "#2f7d4f",
        "equiv": "#b8791b",
        "comment": "#94861f",
        "displaced": "#1f5f8b",
        "vb": "#6d4bb5",
        "err": "#c2352d",
        "missing": "#6b7280",
        "warn": "#b8791b",

        "tint-ok": "rgba(47,125,79,.07)",
        "tint-equiv": "rgba(184,121,27,.07)",
        "tint-comment": "rgba(148,134,31,.05)",
        "tint-displaced": "rgba(31,95,139,.07)",
        "tint-vb": "rgba(109,75,181,.07)",
        "tint-err": "rgba(194,53,45,.07)",
        "tint-missing": "rgba(107,114,128,.08)",
        "tint-sel": "rgba(27,42,58,.07)",
        "tint-field": "rgba(0,0,0,.02)",

        "s1": "4px", "s2": "8px", "s3": "12px", "s4": "20px", "s5": "30px",
        "pad-main": "16px",
        "pad-cell": "9px 12px",
        "pad-ctl": "7px 14px",
        "pad-field": "7px 11px",

        "sans": "'Public Sans',system-ui,-apple-system,'Segoe UI',sans-serif",
        "cond": "'Public Sans',system-ui,-apple-system,'Segoe UI',sans-serif",
        "mono": "'Courier Prime',ui-monospace,'Courier New',monospace",
        "fs-1": "10px", "fs-2": "11.5px", "fs-3": "13px",
        "fs-4": "14.5px", "fs-5": "25px",
        "lh": "1.6",
        "track": ".16em",
        "track-wide": ".2em",
        "w-label": "700",
        "w-bold": "700",

        "radius": "0",
        "shadow": "1px 2px 0 rgba(27,42,58,.10)",
        "shadow-strong": "2px 4px 0 rgba(27,42,58,.16)",
        "shadow-page": "none",
        "page-max": "1760px",
        "nav-w": "0px",
    },
}


# -----------------------------------------------------------------------------
# The vocabulary, written once
# -----------------------------------------------------------------------------
#
# The token NAMES live here and only here -- `_TOKENS` above carries values, not
# names. That is the whole point of the module: before this, the same palette
# was copied into six files and had already drifted. A theme missing a name
# raises KeyError on the first render, so the three can never diverge silently.

_TOKEN_CSS = """\
  /* superficie */
  --bg: %(bg)s;
  --surface: %(surface)s;
  --surface-2: %(surface-2)s;
  --surface-3: %(surface-3)s;
  --viewer-bg: %(viewer-bg)s;
  --bg-image: %(bg-image)s;
  --bg-size: %(bg-size)s;
  /* borda: quatro papeis, porque as tres direcoes colapsam pares diferentes */
  --border-hair: %(border-hair)s;
  --border: %(border)s;
  --border-strong: %(border-strong)s;
  --border-ctl: %(border-ctl)s;
  /* texto */
  --text: %(text)s;
  --text-2: %(text-2)s;
  --text-3: %(text-3)s;
  --text-val: %(text-val)s;
  /* marca */
  --accent: %(accent)s;
  --accent-strong: %(accent-strong)s;
  --accent-fg: %(accent-fg)s;
  --focus: %(focus)s;
  /* significado: os sete vereditos do comparador, mais o aviso generico */
  --ok: %(ok)s;
  --equiv: %(equiv)s;
  --comment: %(comment)s;
  --displaced: %(displaced)s;
  --vb: %(vb)s;
  --err: %(err)s;
  --missing: %(missing)s;
  --warn: %(warn)s;
  /* tinta de linha: 10%% de um tom saturado vira lavagem sobre papel, entao o
     alfa e' calibrado por tema */
  --tint-ok: %(tint-ok)s;
  --tint-equiv: %(tint-equiv)s;
  --tint-comment: %(tint-comment)s;
  --tint-displaced: %(tint-displaced)s;
  --tint-vb: %(tint-vb)s;
  --tint-err: %(tint-err)s;
  --tint-missing: %(tint-missing)s;
  --tint-sel: %(tint-sel)s;
  --tint-field: %(tint-field)s;
  /* espaco: uma escala so' -- mais densidade, que nao e' o mesmo eixo */
  --s1: %(s1)s;
  --s2: %(s2)s;
  --s3: %(s3)s;
  --s4: %(s4)s;
  --s5: %(s5)s;
  --pad-cell: %(pad-cell)s;
  --pad-ctl: %(pad-ctl)s;
  --pad-field: %(pad-field)s;
  /* recuo lateral do painel principal: e' o que abre (ou fecha) a folha */
  --pad-main: %(pad-main)s;
  /* tipo: sempre com pilha de sistema atras da familia embarcada */
  --sans: %(sans)s;
  --cond: %(cond)s;
  --mono: %(mono)s;
  --fs-1: %(fs-1)s;
  --fs-2: %(fs-2)s;
  --fs-3: %(fs-3)s;
  --fs-4: %(fs-4)s;
  --fs-5: %(fs-5)s;
  --lh: %(lh)s;
  --track: %(track)s;
  --track-wide: %(track-wide)s;
  --w-label: %(w-label)s;
  --w-bold: %(w-bold)s;
  /* forma */
  --radius: %(radius)s;
  --shadow: %(shadow)s;
  --shadow-strong: %(shadow-strong)s;
  --shadow-page: %(shadow-page)s;
  --page-max: %(page-max)s;
  --nav-w: %(nav-w)s;
"""


# All three themes fill exactly the same vocabulary. The check runs at import
# on purpose: a missing name has to break at start-up, not on some particular
# screen nobody has opened yet.
_NAMES = set(_TOKENS[DEFAULT_THEME])
for _slug, _vals in _TOKENS.items():
    _missing = _NAMES - set(_vals)
    _extra = set(_vals) - _NAMES
    if _missing or _extra:
        raise RuntimeError(
            f"tema {_slug!r} fora do vocabulario: "
            f"falta {sorted(_missing)}, sobra {sorted(_extra)}")

def token_css(theme: str) -> str:
    """One theme's token block.

    Also emitted on a bare `:root`, with no theme selector: `data-theme` on
    `<html>` is written by the dispatcher, and a page served before it (or
    saved to disk) still has to open with the right palette.
    """
    return f":root,\n:root[data-theme={theme}] {{\n{_TOKEN_CSS % _TOKENS[theme]}}}\n"
