"""Caderno de Campo -- the commissioning engineer's notebook, as a language.

5 mm graph paper, a sheet held by dividers, a verdict stamped on it. The
direction's signature is the **dividers** (`.tabs`/`.tab`, 00, A and 01-09,
with group labels between the runs and a short label on each), the **sheet**
held beneath them (`.sheet-body`) and, in the menu, the **clipped index
cards** (`.card` + `::before`), each with its status label on top.

Reference: `mockups/10-caderno/`.
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
:root[data-theme=caderno] .page{padding:0 var(--pad-main) 70px;background:transparent;
  box-shadow:none}
/* O cabecalho e' escrito no proprio papel: sem fundo, sem sombra e sem recuo
   lateral, pra alinhar com as divisorias e com a folha logo abaixo. Escopado em
   `.page>` de proposito -- o visualizador do GLV
   (`glv/templates/dashboard.html`) tem um <header> de aplicacao, fora de
   `.page`, que mantem a barra propria. */
:root[data-theme=caderno] .page>header,:root[data-theme=caderno] .masthead{
  background:transparent;box-shadow:none;padding:var(--s4) 0 10px;
  align-items:flex-end}
:root[data-theme=caderno] .page>header h1,:root[data-theme=caderno] .masthead h1{
  font-weight:700;letter-spacing:-.02em}

/* --- ASSINATURA: divisorias de caderno como navegacao --------------------- */
:root[data-theme=caderno] nav.tabs{display:flex;flex-wrap:wrap;gap:3px;
  background:transparent;margin:var(--s4) 0 -1px;position:relative;z-index:2}
:root[data-theme=caderno] nav.tabs .tab{display:inline-flex;align-items:center;gap:8px;
  background:var(--surface-2);border:1px solid var(--border);
  padding:8px var(--s3);text-decoration:none;color:var(--text-2);
  font:700 12px var(--sans);box-shadow:1px -1px 0 rgba(27,42,58,.06)}
:root[data-theme=caderno] nav.tabs .tab .n{font-family:var(--mono);font-size:11px;
  color:var(--text-3)}
:root[data-theme=caderno] nav.tabs a.tab:hover{color:var(--text);background:var(--surface)}
:root[data-theme=caderno] nav.tabs .tab.on{background:var(--surface);color:var(--text);
  border-color:var(--text);box-shadow:1px -2px 0 rgba(27,42,58,.12)}
:root[data-theme=caderno] nav.tabs .tab.off{color:var(--text-3);border-style:dashed;
  pointer-events:none}
/* Ao contrario do mockup, toda divisoria tem a base FECHADA: sao nove
   ferramentas, que quebram em duas ou tres fileiras, e a base aberta que a
   fileira unica do mockup podia bancar deixaria abas penduradas sobre a
   fileira de baixo. O -1px da tira enfia o filete da ultima fileira embaixo da
   regua de tinta da folha, que e' de onde vem a leitura de "preso no caderno". */

/* --- ASSINATURA: a folha presa sob as divisorias -------------------------- */
/* `.grid` e' a folha: nas oito telas de documento ele e' o irmao logo depois da
   tira, e o conteudo dentro dele ja traz o proprio padding. Coluna unica: as
   notas caem dentro da folha, embaixo do conteudo. */
:root[data-theme=caderno] .grid{grid-template-columns:minmax(0,1fr);
  background:var(--surface);border:1px solid var(--border);
  border-top:2px solid var(--text);box-shadow:1px 3px 0 rgba(27,42,58,.10)}

/* --- ASSINATURA: fichas presas por um clipe ------------------------------- */
:root[data-theme=caderno] .cards{display:grid;gap:var(--s4) var(--s3);
  grid-template-columns:repeat(auto-fill,minmax(244px,1fr))}
:root[data-theme=caderno] .cards .card{background:var(--surface);
  border:1px solid var(--border);padding:15px 16px 14px;position:relative;
  box-shadow:1px 2px 0 rgba(27,42,58,.10);display:block;
  text-decoration:none;color:inherit}
:root[data-theme=caderno] .cards .card::before{content:"";position:absolute;top:-7px;
  left:50%;transform:translateX(-50%);width:44px;height:12px;background:#8a939b;
  opacity:.32;border-radius:2px}
:root[data-theme=caderno] .cards a.card:hover{background:var(--surface);
  box-shadow:2px 4px 0 rgba(27,42,58,.16)}
:root[data-theme=caderno] .cards .card h3{margin:6px 0 4px;font:700 15.5px/1.25 var(--sans)}
:root[data-theme=caderno] .cards .card p{margin:0;color:var(--text-2);font-size:12.5px}
:root[data-theme=caderno] .cards .card .tag{display:block;margin-top:0;
  font:700 10px var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--ok)}
:root[data-theme=caderno] .cards .card.off{opacity:.62}
:root[data-theme=caderno] .cards .card.off .tag{color:var(--text-2)}

:root[data-theme=caderno] th{background:var(--surface);border-bottom:2px solid var(--text)}
:root[data-theme=caderno] h2{border-bottom:0}
:root[data-theme=caderno] .btn:active{transform:translate(1px,1px);box-shadow:none}
/* O carimbo de veredito. */
:root[data-theme=caderno] .j{font-family:var(--mono);font-size:11.5px;letter-spacing:.13em;
  border:2.5px solid currentColor;border-radius:3px;padding:2px 8px;
  transform:rotate(-4deg);opacity:.88}
:root[data-theme=caderno] .j-falta,:root[data-theme=caderno] .j-none{border-style:dashed}
/* A coluna de margem vira uma linha a mao embaixo do conteudo, com o mesmo
   recuo lateral de `.col-main` pra ficar dentro da folha. */
:root[data-theme=caderno] .grid>.col-main,:root[data-theme=caderno] .grid>main{
  padding-bottom:var(--s3)}
:root[data-theme=caderno] .col-notes{border-left:0;background:transparent;
  padding:0 var(--pad-main) var(--s4)}
:root[data-theme=caderno] .col-notes .cap{display:none}
:root[data-theme=caderno] .note{font-family:var(--mono);color:var(--err);
  padding-left:20px;margin-bottom:var(--s2)}
:root[data-theme=caderno] .note .n{display:none}
:root[data-theme=caderno] .note::before{content:"\21B3";position:absolute;left:2px;
  color:var(--err)}
:root[data-theme=caderno] .note b{color:var(--err)}
@media (prefers-reduced-motion:reduce){
  :root[data-theme=caderno] .btn:active{transform:none}
}
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
:root[data-theme=caderno] .grp .eats{font:400 var(--fs-2) var(--mono);
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
"""


def nav(active: str = "") -> str:
    """The dividers: a short label and a two-digit number, as in the mockup.

    Groups appear as a label between runs of dividers -- which is what a
    notebook divider does. The project files carry "A" instead of a number:
    the tools stay 01..09, which is the order the home counts in.
    """
    out = ['<nav class="tabs" aria-label="Ferramentas">']
    key, href, _name, short, _hint = MENU_ITEM
    out.append(_tab(key == active, href, "00", short))
    fkey, fhref, _fname, fshort, _fhint = FILES_ITEM
    out.append(_tab(fkey == active, fhref, "A", fshort))
    for g in GROUPS:
        out.append(f'  <span class="tabsep">{g.short}</span>')
        group_tools = tools_of(g.key)
        if not group_tools:
            out.append('  <span class="tab off"><span class="n">&mdash;</span>'
                       'reservado</span>')
            continue
        for t in group_tools:
            out.append(_tab(t.key == active, t.href,
                            f"{ORDINAL[t.key]:02d}", t.short))
    out.append("</nav>")
    return "\n".join(out)


def _tab(on: bool, href: str | None, n: str, label: str) -> str:
    inner = f'<span class="n">{n}</span>{label}'
    if href is None:
        return f'  <span class="tab off">{inner}</span>'
    if on:
        return (f'  <a class="tab on" href="{href}" '
                f'aria-current="page">{inner}</a>')
    return f'  <a class="tab" href="{href}">{inner}</a>'


def home() -> str:
    """The menu as cards clipped to the sheet, grouped by manufacturer.

    A group with no tools becomes a reserved blank sheet, carrying the line
    that says what will land there: empty and silent is vapour, empty with a
    roadmap is a forecast.
    """
    blocks = []
    for g in GROUPS:
        group_tools = tools_of(g.key)
        if group_tools:
            shipping = sum(1 for t in group_tools if t.shipping)
            cnt = (f"{shipping} em uso &middot; "
                   f"{len(group_tools) - shipping} em breve")
        else:
            cnt = "reservado"
        blocks.append(f'      <div class="grp"><h3>{g.name}</h3>'
                      f'<span class="eats">{g.eats}</span>'
                      f'<span class="cnt">{cnt}</span></div>')
        if not group_tools:
            blocks.append('      <div class="blank">'
                          '<span class="t">folha em branco</span>'
                          f'<span class="d">{g.empty}</span></div>')
            continue
        cards = []
        for t in group_tools:
            if t.shipping:
                open_, close, tag = (f'<a class="card" href="{t.href}">',
                                     "</a>", "em uso")
            else:
                open_, close, tag = '<span class="card off">', "</span>", "em breve"
            cards.append(f'        {open_}<span class="tag">{tag}</span>'
                         f'<h3>{t.name}</h3>\n'
                         f'          <p>{t.does}</p>{close}')
        blocks.append('      <div class="cards">\n'
                      + "\n".join(cards) + '\n      </div>')
    notes = "\n".join(f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
                      for i, txt in enumerate(NOTES, start=1))
    generic = len(tools_of("geral"))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <h2>Ferramentas</h2>
      <p class="lead">{SHIPPING} em uso nesta instalação e {PLANNED} por fazer.
      {generic} servem qualquer relé — leem SCD; as outras
      {len(TOOLS) - generic} pedem o RDB do AcSELerator QuickSet. Os RDB e SCD
      do projeto entram uma vez em <a href="/files/">Arquivos do
      Projeto</a>; cada ficha escolhe dali. Só o Visualizador de Lógica
      conversa com o relé.</p>
{chr(10).join(blocks)}
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notes}
  </aside>
</div>"""
