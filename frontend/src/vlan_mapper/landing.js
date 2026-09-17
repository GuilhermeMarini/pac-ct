// A tela do Mapeador de VLAN (`/vlan-mapper/`): escolher o SCD, montar a
// tabela de GOOSE por rele, filtrar, trocar o formato do VLAN ID e copiar CSV.
//
// Carregado por `<script src>` no mesmo ponto em que o corpo deste arquivo
// ficava embutido -- logo antes de `</body>`, sem `defer`. A posicao importa
// duas vezes: `SelLibrary.picker(...)` roda no nivel de cima daqui e o
// `SelLibrary` vem no fim do `<head>`, e `inject_progress_runtime` enfia o
// `SelProgress` DEPOIS deste script, e por isso nada aqui pode toca-lo fora
// de um handler.
//
// Esta tela nao tem bloco `page-data`: o servidor nao substitui nada neste
// documento -- o SCD vem de `/select-scd` e de `/state`, e as duas
// preferencias de exibicao vem do `localStorage` do proprio navegador.

const statusEl = document.getElementById('status');
function setStatus(msg, kind) {
  statusEl.textContent = msg || '';
  statusEl.className = kind || '';
}

function escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
// Texto que vai DENTRO de um atributo ("..."), e nao entre tags. Precisa do
// `"`, que o escHtml nao toca: descricao e nome de IED vem do SCD, e uma
// aspas num deles fechava o atributo e o resto virava marcacao.
function escAttr(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

// O SCD entra uma vez em Arquivos do Projeto; aqui so se escolhe qual.
SelLibrary.picker('pick-scd', {
  kind: 'scd', label: 'SCD do projeto',
  onPick: (f) => selectScd(f),
});

async function selectScd(f) {
  setStatus('Lendo ' + f.name + '...', '');
  const r = await SelProgress.post('/select-scd', {sha256: f.sha256},
                                   {label: 'Lendo ' + f.name});
  if (!r.ok) {
    setStatus('Falha: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus(f.name + ' carregado.', 'ok');
  render(r.data);
}

let _lastData = null;
// Formato de exibicao dos VLAN-IDs. O SCD armazena em hex (string de 3 chars
// como "033"); o switch frequentemente espera decimal. Persistido em
// localStorage pra UX entre sessoes.
let _vlanFmt = 'hex';
try { _vlanFmt = localStorage.getItem('vlan-mapper-fmt') || 'hex'; } catch(e) {}
// Modo padrao de exibicao por linha: 'chips' (default) ou 'text' (CSV
// selecionavel). O toggle global no toolbar altera essa variavel e
// re-renderiza; o botao por linha alterna so uma linha (override pontual).
let _displayMode = 'chips';
try { _displayMode = localStorage.getItem('vlan-mapper-mode') || 'chips'; } catch(e) {}

function vlanFmt(v) {
  if (v == null) return '';
  const s = String(v).trim();
  if (_vlanFmt === 'dec') {
    const n = parseInt(s, 16);
    if (!isNaN(n)) return String(n);
  }
  return s.toUpperCase();
}

function vlanSortKey(v) {
  const s = String(v).trim();
  const h = parseInt(s, 16);
  if (!isNaN(h)) return h;
  return Number.MAX_SAFE_INTEGER;
}

function render(data) {
  _lastData = data;
  const results = document.getElementById('results');
  if (!data || !data.has_scd) {
    results.style.display = 'none';
    return;
  }
  results.style.display = 'block';
  renderSummary();
  renderTable();
}

function renderSummary() {
  const data = _lastData;
  const summary = document.getElementById('summary');
  if (!data) { summary.innerHTML = ''; return; }
  summary.innerHTML =
    '<div class="stat"><strong>' + data.ied_count + '</strong> IED(s)</div>'
    + '<div class="stat"><strong>' + data.vlan_count + '</strong> VLAN(s) distintos</div>'
    + (data.all_vlans && data.all_vlans.length
        ? '<div class="stat">Todos: ' + data.all_vlans.map(v =>
            '<code>' + escHtml(vlanFmt(v)) + '</code>').join(' ')
          + '</div>'
        : '');
}

function renderTable() {
  const data = _lastData;
  const tw = document.getElementById('table-wrap');
  if (!data || !data.rows || !data.rows.length) {
    tw.innerHTML = '<div class="empty-list">SCD não contem IEDs.</div>';
    return;
  }
  const rows = data.rows.map(r => rowHtml(r)).join('');
  tw.innerHTML =
    '<table class="vlans">'
    + '<thead><tr>'
    + '<th>IED (relé)</th>'
    + '<th>IP</th>'
    + '<th>VLAN(s) a permitir no switch</th>'
    + '<th>RX / TX</th>'
    + '<th>Nao resolvido</th>'
    + '</tr></thead>'
    + '<tbody>' + rows + '</tbody>'
    + '</table>';
  applyFilter();  // reaplica filtro corrente após rerender
}

// Lista (ordenada) de VLAN-IDs unicos da linha (RX uniao TX), no formato
// armazenado (hex string). E uma "fonte da verdade" interna; usar vlanFmt()
// pra exibir.
function rowVlans(r) {
  const set = new Set([...(r.rx_vlans || []), ...(r.tx_vlans || [])]);
  return [...set].sort((a, b) => vlanSortKey(a) - vlanSortKey(b));
}

// Publishers que originam um dado VLAN para esta linha. Inclui:
//   - publishers RX (do payload publishers_by_vlan)
//   - "(self)" se a linha publica nesse VLAN (TX)
function publishersForVlan(r, vid) {
  const out = [];
  const rxPubs = (r.publishers_by_vlan && r.publishers_by_vlan[vid]) || [];
  out.push(...rxPubs);
  if ((r.tx_vlans || []).indexOf(vid) >= 0) out.push('(self)');
  return out;
}

function chipHtml(r, vid) {
  const inRx = (r.rx_vlans || []).indexOf(vid) >= 0;
  const inTx = (r.tx_vlans || []).indexOf(vid) >= 0;
  const cls = (inRx && inTx) ? 'both' : (inRx ? 'rx' : 'tx');
  const kindLabel = (inRx && inTx) ? 'RX + TX' : (inRx ? 'RX (subscrito)' : 'TX (publicado)');
  const pubs = publishersForVlan(r, vid);

  // Texto curto no chip: 1 publisher mostra o nome inteiro (com ellipsis);
  // >1 mostra "primeiro +N"; sem publishers (caso degenerado) mostra "-".
  let srcText = '';
  let srcCls = '';
  if (pubs.length === 0) {
    srcText = '—';
  } else if (pubs.length === 1) {
    srcText = pubs[0];
    if (srcText === '(self)') srcCls = ' self';
  } else {
    const first = pubs[0];
    const rest = pubs.length - 1;
    srcText = first + '  +' + rest;
    if (first === '(self)') srcCls = ' self';
  }

  // Tooltip lista todos os publishers + tipo.
  const tipPubs = pubs.length
    ? '\n' + pubs.map(p => '  ' + p).join('\n')
    : '';
  const title = 'VLAN ' + vlanFmt(vid) + ' (' + kindLabel + ')' + tipPubs;

  return '<span class="chip ' + cls + '" title="' + escAttr(title) + '">'
    + '<span class="vid">' + escHtml(vlanFmt(vid)) + '</span>'
    + '<span class="src' + srcCls + '">' + escHtml(srcText) + '</span>'
    + '</span>';
}

function csvText(r) {
  // CSV simples (so VLAN-IDs, no formato atual). Util pra colar no switch CLI.
  return rowVlans(r).map(v => vlanFmt(v)).join(', ');
}

function rowHtml(r) {
  const vlans = rowVlans(r);
  const rxSize = (r.rx_vlans || []).length;
  const txSize = (r.tx_vlans || []).length;

  const chips = vlans.length
    ? vlans.map(v => chipHtml(r, v)).join('')
    : '<span class="chip" title="Sem GOOSE">&mdash;</span>';

  const legend = (rxSize && txSize)
    ? '<div class="legend">azul = RX &middot; verde = TX &middot; roxo = ambos</div>'
    : '';

  const subline = r.relay_type || r.description
    ? '<div class="subline">'
        + (r.relay_type ? escHtml(r.relay_type) : '')
        + (r.relay_type && r.description ? ' &middot; ' : '')
        + (r.description ? escHtml(r.description) : '')
      + '</div>'
    : '';

  let unresolvedCell = '';
  if (r.unresolved && r.unresolved.length) {
    unresolvedCell =
      '<details><summary>' + r.unresolved.length + ' GSE(s)</summary>'
      + '<ul>' + r.unresolved.map(u => '<li>' + escHtml(u) + '</li>').join('') + '</ul>'
      + '</details>';
  }

  const cls = vlans.length ? '' : ' class="empty-row"';
  // data-search inclui valor formatado E valor raw, pra que o filtro case
  // tanto com input hex quanto decimal.
  const searchValues = [
    r.ied_name, r.ip, r.relay_type, r.description,
    ...vlans, ...vlans.map(vlanFmt),
  ];

  // Conteudo da celula respeitando o modo global (chips/text). O toggle
  // global no toolbar e a unica forma de alternar entre os dois.
  const initialMode = _displayMode === 'text' ? 'text' : 'chips';
  const cellInner = initialMode === 'text'
    ? csvCellHtml(r)
    : ('<div class="chips">' + chips + '</div>' + legend);

  return '<tr' + cls + ' data-ied="' + escAttr(r.ied_name) + '"'
    + ' data-search="' + escAttr(searchValues.join(' ').toLowerCase()) + '">'
    + '<td class="ied">' + escHtml(r.ied_name) + subline + '</td>'
    + '<td class="ip">' + escHtml(r.ip || '-') + '</td>'
    + '<td class="vlans-cell" data-mode="' + initialMode + '">'
        + cellInner
    + '</td>'
    + '<td class="count">' + r.rx_count + ' / ' + r.tx_count + '</td>'
    + '<td class="unresolved">' + unresolvedCell + '</td>'
    + '</tr>';
}

// HTML do conteudo da celula no modo CSV/texto.
function csvCellHtml(r) {
  const text = csvText(r);
  const isEmpty = !text;
  return '<div class="csv-text' + (isEmpty ? ' empty' : '') + '">'
    + (isEmpty ? '(sem VLANs)' : escHtml(text)) + '</div>';
}

// Estado do filtro atual (compartilhado entre input + re-renders).
let _filterQ = '';
function applyFilter() {
  const q = _filterQ;
  document.querySelectorAll('table.vlans tbody tr').forEach(tr => {
    if (!q) { tr.style.removeProperty('display'); return; }
    const hay = tr.dataset.search || '';
    tr.style.display = hay.indexOf(q) >= 0 ? '' : 'none';
  });
}

(function setupFilterAndToggle() {
  const filter = document.getElementById('filter');
  filter.addEventListener('input', () => {
    _filterQ = (filter.value || '').trim().toLowerCase();
    applyFilter();
  });

  const toggle = document.getElementById('toggle-empty');
  const KEY = 'vlan-mapper-hide-empty';
  function apply(on) {
    document.body.classList.toggle('hide-empty', on);
    toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
    toggle.classList.toggle('active', on);
    toggle.textContent = on ? 'Mostrar sem VLAN' : 'Ocultar sem VLAN';
  }
  try { apply(localStorage.getItem(KEY) === '1'); } catch(e) { apply(false); }
  toggle.addEventListener('click', () => {
    const next = !document.body.classList.contains('hide-empty');
    apply(next);
    try { localStorage.setItem(KEY, next ? '1' : '0'); } catch(e) {}
  });
})();

// Hex/Dec toggle. Aplica imediatamente (re-renderiza summary + tabela).
(function setupFormatToggle() {
  const buttons = document.querySelectorAll('.fmt-group button[data-fmt]');
  function apply(fmt) {
    _vlanFmt = fmt;
    buttons.forEach(b => {
      const active = b.dataset.fmt === fmt;
      b.classList.toggle('active', active);
      b.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    try { localStorage.setItem('vlan-mapper-fmt', fmt); } catch(e) {}
    if (_lastData) {
      renderSummary();
      renderTable();
    }
  }
  // Sincroniza estado visual inicial com _vlanFmt carregado de localStorage.
  apply(_vlanFmt);
  buttons.forEach(b => b.addEventListener('click', () => apply(b.dataset.fmt)));
})();

// Chips/Texto global toggle. Renderiza todas as linhas no modo selecionado,
// permitindo selecionar/copiar varias linhas de texto CSV de uma so vez.
(function setupModeToggle() {
  const buttons = document.querySelectorAll('.fmt-group button[data-mode]');
  function apply(mode) {
    _displayMode = (mode === 'text') ? 'text' : 'chips';
    buttons.forEach(b => {
      const active = b.dataset.mode === _displayMode;
      b.classList.toggle('active', active);
      b.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    try { localStorage.setItem('vlan-mapper-mode', _displayMode); } catch(e) {}
    if (_lastData) renderTable();
  }
  apply(_displayMode);
  buttons.forEach(b => b.addEventListener('click', () => apply(b.dataset.mode)));
})();

document.getElementById('copy-csv').addEventListener('click', async () => {
  if (!_lastData || !_lastData.rows) return;
  // CSV completo da tabela. Usa o formato atual (Hex/Dec) pra coluna All_VLANs;
  // mantem o formato raw nas colunas RX_VLANs/TX_VLANs (auditavel).
  const header = ['IED', 'IP', 'Type', 'Description', 'RX_VLANs', 'TX_VLANs', 'All_VLANs (' + _vlanFmt + ')'];
  const lines = [header.join(',')];
  for (const r of _lastData.rows) {
    const allRaw = [...new Set([...(r.rx_vlans||[]), ...(r.tx_vlans||[])])]
      .sort((a,b) => vlanSortKey(a) - vlanSortKey(b));
    const row = [
      r.ied_name, r.ip || '', r.relay_type || '', r.description || '',
      (r.rx_vlans||[]).join(';'),
      (r.tx_vlans||[]).join(';'),
      allRaw.map(vlanFmt).join(';'),
    ].map(v => {
      const s = String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    });
    lines.push(row.join(','));
  }
  const csv = lines.join('\n');
  try {
    await navigator.clipboard.writeText(csv);
    setStatus('CSV copiado para a área de transferencia.', 'ok');
  } catch (e) {
    setStatus('Falha ao copiar: ' + e, 'err');
  }
});

(function setupBackToMenu() {
  // O menu fica sempre no ar em "/" -- nao ha mais teardown/espera de porta.
  const btn = document.getElementById('back-to-menu');
  if (btn) btn.addEventListener('click', () => { window.location.href = '/'; });
})();

(async function init() {
  try {
    const r = await fetch('/state', { cache: 'no-store' });
    const data = await r.json();
    if (data.has_scd) {
      render(data);
    }
  } catch (e) { /* sem SCD ainda */ }
})();
