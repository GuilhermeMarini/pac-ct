"""Folha de Dados -- the relay manual, as a language.

Pale paper, a 1 px rule, sharp corners, high density and a margin column of
numbered notes. The direction's signature is the **numbered contents** (`.toc`)
and the **reference table**: in the menu each tool is a row with Ref., Function,
Input and Status -- not a card.

Reference: `mockups/06-folha/`. The `.toc` rules are that mockup's own
`theme.css`, with its values swapped for tokens.
"""

from __future__ import annotations

from pacct.web.themes.items import (
    FILES_ITEM,
    GROUPS,
    MENU_ITEM,
    NOTES,
    ORDINAL,
    PLANNED,
    SHIPPING,
    TOOLS,
    tools_of,
)

DELTA_CSS = r"""
:root[data-theme=folha] .page{margin:14px auto}
:root[data-theme=folha] header,:root[data-theme=folha] .masthead{
  border-bottom:3px double var(--text)}

/* --- ASSINATURA: sumario numerado como navegacao -------------------------- */
:root[data-theme=folha] .toc{display:flex;flex-wrap:wrap;gap:0;
  background:var(--surface-2);border-bottom:1px solid var(--border-strong)}
:root[data-theme=folha] .toc>a,:root[data-theme=folha] .toc>span{
  display:inline-flex;align-items:baseline;gap:7px;padding:7px var(--s3);
  text-decoration:none;color:var(--text-2);font-size:var(--fs-3);
  border-right:1px solid var(--border)}
:root[data-theme=folha] .toc .n{font:400 var(--fs-1) var(--mono);color:var(--text-3)}
:root[data-theme=folha] .toc>a:hover{background:var(--surface-3);color:var(--text)}
:root[data-theme=folha] .toc>a.on{background:var(--surface-3);color:var(--text);
  font-weight:var(--w-bold);box-shadow:inset 0 -3px 0 var(--accent)}
:root[data-theme=folha] .toc>span.off{color:var(--text-3);opacity:.6}

/* --- ASSINATURA: tabela de referencia no menu ----------------------------- */
:root[data-theme=folha] td.tool a{font-weight:var(--w-bold);text-decoration:none}
:root[data-theme=folha] td.tool a:hover{text-decoration:underline}

/* --- os grupos: titulo de secao no sumario e no corpo --------------------- */
:root[data-theme=folha] .toc .grp{font:var(--w-label) var(--fs-1) var(--mono);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-3);
  padding:3px var(--s2) 3px 0;white-space:nowrap}
/* O `eats` encosta na direita do proprio titulo, entao o h2 vira faixa. */
:root[data-theme=folha] h2{display:flex;flex-wrap:wrap;align-items:baseline;
  gap:var(--s2)}
:root[data-theme=folha] h2 .eats{margin-left:auto;
  font:400 var(--fs-1) var(--mono);color:var(--text-3);
  letter-spacing:normal;text-transform:none}
:root[data-theme=folha] .empty{padding:var(--pad-cell);font-size:var(--fs-3);
  color:var(--text-3);border:1px dashed var(--border);
  background:var(--surface-2)}
"""


def nav(active: str = "") -> str:
    """The contents: Menu, the project files, and the nine tools.

    A data sheet's contents lists SECTION TITLES, so the group name comes
    before each run. The tool's label is bare text inside the `<a>`, with no
    `<span class=lbl>`: Folha has no subtitle.

    The files carry "A" rather than a number: the tools stay numbered 1 to 9,
    and the home counts them in that same order.
    """
    out = ['<nav class="toc" aria-label="Ferramentas">']
    key, href, name, _short, _hint = MENU_ITEM
    out.append(_link(key == active, href, 0, name))
    fkey, fhref, fname, _fshort, _fhint = FILES_ITEM
    out.append(_link(fkey == active, fhref, "A", fname))
    for g in GROUPS:
        out.append(f'  <span class="grp">{g.name}</span>')
        group_tools = tools_of(g.key)
        if not group_tools:
            out.append('  <span class="off">reservado</span>')
            continue
        for t in group_tools:
            out.append(_link(t.key == active, t.href, ORDINAL[t.key], t.name))
    out.append("</nav>")
    return "\n".join(out)


def _link(on: bool, href: str | None, n: int | str, label: str) -> str:
    inner = f'<span class="n">{n}</span>{label}'
    if href is None:
        return f'  <span class="off">{inner}</span>'
    if on:
        return f'  <a class="on" href="{href}" aria-current="page">{inner}</a>'
    return f'  <a href="{href}">{inner}</a>'


def home() -> str:
    """The menu as a reference table, one numbered section per group.

    The Ref. column becomes `<section>.<row>`, which is how a real data sheet
    numbers. The global ordinal (1..9) is still what Régua and Caderno print;
    the data offers both and each direction chooses.
    """
    sections = []
    for i, g in enumerate(GROUPS, start=1):
        group_tools = tools_of(g.key)
        head = (f'      <h2><span class="num">{i}.</span>{g.name}'
                f'<span class="eats">entrada: {g.eats}</span></h2>')
        if not group_tools:
            sections.append(head + f'\n      <div class="empty">{g.empty}</div>')
            continue
        rows = []
        for j, t in enumerate(group_tools, start=1):
            ref = f'<sup class="ref">{t.note}</sup>' if t.note else ""
            name = f'<a href="{t.href}">{t.name}</a>' if t.shipping else t.name
            status = ('<span class="j j-ok">Disponível</span>' if t.shipping
                      else '<span class="j j-falta">Em breve</span>')
            rows.append(
                f'            <tr><td class="var">{i}.{j}</td>\n'
                f'              <td class="txt tool">{name}</td>\n'
                f'              <td class="txt">{t.does}{ref}</td>\n'
                f'              <td class="val">{t.takes}</td>\n'
                f'              <td>{status}</td></tr>')
        sections.append(
            head + '\n      <div class="wrap">\n        <table>\n'
            '          <thead><tr><th>Ref.</th><th>Ferramenta</th>'
            '<th>Função</th>\n'
            '            <th>Entrada</th><th>Estado</th></tr></thead>\n'
            '          <tbody>\n'
            + "\n".join(rows)
            + '\n          </tbody>\n        </table>\n      </div>')
    notes = "\n".join(
        f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
        for i, txt in enumerate(NOTES, start=1))
    generic = len(tools_of("geral"))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <p class="lead">{SHIPPING} ferramentas disponíveis nesta instalação, em
      {len(GROUPS)} seções. {generic} servem qualquer relé, porque leem SCD;
      as outras {len(TOOLS) - generic} pedem o RDB do AcSELerator QuickSet.
      Cada uma abre na própria aba e todas ficam no ar ao mesmo tempo; só o
      Visualizador de Lógica conversa com o relé. Os RDB e SCD do projeto
      entram uma vez em <a href="/files/">Arquivos do Projeto</a>; cada
      ferramenta escolhe dali.</p>
{chr(10).join(sections)}
      <div class="totals">
        <span><b>{SHIPPING}</b> no ar</span>
        <span><b>{PLANNED}</b> em desenvolvimento</span>
        <span>uploads isolados por visitante<sup class="ref">2</sup></span>
      </div>
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notes}
  </aside>
</div>"""
