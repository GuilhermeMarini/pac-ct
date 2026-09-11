// A tela do Exportador de Comentarios GLE (`/gle-exporter/`): escolher o RDB,
// listar os reles com GLE, exportar o Excel e reimportar o editado.
//
// Carregado por `<script src>` no mesmo ponto em que o corpo deste arquivo
// ficava embutido -- logo antes de `</body>`, sem `defer`. A posicao importa
// duas vezes: `SelLibrary.picker(...)` roda no nivel de cima daqui e o
// `SelLibrary` vem no fim do `<head>`, e `inject_progress_runtime` enfia o
// `SelProgress` DEPOIS deste script, e por isso nada aqui pode toca-lo fora
// de um handler.
//
// Esta tela nao tem bloco `page-data`: o servidor nao substitui nada neste
// documento -- tudo o que ela mostra vem de `/state`, `/select-rdb`,
// `/export` e `/import`.

const statusEl = document.getElementById('status');
function setStatus(msg, kind) {
  statusEl.textContent = msg || '';
  statusEl.className = kind || '';
}

function escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}
function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/["\\]/g, '\\$&');
}

// O RDB entra uma vez em Arquivos do Projeto; aqui so se escolhe qual.
SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: (f) => selectRdb(f),
});

async function selectRdb(f) {
  setStatus('Carregando ' + f.name + '...', '');
  const r = await SelProgress.post('/select-rdb', {sha256: f.sha256},
                                   {label: 'Carregando ' + f.name});
  if (!r.ok) {
    setStatus('Falha: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus(f.name + ' carregado.', 'ok');
  renderRelays(r.data);
}

function renderRelays(data) {
  const section = document.getElementById('relays-section');
  const container = document.getElementById('relays-container');
  if (!data.has_rdb) { section.style.display = 'none'; return; }
  section.style.display = 'block';

  const relays = data.relays || [];
  if (!relays.length) {
    container.innerHTML = '<div class="empty-list">Nenhum relé com GLE encontrado no RDB.</div>';
    return;
  }
  const rows = relays.map(r => {
    const gles = r.gles || [];
    const opts = gles.map(g => `<option value="${escAttr(g.name)}">${escHtml(g.name)}</option>`).join('');
    const noGle = gles.length === 0;
    const select = noGle
      ? '<span class="model">sem GLE</span>'
      : `<select data-relay="${escAttr(r.name)}">${opts}</select>`;
    const rowCheckbox = noGle
      ? '<input type="checkbox" disabled title="Sem GLE disponível">'
      : `<input type="checkbox" class="row-select" data-relay="${escAttr(r.name)}" title="Selecionar para exportar/importar">`;
    return `<tr>
      <td><strong>${escHtml(r.name)}</strong>${r.model ? ' <span class="model">' + escHtml(r.model) + '</span>' : ''}</td>
      <td class="ip">${escHtml(r.ip || '-')}</td>
      <td>${select}</td>
      <td class="col-select">${rowCheckbox}</td>
    </tr>`;
  }).join('');
  container.innerHTML = `
    <table class="relays">
      <thead><tr>
        <th>Relé</th><th>IP</th><th>GLE</th>
        <th class="col-select"><input type="checkbox" id="select-all" title="Selecionar/desselecionar todos"></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  setupBatchSelection(container);
}

function setupBatchSelection(container) {
  const all = container.querySelector('#select-all');
  const rowChecks = container.querySelectorAll('input.row-select');
  const exportBtn = document.getElementById('batch-export');
  const importBtn = document.getElementById('batch-import');
  const countEl = document.getElementById('batch-count');

  function updateState() {
    const total = rowChecks.length;
    const checked = container.querySelectorAll('input.row-select:checked').length;
    countEl.textContent = checked + ' selecionado' + (checked === 1 ? '' : 's');
    exportBtn.disabled = checked === 0;
    importBtn.disabled = checked === 0;
    if (all) {
      all.checked = total > 0 && checked === total;
      all.indeterminate = checked > 0 && checked < total;
      all.disabled = total === 0;
    }
  }
  if (all) {
    all.addEventListener('change', () => {
      rowChecks.forEach(cb => { if (!cb.disabled) cb.checked = all.checked; });
      updateState();
    });
  }
  rowChecks.forEach(cb => cb.addEventListener('change', updateState));
  updateState();
}

// Os tres controles do lote estao na marcacao estatica; so #relays-container e'
// substituido a cada render. Por isso eles sao ligados aqui, UMA vez, e nao
// dentro de setupBatchSelection(): la, escolher um segundo RDB acumulava um
// segundo listener no mesmo botao e um clique disparava dois POST /export
// concorrentes -- o segundo lia btn.textContent ja em "Exportando...", que
// virava o rotulo definitivo do botao. O dialogo de arquivo abria duas vezes
// pelo mesmo motivo.
(function bindBatchButtons() {
  const container = document.getElementById('relays-container');
  const importFile = document.getElementById('batch-import-file');
  document.getElementById('batch-export')
    .addEventListener('click', () => exportBatch(container));
  document.getElementById('batch-import')
    .addEventListener('click', () => importFile.click());
  importFile.addEventListener('change', () => {
    const f = importFile.files && importFile.files[0];
    if (f) importBatch(container, f);
    importFile.value = '';
  });
})();

function collectSelections(container) {
  const checks = container.querySelectorAll('input.row-select:checked');
  const out = [];
  checks.forEach(cb => {
    const relay = cb.dataset.relay;
    const sel = container.querySelector(`select[data-relay="${cssEscape(relay)}"]`);
    const gle = sel ? sel.value : '';
    if (gle) out.push({ relay, gle });
  });
  return out;
}

async function exportBatch(container) {
  const btn = document.getElementById('batch-export');
  const resultEl = document.getElementById('result');
  const selections = collectSelections(container);
  if (!selections.length) return;
  resultEl.style.display = 'none';
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Exportando...';
  try {
    const r = await SelProgress.post('/export', { selections },
      { label: 'Gerando planilha...', doneLabel: 'Planilha gerada.' });
    renderResult(r.data || { error: 'resposta invalida' }, !r.ok, 'export');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function importBatch(container, file) {
  const btn = document.getElementById('batch-import');
  const resultEl = document.getElementById('result');
  resultEl.style.display = 'none';
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Importando...';
  try {
    const r = await SelProgress.upload('/import', file, {
      headers: { 'Content-Type': 'application/octet-stream' },
      label: 'Enviando planilha',
      doneLabel: 'RDB atualizado.',
    });
    renderResult(r.data || { error: 'resposta invalida' }, !r.ok, 'import');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function renderResult(data, isError, mode) {
  const el = document.getElementById('result');
  el.className = isError ? 'err' : '';
  el.style.display = 'block';
  if (isError) {
    el.innerHTML = '<div class="title">Falha</div>'
      + '<div>' + escHtml(data.error || JSON.stringify(data)) + '</div>';
    return;
  }
  if (mode === 'export') {
    el.innerHTML =
      '<div class="title">Excel gerado: <code>' + escHtml(data.output_name || '') + '</code> '
      + '<span style="color:var(--text-2)">(' + (data.selections_count || 0) + ' aba(s), '
      + (data.total_ports || 0) + ' porta(s))</span></div>'
      + '<a class="download" href="' + escAttr(data.download_url || '#') + '" download>Baixar Excel</a>'
      + SelLibrary.savedNote(data.project_file);
    return;
  }
  // mode === 'import'
  const totals = data.totals || {};
  const results = data.results || [];
  const items = results.map(r => {
    if (!r.ok) {
      return '<li><strong>' + escHtml(r.relay) + '</strong> / '
        + escHtml(r.gle) + ' &middot; <span class="warn">' + escHtml(r.error || 'falha') + '</span></li>';
    }
    const st = r.stats || {};
    let parts = [
      (st.elements_touched || 0) + ' elements,',
      (st.ports_updated || 0) + ' ports atualizados',
    ];
    if (st.ports_skipped) parts.push((st.ports_skipped) + ' skipped');
    if (st.elements_missing) parts.push((st.elements_missing) + ' missing');
    if (r.note) parts.push('<em>' + escHtml(r.note) + '</em>');
    return '<li><strong>' + escHtml(r.relay) + '</strong> / '
      + escHtml(r.gle) + ' &middot; ' + parts.join(' ') + '</li>';
  }).join('');
  el.innerHTML =
    '<div class="title">RDB gerado: <code>' + escHtml(data.output_name || '') + '</code> '
    + '<span style="color:var(--text-2)">(' + (data.succeeded || 0) + ' ok, '
    + (data.failed || 0) + ' falha; '
    + (totals.ports_updated || 0) + ' comments atualizados)</span></div>'
    + '<ul>' + items + '</ul>'
    + '<a class="download" href="' + escAttr(data.download_url || '#') + '" download>Baixar RDB</a>'
    + SelLibrary.savedNote(data.project_file);
}

(function setupBackToMenu() {
  // O menu fica sempre no ar em "/" -- nao ha mais teardown/espera de porta.
  const btn = document.getElementById('back-to-menu');
  if (btn) btn.addEventListener('click', () => { window.location.href = '/'; });
})();

(async function init() {
  try {
    const r = await fetch('/state', { cache: 'no-store' });
    const data = await r.json();
    if (data.has_rdb) {
      renderRelays(data);
    }
  } catch (e) {}
})();
