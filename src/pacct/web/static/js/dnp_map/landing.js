let RDB = null;

// RDB-derived names (relay names, session names, relaytype, exported
// filenames) come from the uploaded file's OLE storage/content and are never
// sanitized server-side, so they must be escaped before reaching innerHTML.
// Same helpers as pacct/web/vb_updater.py, kept in sync on purpose.
function escapeHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

// Contagem de alteracoes pendentes por RDB, pendurada em cada linha da lista.
// E' a unica coisa que esta ferramenta sabe sobre um arquivo e o acervo nao.
let PENDING = {};

const PICKER = SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  annotate: (f) => PENDING[f.short_sha]
    ? PENDING[f.short_sha] + ' alteração(ões) pendente(s)' : '',
  // A chave curta do acervo E' a chave desta ferramenta: as duas sao o sha256
  // do conteudo. Nao ha nada a "carregar" antes -- `/relays` acha o RDB no
  // acervo do visitante sozinho.
  onPick: (f) => pickRdb(f.short_sha),
});

function showRelays(relays) {
  const tb = document.querySelector('#relays tbody');
  tb.innerHTML = '';
  document.getElementById('summary').textContent =
    relays.length + ' relé(s) com mapa DNP neste RDB.';
  for (const r of relays) {
    const tr = document.createElement('tr');
    const groups = r.groups.map(g => g.join('=')).join(' · ');
    tr.innerHTML =
      '<td>' + escapeHtml(r.name) + '</td>' +
      '<td class="relaytype">' + escapeHtml(r.relaytype || '—') + '</td>' +
      '<td>' + escapeHtml(groups) + '</td>' +
      '<td><a href="./editor?rdb=' + encodeURIComponent(RDB) +
      '&relay=' + encodeURIComponent(r.name) +
      '&d=' + encodeURIComponent(r.sessions[0]) + '">Editar</a></td>' +
      // Copiar leva o relé da linha como ORIGEM: e' desta linha que o mapa
      // sai, e a pagina de copia ja abre com ele escolhido.
      '<td><a href="./copiar?rdb=' + encodeURIComponent(RDB) +
      '&relay=' + encodeURIComponent(r.name) +
      '&d=' + encodeURIComponent(r.sessions[0]) + '">Copiar</a></td>';
    tb.appendChild(tr);
  }
  document.getElementById('copy-link').href =
    './copiar?rdb=' + encodeURIComponent(RDB);
  document.getElementById('relays-step').hidden = false;
}

// -- Restoring the session's RDBs -------------------------------------------
//
// The server has held every uploaded RDB since the upload (DnpMapState.rdbs,
// keyed by short sha); only the browser forgot, because the selection lived
// in the `RDB` variable above and that dies the moment the user navigates to
// the editor and back. So on load we ask which RDBs this session already has
// and restore the last-used one, and the editor's "← Relés" link comes back
// to a page that is still populated instead of an empty upload form.
//
// Which one was last used is per-tab UI state, not data: sessionStorage is
// exactly the right lifetime for it, and it degrades to "pick the newest"
// when unavailable (private window, storage blocked).
const PICK_KEY = 'dnp-map:rdb';

function rememberPick(key) {
  try { sessionStorage.setItem(PICK_KEY, key); } catch (e) { /* sem storage */ }
}
function rememberedPick() {
  try { return sessionStorage.getItem(PICK_KEY); } catch (e) { return null; }
}

async function loadRdbs() {
  let r;
  try {
    r = await (await fetch('/rdbs')).json();
  } catch (e) { return []; }
  const list = (r && r.rdbs) || [];
  PENDING = {};
  for (const item of list) {
    // Contagem de CAMPOS alterados, nao de reles: e' o mesmo numero que o
    // editor mostra na barra, e "1 pendente" ao lado de uma list que diz
    // "D1 (2)" se le como contradicao.
    const pending = (item.dirty || []).reduce(
      (n, d) => n + Object.values(d.sessions).reduce((a, b) => a + b, 0), 0);
    if (pending) PENDING[item.rdb] = pending;
  }
  return list;
}

async function pickRdb(key) {
  const r = await (await fetch('/relays?rdb=' + encodeURIComponent(key))).json();
  if (!r.ok) { alert(r.error || 'RDB não está mais no projeto.'); return; }
  RDB = key;
  rememberPick(key);
  showRelays(r.relays);
  await loadRdbs();
  PICKER.refresh().then(() => PICKER.select(key));
  await refreshPending();
}

async function refreshPending() {
  if (!RDB) { document.getElementById('pending').hidden = true; return; }
  const r = await (await fetch('/relays?rdb=' + encodeURIComponent(RDB))).json();
  const ul = document.getElementById('pending-list');
  ul.innerHTML = '';
  for (const d of (r.dirty || [])) {
    const li = document.createElement('li');
    const sess = Object.entries(d.sessions)
      .map(([k, v]) => k + ' (' + v + ')').join(', ');
    li.textContent = d.relay + ' — ' + sess;
    ul.appendChild(li);
  }
  document.getElementById('pending').hidden = (r.dirty || []).length === 0;
}

document.getElementById('export').onclick = async () => {
  const r = await SelProgress.post('/export', {rdb: RDB},
                                   {label: 'Exportando RDB'});
  const out = document.getElementById('out');
  const d = r.data || {};
  if (!r.ok) { out.textContent = 'Falha: ' + (d.error || r.status); return; }
  const links = [
    '<a href="' + escapeAttr(d.download_url) + '" download>Baixar RDB</a>',
  ].concat((d.txt_urls || []).map(
    u => '<a href="' + escapeAttr(u.url) + '" download>' +
         escapeHtml(u.name) + '</a>'));
  // O RDB reconstruido sai bem menor que o original -- e' esperado, nao e'
  // perda de dados (o servidor explica o motivo em `d.note`).
  let msg = 'Pronto (' +
    (d.method === 'rebuild' ? 'RDB reconstruído' : 'gravado no lugar') +
    '): ' + links.join(' · ');
  if (d.note) {
    msg += '<span class="hint">' + escapeHtml(d.note) + '</span>';
  }
  msg += SelLibrary.savedNote(d.project_file);
  out.innerHTML = msg;
};

// -- Importing a DNP device profile -----------------------------------------

document.getElementById('import').onclick = async () => {
  const f = document.getElementById('profile').files[0];
  if (!f) { alert('Escolha o zip (ou o XML) do perfil DNP.'); return; }
  const r = await SelProgress.upload('/import-profile', f, {
    headers: {'X-Filename': encodeURIComponent(f.name)},
    label: 'Importando perfil DNP',
    doneLabel: 'Perfil importado.',
  });
  const out = document.getElementById('profile-out');
  const d = r.data || {};
  if (!r.ok) { out.textContent = 'Falha: ' + (d.error || r.status); return; }
  const blocks = (d.check_kinds || []).join(', ') || 'nenhum';
  out.textContent = 'Perfil de ' + (d.device_name || d.model) +
    ' gravado em ' + d.file + '. Blocos com verificação de nome ativa: ' +
    blocks + '.';
  await loadModels();
};

async function loadModels() {
  let r;
  try {
    r = await (await fetch('/wordbits')).json();
  } catch (e) { return; }
  const box = document.getElementById('models');
  const ms = (r && r.models) || [];
  if (!ms.length) {
    box.textContent = 'Nenhum perfil instalado: a verificação de nomes está ' +
      'desligada para todos os modelos.';
    return;
  }
  box.innerHTML = 'Modelos com perfil instalado: ' + ms.map(m =>
    '<code>' + escapeHtml(m.model) + '</code> (' +
    (m.check_kinds.join('/') || 'só duplicados') + ')').join(' · ');
}

(async () => {
  const list = await loadRdbs();
  await PICKER.refresh();
  if (list.length) {
    const preferred = rememberedPick();
    const target = list.find(x => x.rdb === preferred) || list[list.length - 1];
    await pickRdb(target.rdb);
  }
  await loadModels();
})();
