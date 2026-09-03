"""Régua de Bornes -- a régua do painel como linguagem.

Escuro, industrial, cor = cor de fio. A assinatura da direcao e' a **régua
vertical** (`.strip` de `.borne`, cada um com o numero em bloco cor de borne e
uma cabeca de parafuso), e no menu as **fichas com o topo na cor do fio**
daquele sinal, cada uma anunciando o borne que ocupa.

Referencia: `mockups/03-regua/`. Diferente da folha e do caderno, a regua nao
tem item "Menu" na régua: a volta pra home e' o "← Menu" da barra superior.
"""

from __future__ import annotations

from pacct.web.themes.items import (
    FILES_ITEM,
    GROUPS,
    NOTES,
    ORDINAL,
    PLANNED,
    SHIPPING,
    tools_of,
)

# A ordem das cores de fio segue a ordem dos bornes, como no mockup: o borne 1
# fica no azul padrao da `.card` e os seguintes recebem a cor do proprio sinal.
_WIRES = ["", "w-green", "w-red", "w-yellow", "w-violet"]

DELTA_CSS = r"""
:root[data-theme=regua] .page{margin:0}
:root[data-theme=regua] header,:root[data-theme=regua] .masthead{
  background:linear-gradient(180deg,#242220,#1b1a18);position:sticky;top:0;z-index:20;
  padding:var(--s3) var(--s5)}
:root[data-theme=regua] header h1,:root[data-theme=regua] .masthead h1{
  font:700 var(--fs-5)/1 var(--cond);text-transform:uppercase;
  letter-spacing:.1em;white-space:nowrap}

/* --- ASSINATURA: a régua vertical como navegacao -------------------------- */
:root[data-theme=regua] .shell{grid-template-columns:var(--nav-w) minmax(0,1fr)}
:root[data-theme=regua] .strip{display:flex;flex-direction:column;
  background:var(--surface);border-right:2px solid var(--border);
  padding:var(--s3) 0 40px;position:sticky;top:47px;
  max-height:calc(100vh - 47px);overflow-y:auto}
:root[data-theme=regua] .strip .cap{font:var(--w-label) var(--fs-1) var(--cond);
  letter-spacing:var(--track-wide);color:var(--text-2);text-transform:uppercase;
  padding:0 var(--s3) var(--s2)}
:root[data-theme=regua] .borne{display:flex;align-items:stretch;
  text-decoration:none;color:inherit}
:root[data-theme=regua] .borne .num{width:32px;flex:none;background:#d9d2c3;
  color:#26241f;font:700 12px/1 var(--mono);display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:6px;
  border-bottom:1px solid #b8b0a0;box-shadow:inset -2px 0 3px rgba(0,0,0,.25)}
/* A cabeca do parafuso. So decoracao: CSS puro, sem marcacao a mais. */
:root[data-theme=regua] .borne .num::after{content:"";width:12px;height:12px;
  border-radius:50%;
  background:linear-gradient(90deg,transparent 44%,#6f695e 44% 56%,transparent 56%),
             radial-gradient(circle at 35% 30%,#f5f0e6,#8f887a 72%);
  box-shadow:inset 0 0 0 1px #6f695e}
:root[data-theme=regua] .borne .lbl{flex:1;padding:11px var(--s3) 11px 14px;
  border-bottom:1px solid var(--surface-2);font-size:13.5px}
:root[data-theme=regua] .borne .lbl small{display:block;color:var(--text-2);
  font-size:var(--fs-2);margin-top:1px}
:root[data-theme=regua] .borne:hover .lbl{background:var(--surface-2)}
:root[data-theme=regua] .borne.on .lbl{background:var(--surface-2);
  box-shadow:inset 3px 0 0 var(--accent)}
:root[data-theme=regua] .borne.on .num{background:#fff}
:root[data-theme=regua] .borne.off .lbl{color:var(--text-3)}
:root[data-theme=regua] .borne.off .lbl small{color:var(--text-3)}
:root[data-theme=regua] .borne.off .num{background:#b8b0a0;color:#57534c}

/* --- ASSINATURA: fichas com o topo na cor do fio -------------------------- */
:root[data-theme=regua] .cards{display:grid;gap:var(--s3);
  grid-template-columns:repeat(auto-fill,minmax(248px,1fr))}
:root[data-theme=regua] .cards .card{background:var(--surface);
  border:1px solid var(--border);border-top:3px solid var(--vb);
  padding:13px 15px;box-shadow:none;text-decoration:none;color:inherit;display:block}
:root[data-theme=regua] .cards .card:hover{background:var(--surface-2);box-shadow:none}
:root[data-theme=regua] .cards .card h3{margin:0 0 var(--s1);font:500 15px/1.2 var(--sans)}
:root[data-theme=regua] .cards .card p{margin:0;font-size:var(--fs-3);color:var(--text-2)}
:root[data-theme=regua] .cards .card .st{display:block;margin-top:var(--s2);
  font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track);
  text-transform:uppercase;color:var(--ok)}
:root[data-theme=regua] .cards .card.w-green{border-top-color:var(--ok)}
:root[data-theme=regua] .cards .card.w-red{border-top-color:var(--err)}
:root[data-theme=regua] .cards .card.w-yellow{border-top-color:var(--warn)}
:root[data-theme=regua] .cards .card.w-violet{border-top-color:var(--displaced)}
:root[data-theme=regua] .cards .card.off{border-top-color:var(--text-3);opacity:1}
:root[data-theme=regua] .cards .card.off h3,:root[data-theme=regua] .cards .card.off p,
:root[data-theme=regua] .cards .card.off .st{color:var(--text-3)}

/* Filete de secao vira ponta de fio. */
:root[data-theme=regua] h2{border-bottom:0;border-left:3px solid var(--accent);
  padding:0 0 0 var(--s3);margin-bottom:var(--s3)}
:root[data-theme=regua] th{background:#26241f;color:#b9b2a5}
/* Veredito ganha a pastilha do fio. */
:root[data-theme=regua] .j{display:inline-flex;align-items:center;gap:6px}
:root[data-theme=regua] .j::before{content:"";width:9px;height:9px;background:currentColor;
  flex:none}
:root[data-theme=regua] .j-falta::before,:root[data-theme=regua] .j-none::before{
  background:transparent;box-shadow:inset 0 0 0 1px currentColor}
/* Sem coluna de margem: a régua ja gastou o orcamento horizontal. As notas
   caem no pe da pagina, no registro da footbar. */
:root[data-theme=regua] .grid{grid-template-columns:minmax(0,1fr)}
:root[data-theme=regua] .col-notes{border-left:0;border-top:1px solid var(--border);
  background:var(--surface);padding:var(--s3) var(--s4) var(--s4);
  display:flex;flex-wrap:wrap;gap:var(--s4)}
:root[data-theme=regua] .col-notes .cap{width:100%;margin-bottom:0}
:root[data-theme=regua] .note{flex:1 1 260px;margin-bottom:0;font-size:var(--fs-2)}
@media (max-width:860px){
  :root[data-theme=regua] .shell{grid-template-columns:1fr}
  :root[data-theme=regua] .strip{position:static;max-height:none;border-right:0;
    border-bottom:2px solid var(--border)}
}
/* --- os grupos: uma capa por régua, um filete de latao na home ------------ */
:root[data-theme=regua] .strip .cap.later{margin-top:var(--s3);
  padding-top:var(--s3);border-top:1px solid var(--border)}
:root[data-theme=regua] .grp{display:flex;flex-wrap:wrap;align-items:baseline;
  gap:var(--s3);margin:var(--s4) 0 var(--s3)}
:root[data-theme=regua] .grp:first-of-type{margin-top:0}
:root[data-theme=regua] .grp h3{margin:0;
  font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track-wide);
  text-transform:uppercase;color:var(--accent)}
:root[data-theme=regua] .grp .eats{font-size:var(--fs-2);color:var(--text-3)}
:root[data-theme=regua] .grp .ln{flex:1;min-width:var(--s5);height:1px;
  background:var(--border)}
"""


def nav(active: str = "") -> str:
    """A régua: um borne por ferramenta, agrupados por fabricante.

    Cada grupo e' uma régua com a sua capa (X1..X4); a X0 e' a entrada, e ela
    existe porque o borne "A" (Arquivos do Projeto) ficaria parecendo do
    primeiro grupo assim que os grupos ganharam capa. A regua nao tem borne de
    Menu -- a volta pra home e' o "← Menu" da barra superior.

    Um grupo sem ferramenta e' um borne de reserva: numa régua de verdade isso
    nao e' defeito, e' previsao.
    """
    fkey, fhref, fname, _fshort, fhint = FILES_ITEM
    out = ['<nav class="strip" aria-label="Ferramentas">',
           '  <div class="cap">Régua X0 &mdash; entrada</div>',
           _borne(fkey == active, fhref, "A", fname, fhint)]
    for i, g in enumerate(GROUPS, start=1):
        out.append(f'  <div class="cap later">Régua X{i} &mdash; {g.short}</div>')
        group_tools = tools_of(g.key)
        if not group_tools:
            out.append(_borne(False, None, "&mdash;", "reserva", g.eats))
            continue
        for t in group_tools:
            out.append(_borne(t.key == active, t.href, str(ORDINAL[t.key]),
                              t.name, t.hint))
    out.append("</nav>")
    return "\n".join(out)


def _borne(on: bool, href: str | None, n: str, label: str, hint: str) -> str:
    inner = (f'<span class="num">{n}</span>'
             f'<span class="lbl">{label}<small>{hint}</small></span>')
    if href is None:
        return f'  <span class="borne off">{inner}</span>'
    if on:
        return (f'  <a class="borne on" href="{href}" '
                f'aria-current="page">{inner}</a>')
    return f'  <a class="borne" href="{href}">{inner}</a>'


def home() -> str:
    """O menu como fichas de borne, agrupadas, cada uma com a cor do fio."""
    blocks = []
    for g in GROUPS:
        group_tools = tools_of(g.key)
        blocks.append(f'      <div class="grp"><h3>{g.name}</h3>'
                      f'<span class="eats">{g.eats}</span>'
                      f'<span class="ln"></span></div>')
        if not group_tools:
            blocks.append('      <div class="cards">\n'
                          '        <span class="card off">'
                          '<h3>Régua de reserva</h3>\n'
                          f'          <p>{g.empty}</p>'
                          '<span class="st">sem fio</span></span>\n'
                          '      </div>')
            continue
        cards = []
        for t in group_tools:
            i = ORDINAL[t.key]
            if t.shipping:
                wire = _WIRES[(i - 1) % len(_WIRES)]
                cls = f"card {wire}".strip()
                open_, close = f'<a class="{cls}" href="{t.href}">', "</a>"
                st = f"Borne {i} &middot; ligado"
            else:
                open_, close = '<span class="card off">', "</span>"
                st = f"Borne {i} &middot; em breve"
            cards.append(f'        {open_}<h3>{t.name}</h3>\n'
                         f'          <p>{t.does}</p>'
                         f'<span class="st">{st}</span>{close}')
        blocks.append('      <div class="cards">\n'
                      + "\n".join(cards) + '\n      </div>')
    notes = "\n".join(f'    <div class="note"><span class="n">{i}</span>{txt}</div>'
                      for i, txt in enumerate(NOTES, start=1))
    unwired = sum(1 for g in GROUPS if not tools_of(g.key))
    return f"""<div class="grid">
  <div class="col-main">
    <section>
      <h2>Ferramentas ligadas</h2>
      <p class="lead">{SHIPPING} bornes energizados nesta instalação, {PLANNED}
      de reserva e {unwired} réguas ainda sem fio. Os RDB e SCD do projeto
      entram uma vez no borne <a href="/files/">A &mdash; Arquivos do
      Projeto</a>; cada borne escolhe dali. Só o borne {ORDINAL["glv"]}
      conversa com o relé pela rede.</p>
{chr(10).join(blocks)}
    </section>
  </div>

  <aside class="col-notes">
    <div class="cap">Notas</div>
{notes}
  </aside>
</div>"""
