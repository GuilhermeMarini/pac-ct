"""The Arquivos do Projeto tab must exist, and be reachable, in all three
directions -- and it must not disturb the tools' numbering."""

from __future__ import annotations

import re

import pytest

from pacct.web import themes
from pacct.web.themes import items

ALL = list(themes.THEMES)


@pytest.mark.parametrize("theme", ALL)
def test_every_direction_links_the_files_tab(theme):
    html = themes.nav_html(theme, "")
    assert 'href="/files/"' in html


@pytest.mark.parametrize("theme", ALL)
def test_the_files_tab_marks_itself_as_the_current_screen(theme):
    html = themes.nav_html(theme, "files")
    assert 'aria-current="page"' in html
    # and exactly one screen claims it
    assert html.count('aria-current="page"') == 1


@pytest.mark.parametrize("theme", ALL)
def test_the_first_tool_is_the_vendor_neutral_one(theme):
    """régua's home cards say "Borne i" and its strip has to keep matching.
    Com os grupos, a ferramenta 1 passou a ser o VLAN Mapper: e' a unica que
    nao pede nada de fabricante nenhum, e o menu abre pelo generico."""
    html = themes.nav_html(theme, "vlan-mapper")
    assert ">1<" in html or ">01<" in html


@pytest.mark.parametrize("theme", ALL)
def test_the_files_tab_is_not_numbered_with_the_tools(theme):
    html = themes.nav_html(theme, "files")
    assert ">A<" in html


def test_the_files_tab_is_not_in_the_tool_catalogue():
    """It is the input surface, not a commissioning tool: it must not inflate
    the tool count or claim an `entrada` column."""
    assert all(t.key != "files" for t in items.TOOLS)
    assert items.FILES_ITEM[0] == "files"
    assert items.FILES_ITEM[1] == "/files/"


@pytest.mark.parametrize("theme", ALL)
def test_the_home_points_at_the_files_tab(theme):
    assert "/files/" in themes.home_html(theme)


def test_every_tool_declares_a_known_group():
    keys = {g.key for g in items.GROUPS}
    for t in items.TOOLS:
        assert t.group in keys, f"{t.key} declara um grupo desconhecido: {t.group}"


def test_the_catalogue_is_sorted_by_group():
    """O ordinal 1..9 sai da posicao em TOOLS. Se a lista nao estiver na ordem
    dos grupos, a home imprime um numero e a tira imprime outro."""
    positions = [items.GROUP_ORDER.index(t.group) for t in items.TOOLS]
    assert positions == sorted(positions)


def test_the_numbering_the_screens_promise():
    assert items.ORDINAL["vlan-mapper"] == 1
    assert items.ORDINAL["relatorio"] == 2
    assert items.ORDINAL["glv"] == 3
    assert items.ORDINAL["validador"] == 9


def test_the_two_empty_groups_are_declared_with_a_roadmap():
    """Secao vazia e muda e' vapor; com uma linha do que vai cair ali e'
    previsao. E' a decisao do design, entao e' teste."""
    for key in ("ge", "siemens"):
        g = next(g for g in items.GROUPS if g.key == key)
        assert not items.tools_of(key)
        assert g.empty.strip()
        assert g.eats.strip()


def test_caderno_labels_each_group_in_the_strip():
    html = themes.nav_html("caderno", "")
    for g in items.GROUPS:
        assert f'<span class="tabsep">{g.short}</span>' in html


def test_caderno_gives_an_empty_group_a_blank_sheet():
    html = themes.home_html("caderno")
    assert 'class="blank"' in html
    for g in items.GROUPS:
        if not items.tools_of(g.key):
            assert g.empty in html


def test_regua_captions_every_group_and_the_entrance():
    html = themes.nav_html("regua", "")
    assert "Régua X0 &mdash; entrada" in html
    for i, g in enumerate(items.GROUPS, start=1):
        assert f"Régua X{i} &mdash; {g.short}" in html


def test_regua_strip_and_cards_agree_on_the_number():
    """As fichas dizem "Borne i" e a tira imprime i. Sao dois renderizadores;
    o gotcha do docs/ENGINEERING-NOTES.md vira teste aqui."""
    nav = themes.nav_html("regua", "")
    home = themes.home_html("regua")
    for t in items.TOOLS:
        i = items.ORDINAL[t.key]
        assert f'<span class="num">{i}</span>' in nav
        m = re.search(re.escape(f"<h3>{t.name}</h3>") + r".{0,600}?Borne (\d+)",
                      home, re.S)
        assert m, f"ficha de {t.name} sem Borne"
        assert int(m.group(1)) == i, f"{t.name}: tira {i}, ficha {m.group(1)}"


def test_folha_names_every_group_in_its_table_of_contents():
    html = themes.nav_html("folha", "")
    for g in items.GROUPS:
        assert f'<span class="grp">{g.name}</span>' in html


def test_folha_numbers_the_reference_column_by_section():
    html = themes.home_html("folha")
    # 1.1 e' o VLAN Mapper (secao 1, primeira linha); 2.1 e' o GLV.
    assert '<td class="var">1.1</td>' in html
    assert '<td class="var">2.1</td>' in html
    assert '<td class="var">2.7</td>' in html


def test_folha_gives_an_empty_group_a_dashed_box():
    html = themes.home_html("folha")
    assert 'class="empty"' in html


@pytest.mark.parametrize("theme", ALL)
def test_every_group_appears_in_the_navigation(theme):
    html = themes.nav_html(theme, "")
    for g in items.GROUPS:
        assert g.short in html or g.name in html


@pytest.mark.parametrize("theme", ALL)
def test_an_empty_group_says_what_will_land_there(theme):
    html = themes.home_html(theme)
    for g in items.GROUPS:
        if not items.tools_of(g.key):
            assert g.empty in html, f"{theme} nao diz o que cai em {g.key}"
