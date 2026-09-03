"""A faixa de abas do GLV quebra em linhas; nao rola de lado.

CSS nao tem teste de unidade nesta suite -- as ferramentas web se verificam no
navegador (ver docs/ENGINEERING-NOTES.md). O que se pinha aqui e' so o que volta em silencio:
`overflow-x: auto` numa faixa de uma linha nao quebra nada, nao aparece em
nenhum teste, e some da tela exatamente quando o teto de 10 diagramas e'
usado. Medido no navegador com 10 abas: 2 linhas a 1908 px, 4 a 900 px, 10 a
420 px, e `scrollWidth == clientWidth` nos tres.
"""
from __future__ import annotations

import re

from pacct.web import glv


def _tabs_rule() -> str:
    css = glv.load_template("dashboard.html")
    m = re.search(r"#tabs \{(.*?)\}", css, re.S)
    assert m, "o seletor #tabs sumiu do dashboard"
    return m.group(1)


def test_the_tab_strip_wraps():
    assert "flex-wrap: wrap" in _tabs_rule()


def test_the_tab_strip_does_not_scroll_sideways():
    """O que estava aqui antes. Voltar a rolar esconde as abas seguintes atras
    de uma barra que ninguem procura."""
    assert "overflow-x" not in _tabs_rule()


def test_each_tab_stays_on_one_line():
    """O `nowrap` saiu da FAIXA e foi pro ITEM: a faixa quebra entre abas, e
    nunca dentro do nome de uma."""
    css = glv.load_template("dashboard.html")
    assert "#tabs .tab, #tabs .tab-new { white-space: nowrap; }" in css
    assert "white-space: nowrap" not in _tabs_rule()


def test_a_long_relay_name_cannot_widen_the_strip_past_the_viewport():
    """Quebrar entre abas nao salva de UMA aba mais larga que a tela -- um item
    de flex nao quebra dentro de si. O nome e' cortado com reticencias, e o
    texto inteiro fica no `title` da aba (`renderTabs`), entao nada se perde."""
    css = glv.load_template("dashboard.html")
    m = re.search(r"#tabs \.tab \.label \{(.*?)\}", css, re.S)
    assert m, "o corte do nome da aba sumiu"
    rule = m.group(1)
    assert "max-width" in rule
    assert "text-overflow: ellipsis" in rule
    # E o nome tem que CARREGAR a classe que a regra pinta.
    assert "name.className = 'label';" in css
