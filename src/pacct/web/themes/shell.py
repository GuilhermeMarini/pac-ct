"""What all three directions do identically.

The admission rule for this file: a rule belongs here only if all three
mockups (`mockups/06-folha/`, `mockups/03-regua/`, `mockups/10-caderno/`)
write it the same way, up to tokens. Anything a direction draws its own way --
the navigation, the menu, the screen's wrapper, the verdict mark -- lives in
that direction's file, next to the markup that emits it.

Too much living here is what flattened the screens into one another: the
navigation was a single list painted three ways, when the mockups ask for
`.toc`, `.strip/.borne` and `.tabs/.tab` -- different structures, not one
structure in another colour.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Shared component layer
# -----------------------------------------------------------------------------
#
# One markup vocabulary, three themes painting it. Anything that would need
# different HTML per theme is out: navigation is a numbered list whose
# orientation comes from [data-theme]; the notes column is an <aside> on every
# screen; a verdict is always <span class="j j-ok">.

SHELL = r"""
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--text);background-color:var(--bg);
  background-image:var(--bg-image);background-size:var(--bg-size);
  font:var(--fs-4)/var(--lh) var(--sans)}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
::selection{background:var(--accent);color:var(--accent-fg)}
hr{border:0;border-top:1px solid var(--border);margin:var(--s4) 0}
code,kbd,samp,pre{font-family:var(--mono)}

/* --- folha: a page is a sheet on a desk; regua: full bleed ---------------- */
.page{max-width:var(--page-max);margin:0 auto;background:var(--surface);
  box-shadow:var(--shadow-page)}

/* --- document header ------------------------------------------------------ */
header,.masthead{display:flex;align-items:baseline;gap:var(--s3);flex-wrap:wrap;
  padding:var(--s3) var(--pad-main);border-bottom:2px solid var(--border-strong);
  background:var(--surface)}
header h1,.masthead h1{margin:0;font:var(--w-bold) var(--fs-5)/1.15 var(--sans);
  letter-spacing:-.015em}
header .sub,.masthead .sub{font-size:var(--fs-3);color:var(--text-2);margin-top:3px}
header .doc,.masthead .doc{font:400 var(--fs-2) var(--mono);color:var(--text-2);
  letter-spacing:.04em;text-align:right}
header .spacer,.masthead .spacer,.bar .spacer,.filebar .spacer{flex:1}
/* "Isto entrou no acervo do projeto" -- escrito por `SelLibrary.savedNote`,
   abaixo do link de download de qualquer ferramenta. */
.lib-note{margin-top:var(--s2);font-size:var(--fs-2);color:var(--text-2)}
.lib-note code{color:var(--text)}
.lnk{font:var(--w-label) var(--fs-2) var(--cond);text-decoration:none;color:var(--text);
  border:1px solid var(--border-ctl);padding:4px 10px;border-radius:var(--radius);
  white-space:nowrap}
.lnk:hover{background:var(--surface-2)}

/* --- body: main column + margin column ------------------------------------ */
.shell{display:grid;grid-template-columns:minmax(0,1fr);align-items:start}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 244px}
.col-main{padding:var(--s4) var(--pad-main) var(--s5);min-width:0}
.full{padding:var(--s4) var(--pad-main) var(--s5)}
main{padding:var(--s4) var(--pad-main) var(--s5);min-width:0}
.col-notes{border-left:1px solid var(--border);background:var(--surface-2);
  padding:var(--s4) 18px var(--s5)}
.col-notes .cap{font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track-wide);
  text-transform:uppercase;color:var(--text-2);margin-bottom:var(--s3)}
.note{font-size:var(--fs-2);line-height:1.55;color:var(--text-2);padding-left:20px;
  position:relative;margin-bottom:var(--s3)}
.note .n{position:absolute;left:0;top:0;font:var(--w-bold) var(--fs-1) var(--mono);
  color:var(--accent)}
.note b{color:var(--text);font-weight:var(--w-bold)}
sup.ref{font:var(--w-bold) 9px var(--sans);color:var(--accent);margin-left:3px;
  vertical-align:super}

h2{font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track-wide);
  text-transform:uppercase;color:var(--text-2);margin:0 0 var(--s2);
  padding-bottom:5px;border-bottom:1px solid var(--border-strong)}
h2 .num{color:var(--accent);margin-right:8px}
h2+.lead{margin:0 0 var(--s3);font-size:var(--fs-3);color:var(--text-2);max-width:66ch}
h3{font:var(--w-bold) var(--fs-4) var(--sans);margin:0 0 var(--s1)}
section+section{margin-top:var(--s5)}
.stack>*+*{margin-top:var(--s5)}

/* --- surfaces ------------------------------------------------------------- */
.panel{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);box-shadow:var(--shadow)}
.pad{padding:var(--s4)}
/* --- controls ------------------------------------------------------------- */
.btn,button.btn,a.btn{background:var(--surface);color:var(--text);
  border:1px solid var(--border-ctl);font:var(--w-label) var(--fs-2) var(--cond);
  letter-spacing:var(--track);text-transform:uppercase;padding:var(--pad-ctl);
  cursor:pointer;border-radius:var(--radius);box-shadow:var(--shadow);
  text-decoration:none;display:inline-block}
.btn:hover:not(:disabled){border-color:var(--accent-strong);background:var(--surface-2)}
.btn.pri{background:var(--accent-strong);border-color:var(--accent-strong);
  color:var(--accent-fg)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.bar{display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s3)}
input[type=search],input[type=text],input[type=number],select{
  background:var(--surface-3);
  border:1px solid var(--border-ctl);color:var(--text);font:inherit;
  font-size:var(--fs-3);padding:var(--pad-field);border-radius:var(--radius)}
/* Sem uma largura aqui, um campo de texto sai com o padrao do navegador
   (`size=20`), e o `box-sizing:border-box` desconta o padding DESSE espaco --
   o filtro do VLAN Mapper media 174px de conteudo para um placeholder de
   180px, ou seja, o campo comia o proprio texto de ajuda. 26ch cobre os
   placeholders que existem hoje ("Filtrar IED, IP ou VLAN...", 26
   caracteres). E' `width`, e nao `min-width`, de proposito: qualquer regra de
   classe ou id de uma ferramenta vence por especificidade (o campo de IP da
   GLV pede 141px e continua com 141px), enquanto um `min-width` daqui
   ATROPELARIA essas larguras. */
input[type=search],input[type=text]{width:26ch;max-width:100%}
input[type=checkbox],input[type=radio]{accent-color:var(--accent);width:15px;height:15px}
.count{font:400 var(--fs-2) var(--mono);color:var(--text-2)}
.chk{display:flex;align-items:center;gap:10px;padding:var(--pad-cell);
  border-bottom:1px solid var(--border-hair);cursor:pointer}
.chk:hover{background:var(--surface-2)}
.chk .nm{font:400 var(--fs-3) var(--mono)}
.chk .meta{margin-left:auto;font:400 var(--fs-2) var(--mono);color:var(--text-2)}

/* --- upload / file -------------------------------------------------------- */
.drop{display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:var(--s1);min-height:124px;padding:var(--s4);text-align:center;
  background:var(--surface-2);border:2px dashed var(--border-ctl);cursor:pointer;
  border-radius:var(--radius)}
.drop:hover{border-color:var(--accent-strong);background:var(--surface-3)}
.drop .ic{font:var(--w-bold) 17px var(--mono);color:var(--text-2)}
.drop b{font-weight:var(--w-bold)}
.drop small{color:var(--text-2);font-size:var(--fs-2)}
.filebar{display:flex;align-items:center;gap:var(--s3);flex-wrap:wrap;
  padding:var(--pad-cell);background:var(--surface);border:1px solid var(--border);
  border-left:3px solid var(--ok);font:400 var(--fs-3) var(--mono);
  box-shadow:var(--shadow)}
.filebar .hash{color:var(--text-3);font-size:var(--fs-2)}
/* Lista de arquivos do projeto (SelLibrary.picker). Escolher e' clicar numa
   linha: nao ha botao de confirmar, porque nao ha o que confirmar. */
.filelist{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:var(--s2)}
.filelist .lbl{font:var(--w-label) var(--fs-1) var(--cond);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-2);
  padding:0 var(--s2) var(--s1)}
.filelist .noitems{color:var(--text-2);font-size:var(--fs-2);padding:var(--s2)}
.filelist .more{display:inline-block;font-size:var(--fs-2);color:var(--accent);
  padding:var(--s2) var(--s2) 0}
.filerow{display:flex;align-items:baseline;gap:var(--s3);
  padding:var(--s1) var(--s2);border-radius:var(--radius);cursor:pointer;
  font:400 var(--fs-3) var(--mono)}
.filerow:hover{background:var(--surface-2)}
.filerow.sel{background:var(--tint-sel);box-shadow:inset 2px 0 0 var(--accent)}
.filerow .name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.filerow .meta{color:var(--text-2);font-size:var(--fs-2)}
.filerow .flag{color:var(--warn);font-size:var(--fs-2)}
.filerow .hash{color:var(--text-3);font-size:var(--fs-2)}
.steps{display:flex;gap:0;margin-bottom:var(--s3);border:1px solid var(--border-strong)}
.step{flex:1;padding:7px 12px;font:var(--w-label) var(--fs-1) var(--cond);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-2);
  text-align:center;text-decoration:none;border-right:1px solid var(--border)}
.step:last-child{border-right:0}
.step.on{background:var(--accent-strong);color:var(--accent-fg)}
.step.done{color:var(--ok);background:var(--surface-2)}

/* --- tables --------------------------------------------------------------- */
.wrap{overflow:auto;border:1px solid var(--border);background:var(--surface);
  box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:var(--fs-3)}
th{background:var(--surface-2);text-align:left;padding:var(--pad-cell);
  font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:var(--track);
  text-transform:uppercase;color:var(--text-2);
  border-bottom:2px solid var(--border-strong);position:sticky;top:0;z-index:2;
  white-space:nowrap}
td{padding:var(--pad-cell);border-bottom:1px solid var(--border-hair);
  font-family:var(--mono);font-size:var(--fs-3);white-space:nowrap;vertical-align:top}
td.txt{font-family:var(--sans);white-space:normal}
td.var{font-weight:var(--w-bold);color:var(--text)}
td.val{color:var(--text-val)}
tr:hover td{background:var(--surface-2)}
.cmt{display:block;color:var(--text-2);font-size:var(--fs-2);margin-top:2px}
.src{display:block;color:var(--text-3);font-size:var(--fs-1);margin-top:2px}
.totals,.footbar,.tot{display:flex;gap:var(--s4);flex-wrap:wrap;margin-top:10px;
  padding-top:8px;border-top:1px solid var(--border-strong);
  font:400 var(--fs-2) var(--mono);color:var(--text-2)}
.totals b,.footbar b,.tot b{color:var(--text);font-variant-numeric:tabular-nums}

/* Row tint by verdict: the comparator paints 659 rows, so the tint is a token
   per theme -- 10% of a saturated hue reads as a wash on paper. */
tr.var-row td{background:var(--row-tint,transparent)}
tr.var-row:hover td{background:var(--surface-2)}

/* --- verdict: one element, three paint jobs ------------------------------- */
/* Typographic label in folha, swatch in regua, rotated stamp in caderno. */
.j{font:var(--w-label) var(--fs-1) var(--cond);letter-spacing:.1em;
  text-transform:uppercase;white-space:nowrap;display:inline-block}
.j-ok{color:var(--ok)}
.j-equiv,.j-warn{color:var(--equiv)}
.j-comment{color:var(--comment)}
.j-displaced,.j-info{color:var(--displaced)}
.j-vb{color:var(--vb)}
.j-dif,.j-err{color:var(--err)}
.j-falta,.j-none{color:var(--missing)}

/* --- theme picker --------------------------------------------------------- */
.theme-picker{display:inline-flex;align-items:center;gap:0;margin-left:var(--s2)}
.theme-picker .lbl{font:var(--w-label) var(--fs-1) var(--cond);
  letter-spacing:var(--track);text-transform:uppercase;color:var(--text-3);
  margin-right:var(--s2)}
.theme-picker button{font:var(--w-label) var(--fs-1) var(--cond);
  letter-spacing:var(--track);text-transform:uppercase;padding:5px 10px;
  background:var(--surface);color:var(--text-2);border:1px solid var(--border-ctl);
  border-left-width:0;cursor:pointer;border-radius:0}
.theme-picker button:first-of-type{border-left-width:1px}
.theme-picker button:hover{color:var(--text);background:var(--surface-2)}
.theme-picker button[aria-pressed=true]{background:var(--accent-strong);
  color:var(--accent-fg);border-color:var(--accent-strong)}
.theme-picker button:focus-visible{outline:2px solid var(--focus);outline-offset:1px;
  position:relative;z-index:1}

@media (max-width:900px){
  .grid{grid-template-columns:minmax(0,1fr)}
  .col-notes{border-left:0;border-top:1px solid var(--border-strong)}
}
@media print{
  .nav,.theme-picker,.bar,.steps{display:none}
  body{background:#fff}
  .page{box-shadow:none;max-width:none}
}
"""
