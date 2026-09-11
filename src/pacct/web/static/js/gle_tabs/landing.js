// O dispatcher injeta um shim que reprefixa fetch(), entao as rotas sao
// escritas aqui como se esta ferramenta fosse a raiz. Um <a href> o shim NAO
// alcanca: link entre paginas e' relativo ("./editor", "../files/"), e o link
// de download ja vem prefixado pelo servidor.

// -- estado -----------------------------------------------------------------
let RDB = null;            // sha curto do RDB escolhido
// sha curto -> quantos GLEs daquele RDB estao pendentes, como o servidor conta.
const RDB_DIRTY = {};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

// Um badge com as classes da casca (.j/.j-warn/.j-err): a marca de veredito ja
// existe nos tres temas e nao precisa de cor propria.
function badge(on, cls, text) {
  const b = el('span', 'j ' + cls, text);
  b.hidden = !on;
  return b;
}

// -- passo 1: os RDBs do projeto --------------------------------------------
// A lista e' o `SelLibrary.picker`, o mesmo das outras seis ferramentas: nome,
// tamanho, `detail`, `origin`, sha curto, a marca da linha escolhida e o estado
// vazio com o link relativo para "Arquivos do Projeto" ja saem dele. O unico
// pedaco que e' desta ferramenta -- quantos GLEs daquele RDB estao com edicao
// pendente -- entra pelo `annotate`, que existe exatamente para isso (o Editor
// de Mapa DNP e' o precedente).
const PICKER = SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  annotate: (f) => (RDB_DIRTY[f.short_sha] > 0 ? 'modificado' : ''),
  onPick: (f) => pickRdb(f.short_sha),
});

// `annotate` so' roda no render, entao a lista e' redesenhada quando o SINAL
// muda -- quais RDBs tem pendencia --, e nunca a toa.
let PICKER_SIG = '';
const dirtySig = () => Object.keys(RDB_DIRTY)
  .filter(k => RDB_DIRTY[k] > 0).sort().join(',');

function syncPicker() {
  if (dirtySig() === PICKER_SIG) return;
  PICKER_SIG = dirtySig();
  PICKER.refresh().then(() => { if (RDB) PICKER.select(RDB); });
}

// `/rdbs` continua existindo por causa do `annotate`: a contagem por RDB e' a
// unica coisa que esta ferramenta sabe sobre um arquivo e o acervo nao.
async function loadRdbs() {
  let out = null;
  try { out = await (await fetch('/rdbs')).json(); } catch (e) { out = null; }
  for (const k of Object.keys(RDB_DIRTY)) delete RDB_DIRTY[k];
  for (const r of ((out && out.rdbs) || [])) RDB_DIRTY[r.rdb] = r.dirty || 0;
  PICKER_SIG = dirtySig();
  await PICKER.refresh();
  if (RDB) PICKER.select(RDB);
  paintDirty();
}

function pickRdb(key) {
  RDB = key;
  document.getElementById('out').textContent = '';
  document.getElementById('gerar-err').textContent = '';
  loadGles();
}

// -- passo 2: os reles e seus GLEs ------------------------------------------
// Escolher um GLE NAO edita aqui: leva para `./editor`, uma pagina so' daquele
// arquivo. E' a forma do Mapa DNP, e ela resolve sozinha o que na tela unica
// precisava de cuidado -- a marca de "nao salvo" era global mas a mensagem era
// de uma tela so', e "Gerar" convivia com encenacoes no ar.
async function loadGles() {
  const box = document.getElementById('gle-list');
  document.getElementById('step-gle').hidden = false;
  box.textContent = 'Carregando...';
  let out = null;
  try {
    out = await (await fetch('/gles?rdb=' + encodeURIComponent(RDB))).json();
  } catch (e) { out = null; }
  if (!out || !out.ok) {
    box.textContent = (out && out.error) || 'Falha ao ler o RDB.';
    RDB_DIRTY[RDB] = 0;
    paintDirty();
    return;
  }
  box.textContent = '';
  let gles = 0, dirty = 0;
  for (const relay of out.relays) {
    const group = el('div', 'relay list');
    const h = el('h3', null, relay.relay);
    if (relay.model) h.appendChild(el('span', 'meta', ' · ' + relay.model));
    group.appendChild(h);
    if (!relay.gles.length) group.appendChild(el('div', 'lbl', 'sem GLE'));
    for (const g of relay.gles) {
      gles += 1;
      if (g.dirty) dirty += 1;
      const href = './editor?rdb=' + encodeURIComponent(RDB) +
                   '&relay=' + encodeURIComponent(relay.relay) +
                   '&gle=' + encodeURIComponent(g.gle);
      // Um GLE que nao abre nao e' escolhivel: o editor nao teria o que
      // mostrar, e o servidor recusaria de todo jeito.
      const row = el(g.pages == null ? 'span' : 'a', 'row');
      row.appendChild(el('span', 'nm', g.gle));
      if (g.pages == null) {
        row.appendChild(el('span', 'meta', 'ilegível'));
        row.title = 'Este GLE não pôde ser lido.';
      } else {
        row.appendChild(el('span', 'meta', g.pages + ' aba(s)'));
        row.href = href;
      }
      row.appendChild(badge(!!g.dirty, 'j-warn', 'modificado'));
      group.appendChild(row);
    }
    box.appendChild(group);
  }
  document.getElementById('gle-summary').textContent =
    out.relays.length + ' relé(s), ' + gles + ' GLE(s).';
  RDB_DIRTY[RDB] = dirty;
  paintDirty();
}

function paintDirty() {
  let total = 0;
  for (const k of Object.keys(RDB_DIRTY)) total += RDB_DIRTY[k];
  document.getElementById('dirty').textContent = total
    ? total + ' GLE(s) com edição pendente'
    : 'sem edições pendentes';
  const here = RDB ? (RDB_DIRTY[RDB] || 0) : 0;
  document.getElementById('pending').hidden = !here;
  document.getElementById('pending-list').textContent = here
    ? here + ' GLE(s) deste RDB com abas movidas ou renomeadas.' : '';
  syncPicker();
}

// -- Gerar RDB --------------------------------------------------------------
// Sem drenagem aqui: nao ha `/stage` nesta pagina. O que esta encenado e' o que
// o servidor diz que esta -- `/gles` acabou de ser lido dele --, entao gerar
// daqui nunca escreve uma ordem que a tela nao tenha visto.
document.getElementById('gerar').onclick = async () => {
  const out = document.getElementById('out');
  const err = document.getElementById('gerar-err');
  const btn = document.getElementById('gerar');
  // O RDB e' preso no clique: o acervo do passo 1 continua clicavel enquanto a
  // requisicao esta no ar, e ler a global na volta geraria a partir do arquivo
  // ERRADO, com mensagem de sucesso e link de download.
  const rdb = RDB;
  if (!rdb || !(RDB_DIRTY[rdb] > 0)) return;
  btn.disabled = true;
  out.textContent = '';
  err.textContent = '';
  try {
    const r = await SelProgress.post('/gerar', {rdb: rdb},
                                     {label: 'Gerando RDB'});
    const d = r.data || {};
    if (!r.ok || !d.ok) {
      err.textContent = 'Falha ao gerar: ' + (d.error || r.status);
      return;
    }
    const a = el('a', null, 'Baixar RDB (' +
      (d.method === 'rebuild' ? 'reconstruído' : 'gravado no lugar') + ')');
    // Ja vem com o prefixo do mount: um <a href> o shim de fetch nao reescreve.
    a.href = d.download;
    a.setAttribute('download', '');
    out.appendChild(a);
    const t = d.totals || {};
    out.appendChild(el('span', 'hint',
      (d.results || []).length + ' GLE(s), ' + (t.moved || 0) +
      ' aba(s) movida(s), ' + (t.renamed || 0) + ' renomeada(s).'));
    const note = el('div');
    // Escrito pelo runtime SelLibrary, que ja escapa o que vem do arquivo.
    note.innerHTML = SelLibrary.savedNote(d.project_file);
    out.appendChild(note);
    if (!d.project_file) {
      out.appendChild(el('div', 'hint',
        'O arquivo não entrou no acervo do projeto; o link acima ' +
        'continua valendo.'));
    }
    // O RDB gerado entra no acervo, entao a lista do passo 1 e' relida para
    // mostra-lo. As edicoes seguem pendentes ate que se desfaca.
    await loadRdbs();
  } finally {
    btn.disabled = false;
  }
};

loadRdbs();
