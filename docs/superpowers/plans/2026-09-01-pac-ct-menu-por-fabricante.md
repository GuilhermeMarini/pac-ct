# PAC CT — rename and vendor-split menu: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the toolkit to PAC CT and split its nine-tool menu into four vendor sections — Geral, SEL, GE, Siemens — with the last two shipping declared and empty.

**Architecture:** The tool catalogue (`sellib/web/themes/items.py`) gains a `Grupo` dataclass, a `GROUPS` list, a required `grupo` field on `Tool`, and one `ORDINAL` dict that becomes the single source of the 1..9 number. The six renderers (`nav()` + `home()` in `folha.py`, `regua.py`, `caderno.py`) walk `GROUPS` instead of `TOOLS`, each expressing a group with the metaphor it already owns. The rename runs afterwards, in four ordered steps, on a clean tree.

**Tech Stack:** Python 3.10+ (tested on 3.12), stdlib `http.server`, pytest. No linter, no formatter, no build step. CSS is generated Python strings served from `/theme.css`.

**Spec:** `docs/superpowers/specs/2026-09-01-pac-ct-menu-por-fabricante-design.md`

## Global Constraints

- **User-facing strings are Portuguese, accented.** Group names, tool names, tooltips, empty-state lines, error messages. This is unchanged by the rename.
- **Code comments and docstrings are English for new code**; when editing an existing file, match that file's language (the three theme modules are commented in Portuguese without accents — keep that style inside them).
- **Theme CSS uses tokens only.** No literal colour, radius, font stack or padding in any `DELTA_CSS`. Reach for `--text`, `--border`, `--surface-2`, `--s1..--s5`, `--fs-1..--fs-5`, `--mono`, `--cond`, `--track`, `--pad-cell`.
- **The 1..9 ordinal is a contract.** Régua's cards read "Borne *i*" and its strip prints *i*; folha's `Ref.` column and caderno's tab numbers come from the same place. After this plan that place is `items.ORDINAL` and nothing else.
- **`selprotopy/` is vendored and hook-protected.** Never edited, never renamed.
- **Run the suite with** `.venv/bin/python -m pytest tests/` (dev deps: `.venv/bin/python -m pip install -r requirements-dev.txt`, the one sanctioned manual pip).
- **Two tests are `xfail(strict=True)`** and name real bugs. A green `x` is expected output, not a failure.
- **Project documentation is English** after Task 8 (`README.md`, `docs/ENGINEERING-NOTES.md`, new specs and plans). Historic records — `corrections_plan.md`, earlier specs, the ten `mockups/` — are not rewritten.

**Phase boundary:** Tasks 1–5 (the menu) touch only `sellib/web/themes/` and `tests/test_theme_nav.py`, none of which appear in the current uncommitted diff — they are safe on today's dirty tree. Tasks 6–10 (the rename) require a clean tree and must run in order.

---

### Task 1: The group data model

**Files:**
- Modify: `sellib/web/themes/items.py`
- Test: `tests/test_theme_nav.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `items.Grupo(key, nome, curto, come, vazio="")` (frozen dataclass); `items.GROUPS: list[Grupo]`; `items.GROUP_ORDER: list[str]`; `items.tools_of(grupo: str) -> list[Tool]`; `items.ORDINAL: dict[str, int]`; `Tool` gains a required positional `grupo: str` right after `key`. Tasks 2–4 use `GROUPS`, `tools_of` and `ORDINAL` and nothing else.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_theme_nav.py`:

```python
def test_every_tool_declares_a_known_group():
    keys = {g.key for g in items.GROUPS}
    for t in items.TOOLS:
        assert t.grupo in keys, f"{t.key} declara um grupo desconhecido: {t.grupo}"


def test_the_catalogue_is_sorted_by_group():
    """O ordinal 1..9 sai da posicao em TOOLS. Se a lista nao estiver na ordem
    dos grupos, a home imprime um numero e a tira imprime outro."""
    posicoes = [items.GROUP_ORDER.index(t.grupo) for t in items.TOOLS]
    assert posicoes == sorted(posicoes)


def test_the_ordinal_is_the_position_in_the_catalogue():
    """UMA fonte pro numero: os seis renderizadores leem ORDINAL, nunca
    enumerate() da propria lista filtrada."""
    assert items.ORDINAL == {t.key: i for i, t in enumerate(items.TOOLS, start=1)}


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
        assert g.vazio.strip()
        assert g.come.strip()
```

And **replace** the existing `test_the_first_tool_is_still_number_one` (lines 28–35) with:

```python
@pytest.mark.parametrize("theme", ALL)
def test_the_first_tool_is_the_vendor_neutral_one(theme):
    """régua's home cards say "Borne i" and its strip has to keep matching.
    Com os grupos, a ferramenta 1 passou a ser o VLAN Mapper: e' a unica que
    nao pede nada de fabricante nenhum, e o menu abre pelo generico."""
    html = themes.nav_html(theme, "vlan-mapper")
    assert ">1<" in html or ">01<" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -v`
Expected: FAIL — `AttributeError: module 'sellib.web.themes.items' has no attribute 'GROUPS'`, and `test_the_first_tool_is_the_vendor_neutral_one` fails because `vlan-mapper` is still number 4.

- [ ] **Step 3: Add `Grupo`, `GROUPS` and the helpers to `items.py`**

Insert after the `Tool` dataclass (after the `no_ar` property, before the `TOOLS` comment block):

```python
@dataclass(frozen=True)
class Grupo:
    """Um grupo do menu: o fabricante que a ferramenta serve, ou a ausencia de
    um.

    O eixo NAO e' marca por marca -- e' o que a ferramenta come. Quem le SCD
    (IEC 61850) serve GE e Siemens igual, quem le RDB do QuickSet so serve SEL.
    Por isso `come` viaja junto: e' o que transforma uma secao vazia em roteiro.
    """

    key: str      # o slug, usado por Tool.grupo e pelos testes
    nome: str     # nome completo (folha, regua)
    curto: str    # nome curto (caderno, capa da regua)
    come: str     # o que este grupo le, uma linha
    vazio: str = ""   # o que dizer quando o grupo nao tem ferramenta


# A ordem dos grupos e' a ordem do menu, e a numeracao 1..9 corre por cima
# dela: do generico ao especifico. GE e Siemens entram declarados e VAZIOS de
# proposito -- ver o design de 2026-09-01.
GROUPS: "list[Grupo]" = [
    Grupo("geral", "Independentes de fabricante", "Geral",
          "SCD (IEC 61850) — serve qualquer relé"),
    Grupo("sel", "SEL", "SEL",
          "RDB do AcSELerator QuickSet"),
    Grupo("ge", "GE", "GE",
          "EnerVista — ajustes .urs",
          "Nenhuma ferramenta ainda. É aqui que a leitura de ajustes do "
          "EnerVista entra."),
    Grupo("siemens", "Siemens", "Siemens",
          "DIGSI",
          "Nenhuma ferramenta ainda."),
]

GROUP_ORDER: "list[str]" = [g.key for g in GROUPS]
```

- [ ] **Step 4: Add `grupo` to `Tool` and reorder `TOOLS`**

In the `Tool` dataclass, add the field immediately after `key`:

```python
    key: str            # o mesmo slug que o handler usa pra se marcar ativo
    grupo: str          # a key de um Grupo. OBRIGATORIO: um default silencioso
                        # poe a ferramenta nova no grupo errado sem ninguem ver
    href: "str | None"  # None = ainda nao existe, aparece desabilitada
```

Then replace the whole `TOOLS` list with the version below — same nine tools, same texts, reordered into group order and each carrying its group:

```python
TOOLS: "list[Tool]" = [
    # -- independentes de fabricante -----------------------------------------
    Tool("vlan-mapper", "geral", "/vlan-mapper/",
         "VLAN Mapper", "VLAN Mapper",
         "GOOSE por porta",
         "VLANs de GOOSE (subscritas e publicadas) que precisam estar "
         "liberadas na porta do switch.",
         "SCD"),
    Tool("relatorio", "geral", None,
         "Relatório de Comissionamento", "Relatório",
         "em breve",
         "Checklist dos testes funcionais por baia, com fotos e "
         "oscilografias.",
         "—"),
    # -- SEL ------------------------------------------------------------------
    Tool("glv", "sel", "/glv/",
         "Visualizador de Lógica", "Lógica ao vivo",
         "estado do relé sobre o GLE",
         "Estado do relé ao vivo sobre o diagrama GLE do AcSELerator QuickSet.",
         "RDB + telnet", nota=1),
    Tool("settings-compare", "sel", "/settings-compare/",
         "Comparador de Ajustes", "Comparador",
         "até 7 relés lado a lado",
         "Até 7 relés da mesma família (3xx/4xx/7xx) lado a lado; detecta "
         "equações equivalentes por álgebra booleana.",
         "RDB", nota=3),
    Tool("vb-updater", "sel", "/vb-updater/",
         "VB Updater", "VB Updater",
         "virtual bits GLE ↔ SCD",
         "Compara descrições de Virtual Bits entre o GLE do RDB e o SCD, por "
         "relé/IED.",
         "RDB + SCD"),
    Tool("gle-exporter", "sel", "/gle-exporter/",
         "Exportador de Comentários GLE", "Exportador",
         "Excel, ida e volta",
         "Comentários de porta em Excel, uma aba por GLE; edite e reimporte "
         "para gerar um RDB atualizado.",
         "RDB + XLSX"),
    Tool("dnp-map", "sel", "/dnp-map/",
         "Editor de Mapa DNP", "Mapa DNP",
         "pontos DNP3 do relé",
         "Edita os pontos DNP3 (SET_D) de cada relé do RDB e gera um RDB "
         "novo com as alterações aplicadas.",
         "RDB"),
    Tool("rdb-scd", "sel", None,
         "Comparador RDB ↔ SCD", "RDB ↔ SCD",
         "em breve",
         "Cruza ajustes do RDB com IEDs do SCD por IP e RID; reporta "
         "divergências e itens órfãos de cada lado.",
         "RDB + SCD"),
    Tool("validador", "sel", None,
         "Validador de Ajustes", "Validador",
         "em breve",
         "Valida pickups, tempos e polarizações contra o template do projeto.",
         "RDB"),
]
```

- [ ] **Step 5: Add `tools_of` and `ORDINAL` after `TOOLS`**

```python
def tools_of(grupo: str) -> "list[Tool]":
    """As ferramentas de um grupo, na ordem do catalogo. Lista vazia e' uma
    resposta legitima: GE e Siemens estao declarados e ainda sem ferramenta."""
    return [t for t in TOOLS if t.grupo == grupo]


# O ordinal de cada ferramenta, 1..9, na ordem dos grupos. UMA fonte, e e'
# esta: seis renderizadores imprimem este numero (a tira e as fichas da regua
# entre eles), e um `enumerate()` por renderizador sobre uma lista que quatro
# deles ainda filtram e' exatamente como os dois lados se separam.
ORDINAL: "dict[str, int]" = {t.key: i for i, t in enumerate(TOOLS, start=1)}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -v`
Expected: PASS for the six new tests. The three `home()`/`nav()` renderers still work unchanged — they read `TOOLS`, which is still a flat list of nine — so every other test in the file stays green.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, 2 xfailed.

- [ ] **Step 8: Commit**

```bash
git add sellib/web/themes/items.py tests/test_theme_nav.py
git commit -m "Menu: o catalogo ganha grupo, e o numero ganha uma fonte so

Cada Tool passa a declarar o grupo (geral/sel/ge/siemens), obrigatorio e
sem default -- um default silencioso poe a ferramenta nova no grupo
errado sem ninguem ver, como o fast_read faria. TOOLS e' reordenada pra
ordem dos grupos, e o ordinal 1..9 vira ORDINAL, um dict: seis
renderizadores imprimem esse numero e a tira e as fichas da regua tem que
concordar.

O VLAN Mapper passa a ser a ferramenta 1: e' a unica que nao pede nada de
fabricante nenhum. O GLV vira 3."
```

---

### Task 2: Caderno de Campo renders the groups

**Files:**
- Modify: `sellib/web/themes/caderno.py`
- Test: `tests/test_theme_nav.py`

**Interfaces:**
- Consumes: `items.GROUPS`, `items.tools_of`, `items.ORDINAL` from Task 1.
- Produces: caderno's `nav()` emits `<span class="tabsep">` between tab runs; `home()` emits `<div class="grp">` per group and `<div class="blank">` for an empty one.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_theme_nav.py`:

```python
def test_caderno_labels_each_group_in_the_strip():
    html = themes.nav_html("caderno", "")
    for g in items.GROUPS:
        assert f'<span class="tabsep">{g.curto}</span>' in html


def test_caderno_gives_an_empty_group_a_blank_sheet():
    html = themes.home_html("caderno")
    assert 'class="blank"' in html
    for g in items.GROUPS:
        if not items.tools_of(g.key):
            assert g.vazio in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -k caderno -v`
Expected: FAIL — `assert '<span class="tabsep">Geral</span>' in html`.

- [ ] **Step 3: Update the import and rewrite `nav()`**

Change the import at the top of `sellib/web/themes/caderno.py` to:

```python
from sellib.web.themes.items import (EM_BREVE, FILES_ITEM, GROUPS, MENU_ITEM,
                                      NO_AR, NOTES, ORDINAL, TOOLS, tools_of)
```

Replace `nav()` with:

```python
def nav(active: str = "") -> str:
    """As divisórias: rotulo curto e numero de dois digitos, como no mockup.

    Os grupos entram como etiqueta entre corridas de divisorias -- e' o que uma
    divisoria de caderno faz. Os arquivos do projeto levam "A" no lugar do
    numero: as ferramentas continuam 01..09, que e' a ordem que a home conta.
    """
    out = ['<nav class="tabs" aria-label="Ferramentas">']
    key, href, _nome, curto, _dica = MENU_ITEM
    out.append(_tab(key == active, href, "00", curto))
    fkey, fhref, _fnome, fcurto, _fdica = FILES_ITEM
    out.append(_tab(fkey == active, fhref, "A", fcurto))
    for g in GROUPS:
        out.append(f'  <span class="tabsep">{g.curto}</span>')
        lista = tools_of(g.key)
        if not lista:
            out.append('  <span class="tab off"><span class="n">&mdash;</span>'
                       'reservado</span>')
            continue
        for t in lista:
            out.append(_tab(t.key == active, t.href,
                            f"{ORDINAL[t.key]:02d}", t.curto))
    out.append("</nav>")
    return "\n".join(out)
```

- [ ] **Step 4: Rewrite `home()`**

```python
def home() -> str:
    """O menu como fichas presas na folha, agrupadas por fabricante.

    Um grupo sem ferramenta vira folha em branco reservada, com a linha do que
    vai cair ali: vazio e mudo e' vapor, vazio com roteiro e' previsao.
    """
    blocos = []
    for g in GROUPS:
        lista = tools_of(g.key)
        if lista:
            no_ar = sum(1 for t in lista if t.no_ar)
            cnt = f"{no_ar} em uso &middot; {len(lista) - no_ar} em breve"
        else:
            cnt = "reservado"
        blocos.append(f'      <div class="grp"><h3>{g.nome}</h3>'
                      f'<span class="come">{g.come}</span>'
                      f'<span class="cnt">{cnt}</span></div>')
        if not lista:
            blocos.append('      <div class="blank">'
                          '<span class="t">folha em branco</span>'
                          f'<span class="d">{g.vazio}</span></div>')
            continue
        fichas = []
        for t in lista:
            if t.no_ar:
                abre, fecha, tag = (f'<a class="card" href="{t.href}">',
                                    "</a>", "em uso")
            else:
                abre, fecha, tag = '<span class="card off">', "</span>", "em breve"
            fichas.append(f'        {abre}<span class="tag">{tag}</span>'
                          f'<h3>{t.nome}</h3>\n'
                          f'          <p>{t.funcao}</p>{fecha}')
        blocos.append('      <div class="cards">\n'
                      + "\n".join(fichas) + '\n      </div>')
    notas = "\n".join(f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
                      for i, txt in enumerate(NOTES, start=1))
    gerais = len(tools_of("geral"))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <h2>Ferramentas</h2>
      <p class="lead">{NO_AR} em uso nesta instalação e {EM_BREVE} por fazer.
      {gerais} servem qualquer relé — leem SCD; as outras
      {len(TOOLS) - gerais} pedem o RDB do AcSELerator QuickSet. Os RDB e SCD
      do projeto entram uma vez em <a href="/arquivos/">Arquivos do
      Projeto</a>; cada ficha escolhe dali. Só a primeira conversa com o
      relé.</p>
{chr(10).join(blocos)}
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notas}
  </aside>
</div>"""
```

- [ ] **Step 5: Add the CSS**

Append to caderno's `DELTA_CSS`, immediately before the closing `"""`, after the `@media (prefers-reduced-motion:reduce)` block:

```css
/* --- os grupos: etiqueta na tira, titulo escrito sobre a folha ------------ */
/* A etiqueta acompanha a base fechada das divisorias: ela e' a mesma regua de
   tinta, so que sem aba em cima. */
:root[data-theme=caderno] nav.tabs .tabsep{align-self:stretch;
  display:inline-flex;align-items:flex-end;padding:0 var(--s2) 8px;
  font:var(--w-label) var(--fs-1) var(--mono);letter-spacing:var(--track);
  text-transform:uppercase;color:var(--text-2);
  border-bottom:1px solid var(--text)}
:root[data-theme=caderno] .grp{display:flex;flex-wrap:wrap;align-items:baseline;
  gap:var(--s3);margin:var(--s5) 0 var(--s3);padding-bottom:5px;
  border-bottom:1px solid var(--text)}
:root[data-theme=caderno] .grp:first-of-type{margin-top:var(--s2)}
:root[data-theme=caderno] .grp h3{margin:0;font:var(--w-bold) var(--fs-4)/1 var(--sans)}
:root[data-theme=caderno] .grp .come{font:400 var(--fs-2) var(--mono);
  color:var(--text-2)}
:root[data-theme=caderno] .grp .cnt{margin-left:auto;
  font:var(--w-label) var(--fs-1) var(--mono);letter-spacing:var(--track);
  text-transform:uppercase;color:var(--text-3)}
/* Grupo sem ferramenta: folha em branco, presa e reservada. */
:root[data-theme=caderno] .blank{border:1px dashed var(--border);
  background:var(--surface-2);padding:var(--s3);display:flex;
  flex-direction:column;gap:var(--s1)}
:root[data-theme=caderno] .blank .t{font:var(--w-label) var(--fs-2) var(--mono);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-3)}
:root[data-theme=caderno] .blank .d{font-size:var(--fs-3);color:var(--text-2)}
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -v`
Expected: PASS, all of them.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/themes/caderno.py tests/test_theme_nav.py
git commit -m "Caderno: os grupos como etiqueta de divisoria e titulo na folha

A tira ganha .tabsep entre corridas de divisoria e a home ganha .grp
sobre cada bloco de fichas. Grupo sem ferramenta vira .blank -- folha em
branco reservada, com a linha do que vai cair ali."
```

---

### Task 3: Régua de Bornes renders the groups

**Files:**
- Modify: `sellib/web/themes/regua.py`
- Test: `tests/test_theme_nav.py`

**Interfaces:**
- Consumes: `items.GROUPS`, `items.tools_of`, `items.ORDINAL`.
- Produces: régua's `nav()` emits one `<div class="cap">Régua X<i> &mdash; <curto></div>` per group plus `Régua X0 &mdash; entrada` for terminal A; `home()` emits `<div class="grp">` per group.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_theme_nav.py` (add `import re` at the top of the file if not present):

```python
def test_regua_captions_every_group_and_the_entrance():
    html = themes.nav_html("regua", "")
    assert "Régua X0 &mdash; entrada" in html
    for i, g in enumerate(items.GROUPS, start=1):
        assert f"Régua X{i} &mdash; {g.curto}" in html


def test_regua_strip_and_cards_agree_on_the_number():
    """As fichas dizem "Borne i" e a tira imprime i. Sao dois renderizadores;
    o gotcha do docs/ENGINEERING-NOTES.md vira teste aqui."""
    nav = themes.nav_html("regua", "")
    home = themes.home_html("regua")
    for t in items.TOOLS:
        i = items.ORDINAL[t.key]
        assert f'<span class="num">{i}</span>' in nav
        m = re.search(re.escape(f"<h3>{t.nome}</h3>") + r".{0,600}?Borne (\d+)",
                      home, re.S)
        assert m, f"ficha de {t.nome} sem Borne"
        assert int(m.group(1)) == i, f"{t.nome}: tira {i}, ficha {m.group(1)}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -k regua -v`
Expected: FAIL — `assert 'Régua X0 &mdash; entrada' in html`.

- [ ] **Step 3: Update the import and rewrite `nav()`**

Change the import at the top of `sellib/web/themes/regua.py` to:

```python
from sellib.web.themes.items import (EM_BREVE, FILES_ITEM, GROUPS, NO_AR,
                                      NOTES, ORDINAL, TOOLS, tools_of)
```

Replace `nav()` with:

```python
def nav(active: str = "") -> str:
    """A régua: um borne por ferramenta, agrupados por fabricante.

    Cada grupo e' uma régua com a sua capa (X1..X4); a X0 e' a entrada, e ela
    existe porque o borne "A" (Arquivos do Projeto) ficaria parecendo do
    primeiro grupo assim que os grupos ganharam capa. A regua nao tem borne de
    Menu -- a volta pra home e' o "← Menu" da barra superior.

    Um grupo sem ferramenta e' um borne de reserva: numa régua de verdade isso
    nao e' defeito, e' previsao.
    """
    fkey, fhref, fnome, _fcurto, fdica = FILES_ITEM
    out = ['<nav class="strip" aria-label="Ferramentas">',
           '  <div class="cap">Régua X0 &mdash; entrada</div>',
           _borne(fkey == active, fhref, "A", fnome, fdica)]
    for i, g in enumerate(GROUPS, start=1):
        out.append(f'  <div class="cap later">Régua X{i} &mdash; {g.curto}</div>')
        lista = tools_of(g.key)
        if not lista:
            out.append(_borne(False, None, "&mdash;", "reserva", g.come))
            continue
        for t in lista:
            out.append(_borne(t.key == active, t.href, str(ORDINAL[t.key]),
                              t.nome, t.dica))
    out.append("</nav>")
    return "\n".join(out)


def _borne(on: bool, href: "str | None", n: str, label: str, dica: str) -> str:
    inner = (f'<span class="num">{n}</span>'
             f'<span class="lbl">{label}<small>{dica}</small></span>')
    if href is None:
        return f'  <span class="borne off">{inner}</span>'
    if on:
        return (f'  <a class="borne on" href="{href}" '
                f'aria-current="page">{inner}</a>')
    return f'  <a class="borne" href="{href}">{inner}</a>'
```

- [ ] **Step 4: Rewrite `home()`**

```python
def home() -> str:
    """O menu como fichas de borne, agrupadas, cada uma com a cor do fio."""
    blocos = []
    for g in GROUPS:
        lista = tools_of(g.key)
        blocos.append(f'      <div class="grp"><h3>{g.nome}</h3>'
                      f'<span class="come">{g.come}</span>'
                      f'<span class="ln"></span></div>')
        if not lista:
            blocos.append('      <div class="cards">\n'
                          '        <span class="card off">'
                          '<h3>Régua de reserva</h3>\n'
                          f'          <p>{g.vazio}</p>'
                          '<span class="st">sem fio</span></span>\n'
                          '      </div>')
            continue
        fichas = []
        for t in lista:
            i = ORDINAL[t.key]
            if t.no_ar:
                fio = _FIOS[(i - 1) % len(_FIOS)]
                cls = f"card {fio}".strip()
                abre, fecha = f'<a class="{cls}" href="{t.href}">', "</a>"
                st = f"Borne {i} &middot; ligado"
            else:
                abre, fecha = '<span class="card off">', "</span>"
                st = f"Borne {i} &middot; em breve"
            fichas.append(f'        {abre}<h3>{t.nome}</h3>\n'
                          f'          <p>{t.funcao}</p>'
                          f'<span class="st">{st}</span>{fecha}')
        blocos.append('      <div class="cards">\n'
                      + "\n".join(fichas) + '\n      </div>')
    notas = "\n".join(f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
                      for i, txt in enumerate(NOTES, start=1))
    vazios = sum(1 for g in GROUPS if not tools_of(g.key))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <h2>Ferramentas ligadas</h2>
      <p class="lead">{NO_AR} bornes energizados nesta instalação, {EM_BREVE} de
      reserva e {vazios} réguas ainda sem fio. Os RDB e SCD do projeto entram
      uma vez no borne <a href="/arquivos/">A &mdash; Arquivos do Projeto</a>;
      cada borne escolhe dali. Só o borne {ORDINAL["glv"]} conversa com o relé
      pela rede.</p>
{chr(10).join(blocos)}
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notas}
  </aside>
</div>"""
```

- [ ] **Step 5: Add the CSS**

Append to régua's `DELTA_CSS`, immediately before the closing `"""`, after the `@media (max-width:860px)` block:

```css
/* --- os grupos: uma capa por régua, um filete de latao na home ------------ */
:root[data-theme=regua] .strip .cap.later{margin-top:var(--s3);
  padding-top:var(--s3);border-top:1px solid var(--border)}
:root[data-theme=regua] .grp{display:flex;flex-wrap:wrap;align-items:baseline;
  gap:var(--s3);margin:var(--s4) 0 var(--s3)}
:root[data-theme=regua] .grp:first-of-type{margin-top:0}
:root[data-theme=regua] .grp h3{margin:0;
  font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track-wide);
  text-transform:uppercase;color:var(--accent)}
:root[data-theme=regua] .grp .come{font-size:var(--fs-2);color:var(--text-3)}
:root[data-theme=regua] .grp .ln{flex:1;min-width:var(--s5);height:1px;
  background:var(--border)}
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/themes/regua.py tests/test_theme_nav.py
git commit -m "Regua: uma régua por grupo, e a X0 da entrada

Cada grupo vira uma capa X1..X4 na tira e um filete de latao na home. A
X0 e' nova: o borne A ficaria parecendo do primeiro grupo assim que os
grupos ganharam capa. Grupo sem ferramenta e' borne de reserva, que numa
régua de verdade e' previsao e nao defeito.

O numero sai de ORDINAL nos dois renderizadores, e um teste novo afirma
que a tira e a ficha concordam -- o gotcha do docs/ENGINEERING-NOTES.md deixa de ser so
comentario."
```

---

### Task 4: Folha de Dados renders the groups

**Files:**
- Modify: `sellib/web/themes/folha.py`
- Test: `tests/test_theme_nav.py`

**Interfaces:**
- Consumes: `items.GROUPS`, `items.tools_of`, `items.ORDINAL`.
- Produces: folha's `nav()` emits `<span class="grp">` labels in the `.toc`; `home()` emits one `<h2><span class="num">N.</span>` section per group and a `<div class="vazio">` for an empty one; the `Ref.` column reads `<group>.<position>`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_theme_nav.py`:

```python
def test_folha_names_every_group_in_its_table_of_contents():
    html = themes.nav_html("folha", "")
    for g in items.GROUPS:
        assert f'<span class="grp">{g.nome}</span>' in html


def test_folha_numbers_the_reference_column_by_section():
    html = themes.home_html("folha")
    # 1.1 e' o VLAN Mapper (secao 1, primeira linha); 2.1 e' o GLV.
    assert '<td class="var">1.1</td>' in html
    assert '<td class="var">2.1</td>' in html
    assert '<td class="var">2.7</td>' in html


def test_folha_gives_an_empty_group_a_dashed_box():
    html = themes.home_html("folha")
    assert 'class="vazio"' in html
```

And the cross-theme invariant, which all three now satisfy:

```python
@pytest.mark.parametrize("theme", ALL)
def test_every_group_appears_in_the_navigation(theme):
    html = themes.nav_html(theme, "")
    for g in items.GROUPS:
        assert g.curto in html or g.nome in html


@pytest.mark.parametrize("theme", ALL)
def test_an_empty_group_says_what_will_land_there(theme):
    html = themes.home_html(theme)
    for g in items.GROUPS:
        if not items.tools_of(g.key):
            assert g.vazio in html, f"{theme} nao diz o que cai em {g.key}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -k "folha or group" -v`
Expected: FAIL — `assert '<span class="grp">Independentes de fabricante</span>' in html`.

- [ ] **Step 3: Update the import and rewrite `nav()`**

Change the import at the top of `sellib/web/themes/folha.py` to:

```python
from sellib.web.themes.items import (EM_BREVE, FILES_ITEM, GROUPS, MENU_ITEM,
                                      NO_AR, NOTES, ORDINAL, TOOLS, tools_of)
```

Replace `nav()` with:

```python
def nav(active: str = "") -> str:
    """O sumario: Menu, os arquivos do projeto e as nove ferramentas.

    Um sumario de folha de dados lista os TITULOS DE SECAO, entao o nome do
    grupo entra antes de cada corrida. O rotulo da ferramenta e' texto solto
    dentro do `<a>`, sem `<span class=lbl>`: a folha nao tem subtitulo.

    Os arquivos levam "A" e nao um numero: as ferramentas seguem numeradas de
    1 a 9 e a home as conta por essa mesma ordem.
    """
    out = ['<nav class="toc" aria-label="Ferramentas">']
    key, href, nome, _curto, _dica = MENU_ITEM
    out.append(_link(key == active, href, 0, nome))
    fkey, fhref, fnome, _fcurto, _fdica = FILES_ITEM
    out.append(_link(fkey == active, fhref, "A", fnome))
    for g in GROUPS:
        out.append(f'  <span class="grp">{g.nome}</span>')
        lista = tools_of(g.key)
        if not lista:
            out.append('  <span class="off">reservado</span>')
            continue
        for t in lista:
            out.append(_link(t.key == active, t.href, ORDINAL[t.key], t.nome))
    out.append("</nav>")
    return "\n".join(out)
```

- [ ] **Step 4: Rewrite `home()`**

```python
def home() -> str:
    """O menu como tabela de referencia, uma secao numerada por grupo.

    A coluna Ref. vira `<secao>.<linha>` -- e' como uma folha de dados numera
    de verdade. O ordinal global (1..9) continua sendo o que a régua e o
    caderno imprimem; o dado oferece os dois e cada direcao escolhe.
    """
    secoes = []
    for i, g in enumerate(GROUPS, start=1):
        lista = tools_of(g.key)
        cab = (f'      <h2><span class="num">{i}.</span>{g.nome}'
               f'<span class="come">entrada: {g.come}</span></h2>')
        if not lista:
            secoes.append(cab + f'\n      <div class="vazio">{g.vazio}</div>')
            continue
        linhas = []
        for j, t in enumerate(lista, start=1):
            ref = f'<sup class="ref">{t.nota}</sup>' if t.nota else ""
            nome = f'<a href="{t.href}">{t.nome}</a>' if t.no_ar else t.nome
            estado = ('<span class="j j-ok">Disponível</span>' if t.no_ar
                      else '<span class="j j-falta">Em breve</span>')
            linhas.append(
                f'            <tr><td class="var">{i}.{j}</td>\n'
                f'              <td class="txt tool">{nome}</td>\n'
                f'              <td class="txt">{t.funcao}{ref}</td>\n'
                f'              <td class="val">{t.entrada}</td>\n'
                f'              <td>{estado}</td></tr>')
        secoes.append(
            cab + '\n      <div class="wrap">\n        <table>\n'
            '          <thead><tr><th>Ref.</th><th>Ferramenta</th>'
            '<th>Função</th>\n'
            '            <th>Entrada</th><th>Estado</th></tr></thead>\n'
            '          <tbody>\n'
            + "\n".join(linhas)
            + '\n          </tbody>\n        </table>\n      </div>')
    notas = "\n".join(
        f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
        for i, txt in enumerate(NOTES, start=1))
    gerais = len(tools_of("geral"))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <p class="lead">{NO_AR} ferramentas disponíveis nesta instalação, em
      {len(GROUPS)} seções. {gerais} servem qualquer relé, porque leem SCD;
      as outras {len(TOOLS) - gerais} pedem o RDB do AcSELerator QuickSet.
      Cada uma abre na própria aba e todas ficam no ar ao mesmo tempo; só o
      Visualizador de Lógica conversa com o relé. Os RDB e SCD do projeto
      entram uma vez em <a href="/arquivos/">Arquivos do Projeto</a>; cada
      ferramenta escolhe dali.</p>
{chr(10).join(secoes)}
      <div class="totals">
        <span><b>{NO_AR}</b> no ar</span>
        <span><b>{EM_BREVE}</b> em desenvolvimento</span>
        <span>uploads isolados por visitante<sup class="ref">2</sup></span>
      </div>
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notas}
  </aside>
</div>"""
```

- [ ] **Step 5: Add the CSS**

Append to folha's `DELTA_CSS`, immediately before the closing `"""`:

```css
/* --- os grupos: titulo de secao no sumario e no corpo --------------------- */
:root[data-theme=folha] .toc .grp{font:var(--w-label) var(--fs-1) var(--mono);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-3);
  padding:3px var(--s2) 3px 0;white-space:nowrap}
/* O `come` encosta na direita do proprio titulo, entao o h2 vira faixa. */
:root[data-theme=folha] h2{display:flex;flex-wrap:wrap;align-items:baseline;
  gap:var(--s2)}
:root[data-theme=folha] h2 .come{margin-left:auto;
  font:400 var(--fs-1) var(--mono);color:var(--text-3);
  letter-spacing:normal;text-transform:none}
:root[data-theme=folha] .vazio{padding:var(--pad-cell);font-size:var(--fs-3);
  color:var(--text-3);border:1px dashed var(--border);
  background:var(--surface-2)}
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -v`
Expected: PASS, including the two cross-theme invariants.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, 2 xfailed.

- [ ] **Step 8: Commit**

```bash
git add sellib/web/themes/folha.py tests/test_theme_nav.py
git commit -m "Folha: uma secao numerada por grupo, Ref. por secao

O sumario passa a nomear o grupo antes de cada corrida -- um sumario de
folha de dados lista titulo de secao -- e o corpo vira quatro secoes, com
a coluna Ref. em <secao>.<linha>. Grupo sem ferramenta e' caixa
tracejada com a linha do que vai cair ali.

Fecha os dois invariantes entre direcoes: todo grupo aparece nas tres
navegacoes, e todo grupo vazio diz o que vai cair ali nas tres homes."
```

---

### Task 5: See it in a browser

**Files:**
- No code. Verification only.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: a green light for the rename phase, or a bug report against Tasks 2–4.

The web tools have no unit tests by policy — the screens are verified by exercising them. This task is that verification, and it is where a CSS collision (the one thing the tests above cannot see) surfaces.

- [ ] **Step 1: Launch the server**

Run: `python3 app.py --web`
Expected: `http://0.0.0.0:8765` serving, no traceback at boot. A missing token name raises at boot, so a clean start already proves `tokens.py` still covers every name the three `DELTA_CSS` use.

- [ ] **Step 2: Check all three directions on the home**

Open `http://localhost:8765/`, and use the theme picker on the home to visit each of **Caderno de Campo**, **Folha de Dados** and **Régua de Bornes**. In each, confirm:

1. Four group headings, in the order Geral → SEL → GE → Siemens.
2. The GE and Siemens sections show their empty-state line, and it reads as reserved rather than broken.
3. Numbering: VLAN Mapper is 1, GLV is 3, Validador is 9 — in the navigation *and* on the cards.
4. Régua only: the strip reads `Régua X0 — entrada` above terminal A, then `X1 Geral`, `X2 SEL`, `X3 GE`, `X4 Siemens`; the page is not collapsed to ~200px wide (that is the `<!--NAV:...-->` marker gotcha — the marker must stay the first child inside `<div class="shell">`).
5. Caderno only: the divider strip wraps across two or three rows without leaving a tab hanging over the row below.

- [ ] **Step 3: Check a tool page in each direction**

Open `/vlan-mapper/` and `/glv/` in each theme. The navigation there comes from the same `nav()`, so a broken group label shows up on all nine screens, not just the home.

- [ ] **Step 4: Commit anything you had to fix**

If Steps 2–3 surfaced a CSS or markup bug, fix it, re-run `.venv/bin/python -m pytest tests/test_theme_nav.py -v`, and commit with a message naming what was wrong on screen. If nothing was wrong, there is nothing to commit — say so and move on.

---

### Task 6: Clean the tree for the rename

**Files:**
- No new files. This task decides what happens to ~30 modified files and 7 untracked ones.

**Interfaces:**
- Consumes: Tasks 1–5 committed.
- Produces: `git status --porcelain` empty. Tasks 7–10 cannot start without it.

**A `sed` across 974 occurrences in 162 files does not coexist with uncommitted work.** Conflicts from a mechanical rename are not resolvable by hand at that scale.

- [ ] **Step 1: Show the user what is pending**

Run: `git status --short`
The GLV connectors work is in there (`sellib/web/glv/connectors.py`, `tests/test_glv_connectors.py`, `tests/fixtures/connectors.gle.xml`, `docs/superpowers/specs/2026-09-01-glv-connectors-design.md`, plus ~30 modified files).

- [ ] **Step 2: Ask the user how to clear it**

This is the user's call, not the implementer's — the two options are *commit it* (if the connectors work is finished and its tests pass) or *stash it* (`git stash push -u -m "glv connectors, antes do rename"`, restored after Task 10 and then rebased by hand onto the renamed tree, which is real work).

Do not choose for them. Ask, and wait.

- [ ] **Step 3: Verify the tree is clean**

Run: `git status --porcelain`
Expected: no output at all.

- [ ] **Step 4: Establish the baseline**

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: record the exact counts (e.g. `672 passed, 2 xfailed`). Tasks 7–10 must end on the same numbers — that is how a mechanical rename proves it changed nothing.

---

### Task 7: The visible brand becomes PAC CT

**Files:**
- Modify: `sellib/web/dashboard.py:46,55,56`
- Modify: `app.py:2` and the module docstring's mode list

**Interfaces:**
- Consumes: a clean tree (Task 6).
- Produces: nothing importable. The brand string on screen.

- [ ] **Step 1: Change the home's title and masthead**

In `sellib/web/dashboard.py`, inside `HOME_HTML`:

```html
<title>PAC CT &mdash; Ferramentas</title>
```

and

```html
  <div>
    <h1>PAC CT</h1>
    <div class="sub">Comissionamento de prote&ccedil;&atilde;o, automa&ccedil;&atilde;o e controle</div>
  </div>
```

The tagline stays Portuguese: the brand is an English acronym, the interface is not.

- [ ] **Step 2: Change the launcher docstring**

In `app.py`, line 2:

```
Launcher do PAC CT -- Protection, Automation & Control Commissioning Toolkit
(multi-ferramenta web).
```

and in the same docstring's project sketch, `Sel_commissioning/` becomes `pac-ct/`, and the `--web` line reads `# PAC CT web (menu de ferramentas)`.

- [ ] **Step 3: Check the generated stylesheet header**

`sellib/web/themes/__init__.py:theme_css()` opens the CSS with a comment naming the product:

```python
        f"/* PAC CT -- tema \"{THEMES[theme]}\" ({theme}).\n"
```

- [ ] **Step 4: Verify no visible "SEL Commissioning" or "Comissionamento SEL" survives outside the record**

Run:

```bash
grep -rn "Comissionamento SEL\|SEL Commissioning\|SEL Toolkit" \
  --include=*.py --include=*.html . | grep -v "^./.venv/" | grep -v "^./mockups/"
```

Expected: no output.

- [ ] **Step 5: Run the suite and the server**

Run: `.venv/bin/python -m pytest tests/ -q` — same counts as the Task 6 baseline.
Run: `python3 app.py --web` and confirm the home reads **PAC CT**.

- [ ] **Step 6: Commit**

```bash
git add app.py sellib/web/dashboard.py sellib/web/themes/__init__.py
git commit -m "PAC CT: a marca na tela

Protection, Automation & Control Commissioning Toolkit. O toolkit deixou
de ser so SEL -- o VLAN Mapper come SCD e serve GE ou Siemens como serve
um SEL -- e o <h1> dizia o contrario.

A sigla e' inglesa, a interface nao: a tagline continua em portugues
acentuado, como a convencao manda."
```

---

### Task 8: README and docs/ENGINEERING-NOTES.md in English

**Files:**
- Modify: `README.md` (348 lines)
- Modify: `docs/ENGINEERING-NOTES.md` (154 lines)

**Interfaces:**
- Consumes: Task 7.
- Produces: the project's documentation language.

Translate, don't rewrite: every gotcha keeps its measured numbers, its file paths and its argument. A gotcha that loses the measurement that justified it becomes an opinion.

- [ ] **Step 1: Translate `docs/ENGINEERING-NOTES.md`**

Work top to bottom. Four things must survive the translation exactly:

1. Every number (974 occurrences, 22 025 names across 5 LDs, 142.9 MB → 36.9 MB, 0 false positives across 173 046 values, and so on).
2. Every path, identifier and filename, verbatim.
3. The Portuguese gotcha about GLE connectors ("Um conector do GLE não é uma aresta") — translate it like the rest; it is the odd one out only because it was written in a Portuguese session.
4. The convention section's rule that **user-facing strings stay Portuguese, accented**, which is now the one language rule that does not change.

Update the opening line to name the product and drop the SEL-only framing:

```markdown
Web toolkit for commissioning protection, automation and control systems —
SEL relays today (SEL-411L, SEL-451, SEL-487E, SEL-751, SEL-787, …), with
IEC 61850 tools that already serve any vendor. Nine tools, six shipping, all
served from a single dashboard on one port.
```

Add, to the Gotchas section:

```markdown
- **The menu is grouped by what a tool eats, and two groups ship empty.**
  `themes/items.py` carries `GROUPS` (geral → SEL → GE → Siemens) and every
  `Tool` declares its `grupo`, required and without a default — the same reason
  `fast_read` has none. `TOOLS` is sorted into group order and `ORDINAL` is the
  one source of the 1..9 number, because six renderers print it and régua's
  strip and cards have to agree. GE and Siemens are declared with no tools and
  a `vazio` line saying what will land there: empty and silent is vapour, empty
  with a roadmap is a forecast.
- **`corrections_plan.md`, the specs under `docs/superpowers/specs/` dated
  before 2026-09-01, and the ten `mockups/` predate the PAC CT rename** and
  still say `sellib` and "Comissionamento SEL". They are a record of what was
  done, not documentation of what is: they are deliberately not rewritten.
```

- [ ] **Step 2: Translate `README.md`**

Same rules. The tool table's numbering changes with the menu — renumber it to match `items.ORDINAL` (1 VLAN Mapper, 2 Relatório, 3 GLV, …, 9 Validador) and group its rows under the four headings, so the README and the home agree. The title becomes:

```markdown
# PAC CT — Protection, Automation & Control Commissioning Toolkit
```

The tool names inside the table stay in Portuguese: they are what the screen says.

- [ ] **Step 3: Verify nothing lost a number**

Run:

```bash
git diff --stat README.md docs/ENGINEERING-NOTES.md
grep -c "974\|22 025\|142.9\|173,046\|173 046" docs/ENGINEERING-NOTES.md
```

Expected: the counts that were in the file before are still in it. Read the diff — this is the step where a translation quietly drops a measurement.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ENGINEERING-NOTES.md
git commit -m "Docs: README e docs/ENGINEERING-NOTES.md em ingles, e o PAC CT no titulo

A documentacao do projeto passa a ser inglesa junto com o rename. As
strings de usuario NAO: templates, nomes de ferramenta e mensagens de
erro seguem em portugues acentuado, que e' a convencao que o proprio
docs/ENGINEERING-NOTES.md declara.

Traducao, nao reescrita: todo numero medido, todo caminho e todo
identificador atravessam iguais. Um gotcha que perde a medicao que o
justificava vira opiniao."
```

---

### Task 9: `sellib` becomes `pacct`

**Files:**
- Rename: `sellib/` → `pacct/`
- Modify: every file that says `sellib` — 974 occurrences across 162 files

**Interfaces:**
- Consumes: Task 8, clean tree.
- Produces: `import pacct.…` everywhere. Nothing else changes.

- [ ] **Step 1: Move the package**

```bash
git mv sellib pacct
```

- [ ] **Step 2: Rewrite the references**

```bash
grep -rlZ --include='*.py' --include='*.md' --include='*.html' \
     --include='*.json' --include='*.ini' --include='*.example' \
     --include='*.txt' -e 'sellib' . \
  | grep -zZv -e '^\./\.venv/' -e '^\./selprotopy/' -e '^\./mockups/' \
              -e '^\./corrections_plan\.md$' \
              -e '^\./docs/superpowers/specs/' \
  | xargs -0 sed -i 's/\bsellib\b/pacct/g'
```

The four exclusions are deliberate: `.venv/` is generated, `selprotopy/` is vendored and hook-protected, and `mockups/` plus the earlier specs and `corrections_plan.md` are the historic record Task 8 just documented as deliberately untouched.

- [ ] **Step 3: Verify nothing importable still says `sellib`**

```bash
grep -rn '\bsellib\b' --include='*.py' . | grep -v '^./.venv/'
```

Expected: no output.

- [ ] **Step 4: Run the whole suite — this is the verification**

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: **the same counts as the Task 6 baseline.** Every broken import surfaces here; the suite imports `pacct.core`, `pacct.parsers`, `pacct.web` and `pacct.matchers` across its files.

- [ ] **Step 5: Boot the server**

Run: `python3 app.py --web`
Expected: the dashboard comes up and all nine screens are reachable. `app.py` resolves `pacct.web.dashboard` by name, and `tests/test_requirements.py` pins that `parse_requirements` still yields an importable name — but only a boot proves the mount table wired itself.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "sellib -> pacct

974 ocorrencias em 162 arquivos, mecanico. selprotopy nao muda: e'
vendorizada e o nome dela nao e' nosso. mockups/, corrections_plan.md e
os specs anteriores ficam como estao -- sao registro.

A suite passando E' a verificacao: todo import quebrado aparece nela."
```

---

### Task 10: The directory becomes `pac-ct`

**Files:**
- Rename: `~/py_projects/Sel_comissioning/` → `~/py_projects/pac-ct/`

**Interfaces:**
- Consumes: Task 9, clean tree.
- Produces: nothing the code reads. `PROJECT_ROOT = Path(__file__).resolve().parent.parent` is derived, so the code does not notice.

**This task ends the session's working directory.** Run it last, and expect to reopen the terminal in the new path afterwards.

- [ ] **Step 1: Confirm the tree is clean and the suite is green**

```bash
git status --porcelain          # no output
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 2: Move the directory**

```bash
cd ~/py_projects && mv Sel_comissioning pac-ct
```

- [ ] **Step 3: Rebuild the virtualenv**

The `.venv` stores absolute paths in `bin/activate` and `pyvenv.cfg`; the move breaks them.

```bash
cd ~/py_projects/pac-ct && rm -rf .venv && python3 app.py --web
```

Expected: `app.py` bootstraps a new `.venv` from `requirements.txt` and the dashboard comes up. This is the sanctioned path — do not build the venv by hand.

- [ ] **Step 4: Reinstall the dev dependencies and re-run the suite**

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: the Task 6 baseline counts.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "A pasta vira pac-ct

Ultimo passo do rename. O codigo nao percebe: PROJECT_ROOT e' derivado de
__file__. O .venv percebe -- caminhos absolutos -- e e' recriado pelo
proprio app.py no boot seguinte."
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `Grupo` dataclass with `key`/`nome`/`curto`/`come`/`vazio` | 1 |
| `GROUPS` ordered geral → SEL → GE → Siemens | 1 |
| `Tool.grupo` required, no default | 1 |
| `TOOLS` physically reordered; test pins the ordering | 1 |
| `items.ORDINAL` as the one source of the number | 1 |
| Numbering: VLAN Mapper 1, GLV 3, Validador 9 | 1 |
| `MENU_ITEM` / `FILES_ITEM` stay ungrouped, keep `0` and `A` | 1 (untouched by construction; covered by the pre-existing `test_the_files_tab_is_not_in_the_tool_catalogue`) |
| caderno: `.tabsep`, `.grp`, `.blank` | 2 |
| régua: `.cap` per group, `X0 — entrada`, `.grp`, reserve terminal | 3 |
| folha: `.grp` in the toc, per-group `<h2>`, `Ref.` = `<group>.<pos>`, `.vazio` | 4 |
| CSS uses tokens only | 2, 3, 4 (all values are `var(--…)`) |
| Home `lead` copy tells the split | 2, 3, 4 |
| Every group in all three `nav()` | 4 (cross-theme test) |
| Empty group's `vazio` line in all three `home()` | 4 (cross-theme test) |
| Régua strip and cards agree on the ordinal | 3 |
| `test_the_first_tool_is_still_number_one` rewritten, not deleted | 1 |
| Browser check across three directions | 5 |
| Clean tree before the rename | 6 |
| Visible brand → PAC CT | 7 |
| README + docs/ENGINEERING-NOTES.md → English | 8 |
| `sellib` → `pacct`, excluding vendored and historic files | 9 |
| Directory → `pac-ct`, `.venv` rebuilt, memory carried | 10 |

**Placeholder scan:** none. Every code step carries the code; every verification step carries the command and the expected output. Task 6 Step 2 deliberately stops and asks the user — that is a decision that belongs to them (their uncommitted work), not an unfilled blank.

**Type consistency:** `Grupo(key, nome, curto, come, vazio="")` is constructed with 4 or 5 positional arguments in Task 1 and read as `g.key`, `g.nome`, `g.curto`, `g.come`, `g.vazio` in Tasks 2–4. `tools_of(grupo: str) -> list[Tool]` is called with `g.key` throughout. `ORDINAL` is indexed by `t.key` in all three renderers and in the tests. `_borne(on, href, n, label, dica)` is new in Task 3 and called only there; `_tab` and `_link` keep their existing signatures.
