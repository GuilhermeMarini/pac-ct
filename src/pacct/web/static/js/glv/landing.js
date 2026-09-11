// A tela de abertura do GLV (`/glv/novo`): escolhe o RDB do projeto, lista os
// reles que tem GLE, e abre um ou varios diagramas de uma vez.
//
// Carregado por `<script src>` exatamente onde o corpo deste arquivo ficava
// embutido -- logo antes de `</body>`, sem `defer`. A posicao importa:
// `inject_progress_runtime` enfia o `SelProgress` ali tambem, DEPOIS deste
// script, e por isso nada aqui pode tocar em `SelProgress` no nivel de cima.
// `SelLibrary` ao contrario vem no fim do `<head>`, e as duas chamadas de
// `SelLibrary.picker(...)` la' embaixo rodam no nivel de cima de proposito.

const statusEl = document.getElementById('status');
const infoEl = document.getElementById('info');
const relaysEl = document.getElementById('relays');
const barEl = document.getElementById('batch-bar');
const barBtn = document.getElementById('batch-open');
const barSlots = document.getElementById('batch-slots');

// Padrao de CADA linha, vindo do `[web] glv_scan_mode` do servidor. Antes a
// tela trazia 'telnet' marcado no HTML e sempre mandava um modo explicito no
// POST, entao o default do config.ini nunca chegava aqui -- ele so' valia
// como fallback pra um cliente que nao dissesse nada, e este cliente sempre
// diz. Configurar `glv_scan_mode = mms` nao mudava nada na tela.
let defaultMode = 'telnet';

// O SCD escolhido para os diagramas que forem abertos daqui. E' do PROJETO,
// nao de um rele: descreve a subestacao inteira, e vale para os dois usos --
// o mapa que o MMS le e a fonte dos VB que o diagrama desconectado mostra.
//
// Ele ficava escondido dentro do quadro "Como ler o rele", e so' aparecia
// quando algum rele estava em MMS e o projeto tinha mais de um SCD. Com a
// fonte dos VB, um diagrama em telnet tambem precisa dele, entao a escolha
// subiu para o mesmo lugar do RDB e usa a mesma lista de arquivos do
// projeto que as outras ferramentas.
let scdSha = null;


// Um item por linha de GLE na tela. A selecao mora nos proprios checkboxes --
// uma copia paralela seria uma segunda verdade pra manter em dia.
let picks = [];
let openDiagrams = 0;
let maxDiagrams = 8;

function selectedPicks() {
  return picks.filter(p => p.cb.checked);
}

function setOpenCount(n, max) {
  openDiagrams = n;
  if (max) maxDiagrams = max;
  const back = document.getElementById('back-to-diagrams');
  if (back) {
    back.style.display = n > 0 ? '' : 'none';
    back.textContent = '\u2190 Diagramas (' + n + ')';
  }
  renderBatchBar();
}

function renderBatchBar() {
  const sel = selectedPicks();
  if (!sel.length) { barEl.style.display = 'none'; return; }
  const free = Math.max(0, maxDiagrams - openDiagrams);
  const over = sel.length > free;
  barEl.style.display = 'flex';
  barEl.classList.toggle('over', over);
  barBtn.disabled = over;
  barBtn.textContent = sel.length === 1
    ? 'Abrir 1 diagrama' : 'Abrir ' + sel.length + ' diagramas';
  // O teto e' dito antes do clique: descobrir que so cabem duas depois de
  // marcar seis e esperar a renderizacao seria o pior momento.
  barSlots.textContent = over
    ? sel.length + ' marcados e s\u00f3 ' + free + ' vaga(s) de ' + maxDiagrams
      + ' \u2014 feche uma aba ou desmarque'
    : sel.length + ' de ' + free + ' vaga(s) livre(s)';
}

function clearPicks() {
  for (const p of picks) {
    p.cb.checked = false;
    if (p.row) p.row.classList.remove('checked');
  }
  renderBatchBar();
}

function setStatus(msg, kind) {
  statusEl.textContent = msg || '';
  statusEl.className = kind || '';
}

function adoptDefaults(data) {
  if (data && data.scan_mode) defaultMode = data.scan_mode;
}

function renderRdbInfo(data) {
  // Antes de montar as linhas: cada <select> nasce lendo `defaultMode`.
  adoptDefaults(data);
  picks = [];
  infoEl.style.display = 'flex';
  document.getElementById('static-toggle').style.display = 'flex';
  document.getElementById('info-name').textContent = data.rdb_name || '';
  const badge = document.getElementById('info-badge');
  if (data.reused) {
    badge.textContent = 'hash idêntico -- extração reaproveitada';
    badge.className = 'badge reused';
  } else {
    badge.textContent = 'extraído agora';
    badge.className = 'badge fresh';
  }
  document.getElementById('info-hash').textContent = 'sha256: ' + (data.sha256 || '').slice(0,16) + '...';

  relaysEl.innerHTML = '';
  if (!data.relays || !data.relays.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Nenhum relé com arquivo GLE encontrado no RDB.';
    relaysEl.appendChild(empty);
    renderBatchBar();
    return;
  }
  for (const r of data.relays) {
    const card = document.createElement('div');
    card.className = 'relay';

    const head = document.createElement('div');
    head.className = 'relay-head';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'name';
    nameSpan.textContent = r.name;
    head.appendChild(nameSpan);

    if (r.model) {
      const modelSpan = document.createElement('span');
      modelSpan.className = 'model';
      modelSpan.textContent = 'SEL-' + r.model;
      head.appendChild(modelSpan);
    }

    const ipLabel = document.createElement('span');
    ipLabel.className = 'ip-label';
    ipLabel.textContent = 'IP:';
    head.appendChild(ipLabel);

    const ipInput = document.createElement('input');
    ipInput.type = 'text';
    ipInput.className = 'ip-input';
    ipInput.placeholder = '0.0.0.0';
    ipInput.value = r.ip || '';
    ipInput.spellcheck = false;
    ipInput.autocomplete = 'off';
    ipInput.title = r.ip
      ? 'IP detectado no RDB; edite se necessario'
      : 'IP não encontrado no RDB; informe manualmente';
    ipInput.addEventListener('input', () => {
      ipInput.classList.toggle('invalid',
        ipInput.value.trim() !== '' && !isValidIp(ipInput.value));
    });
    head.appendChild(ipInput);

    // O modo e' do RELE, e nao do GLE: os varios GLE de um rele sao paginas
    // do mesmo diagrama fisico e conversam pelo mesmo transporte.
    const modeSel = document.createElement('select');
    modeSel.className = 'mode-sel';
    modeSel.title = 'Telnet lê a Relay Word inteira, mas não vê Virtual Bits '
      + 'nem páginas de GOOSE. MMS mostra GOOSE e todas as VB, e não gasta '
      + 'sessão telnet, mas não alcança todos os bits.';
    for (const [value, label] of [['telnet', 'Telnet'], ['mms', 'MMS']]) {
      const o = document.createElement('option');
      o.value = value;
      o.textContent = label;
      modeSel.appendChild(o);
    }
    modeSel.value = defaultMode;
    head.appendChild(modeSel);

    const countSpan = document.createElement('span');
    countSpan.className = 'count';
    countSpan.textContent = r.gles.length + ' GLE';
    head.appendChild(countSpan);

    card.appendChild(head);

    for (const g of r.gles) {
      const row = document.createElement('div');
      row.className = 'gle-row';

      const pick = document.createElement('label');
      pick.className = 'pick';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      const nm = document.createElement('div');
      nm.className = 'gle-name';
      nm.textContent = g.filename;
      pick.appendChild(cb);
      pick.appendChild(nm);

      // O botao avulso continua: marcar e clicar na barra pra abrir um so
      // seria mais trabalho do que antes.
      const btn = document.createElement('button');
      btn.className = 'primary';
      btn.textContent = 'Abrir';
      btn.onclick = () => selectGle(r.name, g.name, btn, ipInput, modeSel);

      row.appendChild(pick);
      row.appendChild(btn);
      card.appendChild(row);

      picks.push({relay: r.name, gle: g.name, cb, row, ipInput, modeSel});
      cb.addEventListener('change', () => {
        row.classList.toggle('checked', cb.checked);
        renderBatchBar();
      });
    }
    relaysEl.appendChild(card);
  }
  if (typeof data.open_diagrams === 'number') {
    setOpenCount(data.open_diagrams, data.max_diagrams);
  } else {
    renderBatchBar();
  }
}

function isValidIp(s) {
  const m = String(s).trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return false;
  return m.slice(1).every(x => { const n = +x; return n >= 0 && n <= 255; });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// O RDB entra uma vez em Arquivos do Projeto; aqui so se escolhe qual usar.
async function selectRdb(f) {
  setStatus('Carregando ' + f.name + '...', '');
  const r = await SelProgress.post('/select-rdb', {sha256: f.sha256},
                                   {label: 'Carregando ' + f.name});
  if (!r.ok) {
    setStatus('Erro: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus('Pronto. Escolha um GLE para plotar.', 'ok');
  renderRdbInfo(r.data);
}

async function selectGle(relay, gle, btn, ipInput, modeSel) {
  const ip = (ipInput && ipInput.value || '').trim();
  // O IP e' obrigatorio mesmo com o diagrama abrindo desconectado: sem ele o
  // botao Conectar nasceria morto, e e' melhor dizer agora.
  if (!ip) {
    setStatus('Informe o IP do relé.', 'err');
    ipInput && ipInput.focus();
    return;
  }
  if (!isValidIp(ip)) {
    setStatus('IP inválido: ' + ip, 'err');
    ipInput && ipInput.focus();
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Abrindo...';
  setStatus('Renderizando as páginas do GLE...', '');
  try {
    const resp = await fetch('/diagrams', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({relay, gle, ip,
                            scan_mode: modeSel ? modeSel.value : defaultMode,
                            scd_sha: scdSha}),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus('Erro: ' + (data.error || resp.statusText), 'err');
      btn.disabled = false;
      btn.textContent = 'Abrir';
      return;
    }
    // Volta pra casca com a aba nova ativa. Trocar DE aba nao recarrega;
    // abrir mais uma, sim -- e' o unico momento em que isso acontece.
    window.location.href = './?d=' + encodeURIComponent(data.id);
  } catch (e) {
    setStatus('Falha de rede: ' + e, 'err');
    btn.disabled = false;
    btn.textContent = 'Abrir';
  }
}

async function openBatch() {
  const sel = selectedPicks();
  if (!sel.length) return;
  // So os IPs dos reles com alguma coisa marcada importam: um rele sem nada
  // marcado nao vira diagrama nenhum e nao precisa de IP.
  for (const p of sel) {
    const ip = (p.ipInput && p.ipInput.value || '').trim();
    if (!ip || !isValidIp(ip)) {
      setStatus(ip ? 'IP inválido em ' + p.relay + ': ' + ip
                   : 'Informe o IP do relé ' + p.relay + '.', 'err');
      if (p.ipInput) { p.ipInput.classList.add('invalid'); p.ipInput.focus(); }
      return;
    }
  }
  // Um modo POR ITEM: `/diagrams/batch` sempre leu `scan_mode` de cada item,
  // era a tela que mandava o mesmo pra todos. Um lote pode agora misturar um
  // 311C em telnet com um 487E em MMS, numa clicada so'.
  const scd = scdSha;
  const items = sel.map(p => ({relay: p.relay, gle: p.gle,
                               ip: p.ipInput.value.trim(),
                               scan_mode: p.modeSel ? p.modeSel.value
                                                    : defaultMode,
                               scd_sha: scd}));
  barBtn.disabled = true;
  setStatus('Renderizando ' + items.length + ' GLE...', '');
  const r = await SelProgress.post('/diagrams/batch', {items: items},
    {label: 'Abrindo ' + items.length + ' diagrama(s)'});
  barBtn.disabled = false;
  if (!r.ok) {
    setStatus('Erro: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  const ids = (r.data && r.data.ids) || [];
  const errs = (r.data && r.data.errors) || [];
  if (errs.length) {
    // Lote parcial: nao navega, senao a mensagem sumiria junto com a pagina.
    setStatus(ids.length + ' diagrama(s) aberto(s). Falhou: ' + errs.join(' | '),
              'err');
    clearPicks();
    setOpenCount(((r.data && r.data.diagrams) || []).length, r.data.max_diagrams);
    return;
  }
  // Volta pra casca com a primeira aba do lote ativa.
  window.location.href = './?d=' + encodeURIComponent(ids[0]);
}

barBtn.addEventListener('click', openBatch);
document.getElementById('batch-clear').addEventListener('click', clearPicks);

const rdbPicker = SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: (f) => selectRdb(f),
});

// Sem rota: o sha viaja no POST que abre o diagrama, e e' la' que o servidor
// resolve o arquivo. Nao ha' nada para carregar aqui e agora.
const scdPicker = SelLibrary.picker('pick-scd', {
  kind: 'scd', label: 'SCD do projeto (opcional)',
  onPick: (f) => { scdSha = f.sha256; },
});

document.getElementById('reload-btn').addEventListener('click', () => {
  infoEl.style.display = 'none';
  document.getElementById('static-toggle').style.display = 'none';
  relaysEl.innerHTML = '';
  picks = [];
  renderBatchBar();
  setStatus('', '');
  // O acervo pode ter mudado noutra aba enquanto este RDB estava escolhido.
  rdbPicker.refresh();
  scdPicker.refresh();
});

// Restaura estado se ja temos um RDB carregado (ex: recarga da pagina)
fetch('/landing-state').then(r => r.json()).then(data => {
  adoptDefaults(data);
  setOpenCount((data && data.open_diagrams) || 0, data && data.max_diagrams);
  if (data && data.has_rdb) renderRdbInfo(data);
});
