// O Visualizador de Logica (`/glv/`): a faixa de abas, o desenho, o avaliador
// da logica, a busca de variaveis, as notas, o marcador, a fonte dos VB e o
// polling.
//
// Carregado por `<script src>` exatamente onde o corpo deste arquivo ficava
// embutido -- logo antes de `</body>`, sem `defer`. A posicao importa:
// `inject_progress_runtime` enfia o `SelProgress` ali tambem, DEPOIS deste
// script, e por isso nada aqui toca em `SelProgress` no nivel de cima -- so'
// de dentro de um handler, que e' quando ele ja' existe.
//
// Esta e' a UNICA pagina da arvore que precisa do bloco `page-data`. As outras
// cinco extracoes eram constantes; esta nao: `const BOOT = ${BOOT_JSON}` era
// substituido a CADA request em `glv/handler.py`, e substituir dentro do corpo
// de um <script> deixou de ser possivel no momento em que o corpo saiu do
// .html. O bloco vem antes deste arquivo no documento e e' lido com
// `PacPage.data()`.

// Estado de arranque: as abas abertas e qual esta ativa. O resto (paginas,
// indice de variaveis, familias de analogicos) vem de /meta?d= a cada troca
// de aba -- por isso nada aqui e' const.
const BOOT = PacPage.data().boot || {};
let TABS = BOOT.diagrams || [];
let activeDiagram = BOOT.active || (TABS[0] && TABS[0].id) || null;
let PAGES = [];        // [[nome, safe_id], ...] do diagrama ativo
let VAR_INDEX = {};    // {NAME_UPPER: {kind:'bit'|'analog', pages:[safe,...]}}
let currentPage = "";

// Toda rota do GLV e' por diagrama: sem `d` o servidor usaria o ativo dele,
// que pode nao ser o que esta na tela desta aba do navegador.
function withD(url) {
  if (!activeDiagram) return url;
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'd=' + encodeURIComponent(activeDiagram);
}
let lastValues = { digitals: {}, analogs: {}, ts: 0, error: "" };
let varSearch = null;  // exposto pra loadPage() re-aplicar busca após troca de página
let zoomCtl = null;    // exposto pra loadPage() reajustar o zoom na nova página
let vbSourceCtl = null;// exposto pra loadPage() re-aplicar a camada de fonte dos VB

// Controle de período: so' existe pro modo MMS (ver diagram.py:set_interval_ms
// -- telnet nao tem piso proprio pra isto mexer). `activeScanMode` vem de
// meta() a cada troca de diagrama; `periodNotice` guarda o "adiado"/"recusado"
// da ultima resposta de /period ate' a proxima troca, porque um pedido adiado
// continua valendo mesmo depois que o proximo poll de /values sobrescreveria
// um texto solto.
let activeScanMode = 'telnet';
let periodNotice = null;
const pollCtl = document.getElementById('poll-ctl');
const pollMs = document.getElementById('poll-ms');
const pollStatus = document.getElementById('poll-status');

(function setupPanelToggle() {
  const btn = document.getElementById('panel-toggle');
  if (!btn) return;
  const KEY = 'sel411-panel-collapsed';
  function apply(collapsed) {
    document.body.classList.toggle('panel-collapsed', collapsed);
    btn.textContent = collapsed ? 'Mostrar painel' : 'Ocultar painel';
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
  try { apply(localStorage.getItem(KEY) === '1'); } catch (e) { apply(false); }
  btn.addEventListener('click', () => {
    const next = !document.body.classList.contains('panel-collapsed');
    apply(next);
    try { localStorage.setItem(KEY, next ? '1' : '0'); } catch (e) {}
  });
})();

// Cache do SVG por pagina, no cliente. `/pages/<id>` devolve sempre os mesmos
// bytes -- o servidor renderiza todas as paginas quando o diagrama abre e
// guarda as strings prontas -- entao ir e voltar entre duas paginas baixava e
// RE-PARSEAVA ~48 kB de SVG a cada troca. Guardamos o no ja' parseado: voltar
// a uma pagina ja' vista e' um `replaceChildren`, sem rede e sem parse.
//
// O GET continua acontecendo, agora com `have=1`. `/pages/<id>` e' o UNICO
// caminho pelo qual o servidor descobre que pagina o visitante abriu
// (`GlvDiagram.remember_page`, que e' o que faz a aba lembrar onde estava), e
// essa invariante nao pode depender de o cliente ter ou nao os bytes. Com
// `have=1` a resposta e' 204 sem corpo, e ninguem espera por ela.
const PAGE_CACHE_MAX = 12;
const pageSvgCache = new Map();

async function loadPage(safeId, name) {
  const viewer = document.getElementById('viewer');
  const key = `${activeDiagram}|${safeId}`;
  let node = pageSvgCache.get(key);
  if (node) {
    pageSvgCache.delete(key);            // reordena o LRU
    pageSvgCache.set(key, node);
    fetch(withD(`/pages/${safeId}?have=1`)).catch(() => {});
    // Um no que volta do cache traz os overlays que tinha quando saiu.
    // `applyHighlightsForCurrentPage` so' ADICIONA (e cada `apply*Highlight`
    // se protege de duplicar), entao um marcador apagado enquanto a pagina
    // estava fora ficaria na tela para sempre. Zerar aqui e deixar o reapply
    // reconstruir e' o que mantem o desenho igual ao estado do servidor.
    node.querySelectorAll('.hl-overlay, .hl-line-overlay, .search-hit, .search-hit-line')
        .forEach(n => n.remove());
    node.querySelectorAll('.highlighted').forEach(n => n.classList.remove('highlighted'));
  } else {
    const r = await fetch(withD(`/pages/${safeId}`));
    const text = await r.text();
    const holder = document.createElement('div');
    holder.innerHTML = text;
    node = holder.querySelector('svg');
    if (node) {
      pageSvgCache.set(key, node);
      // LRU curto: 42 paginas de um GLE grande sao ~2 MB de SVG parseado, e
      // ninguem navega entre doze paginas ao mesmo tempo.
      while (pageSvgCache.size > PAGE_CACHE_MAX) {
        pageSvgCache.delete(pageSvgCache.keys().next().value);
      }
    }
  }
  if (node) viewer.replaceChildren(node);
  else viewer.innerHTML = '';
  if (zoomCtl) zoomCtl.onPageChange();
  document.querySelectorAll('#pages button').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`#pages button[data-page="${safeId}"]`);
  if (btn) btn.classList.add('active');
  currentPage = safeId;
  // O stream segue a PAGINA: `/events` filtra pelos bits dela, como `/values`.
  startStream();
  applyValues(lastValues);
  applyGroupState();
  applyHighlightsForCurrentPage();
  if (notesCtl) notesCtl.onPageChange(safeId);
  if (varSearch) varSearch.reapply();
  // Depois dos valores: a tinta de auditoria escreve por cima das classes de
  // estado, e um `applyValues` posterior a apagaria.
  if (vbSourceCtl) vbSourceCtl.onPageChange();
}

// =============================================================================
// Busca de variaveis no diagrama (header > #var-search-wrap)
// Match case-insensitive por substring contra VAR_INDEX (chaves UPPER).
// Pinta overlays cyan sobre matches da pagina atual; lista paginas com
// matches em outros lugares no dropdown (clicaveis -> loadPage).
// Enter avanca, Shift+Enter volta, Esc limpa. Tecla "/" foca o input.
// =============================================================================
(function setupVarSearch() {
  const input    = document.getElementById('var-search');
  const countEl  = document.getElementById('var-search-count');
  const btnPrev  = document.getElementById('var-search-prev');
  const btnNext  = document.getElementById('var-search-next');
  const btnClear = document.getElementById('var-search-clear');
  const dropdown = document.getElementById('var-search-dropdown');
  if (!input) return;

  // PAGES so' e' preenchido por applyMeta(), DEPOIS deste IIFE, e e' trocado
  // a cada aba de diagrama -- entao nada aqui pode virar snapshot: le-se
  // sempre a lista viva do diagrama ativo.
  // Mapa pageId -> nome legivel (pra dropdown)
  function pageName(safe) {
    for (const [name, s] of PAGES) if (s === safe) return name;
    return safe;
  }

  // Ordem global das paginas -- usada pra decidir qual e a "proxima pagina
  // com matches" na navegacao cross-page.
  function pageOrder() { return PAGES.map(([, safe]) => safe); }

  let lastQuery = '';
  let currentHits = [];   // [{el, name, kind}] sobre a página atual
  let currentIdx = -1;
  let debounceTimer = null;
  // Set ordenado (pela ordem de PAGES) das paginas que tem matches pra
  // query atual. Recalculado em todo run() a partir do VAR_INDEX.
  let pagesWithHits = [];
  // Sinaliza ao proximo run() pra focar o ULTIMO hit (em vez do primeiro)
  // -- usado quando o usuario navega "pra tras" cruzando paginas.
  let pendingFocus = null;  // 'first' | 'last' | null

  function clearHighlights() {
    document.querySelectorAll('#viewer svg .search-hit').forEach(n => n.remove());
    currentHits = [];
    currentIdx = -1;
  }

  // Pinta/desmarca o dot ciano nos botoes das paginas que tem matches.
  function clearTabHighlights() {
    document.querySelectorAll('#pages button.has-search-hit')
      .forEach(b => b.classList.remove('has-search-hit'));
  }
  function applyTabHighlights(pageSet) {
    document.querySelectorAll('#pages button').forEach(b => {
      const pg = b.getAttribute('data-page');
      b.classList.toggle('has-search-hit', pageSet.has(pg));
    });
  }

  function paintHit(blockEl) {
    // Caixa principal do bloco (mesma logica do hl-overlay amarelo)
    const rect = blockEl.querySelector('rect:not(.hl-overlay):not(.search-hit)');
    if (!rect) return null;
    const x = parseFloat(rect.getAttribute('x'));
    const y = parseFloat(rect.getAttribute('y'));
    const w = parseFloat(rect.getAttribute('width'));
    const h = parseFloat(rect.getAttribute('height'));
    if (![x, y, w, h].every(Number.isFinite)) return null;
    const pad = 3;
    const hl = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    hl.setAttribute('class', 'search-hit');
    hl.setAttribute('x', x - pad);
    hl.setAttribute('y', y - pad);
    hl.setAttribute('width', w + 2 * pad);
    hl.setAttribute('height', h + 2 * pad);
    hl.setAttribute('rx', '2.5');
    // Insere LOGO APOS o rect original: assim a cor tinge o fundo do bloco
    // (rect opaco coberto por overlay semi-transparente) mas o <text> com
    // o nome da variavel, que vem depois do rect no DOM, fica por cima.
    rect.insertAdjacentElement('afterend', hl);
    return hl;
  }

  function matchesQuery(name, q) {
    if (!q) return false;
    return name.toLowerCase().indexOf(q) !== -1;
  }

  // Acha matches na pagina atual usando os data-attrs do SVG (case-insensitive)
  function findCurrentPageHits(q) {
    const out = [];
    const svg = document.querySelector('#viewer svg');
    if (!svg || !q) return out;
    const blocks = svg.querySelectorAll('[data-bit], [data-analog], [data-output-bit], [data-const]');
    blocks.forEach(el => {
      const raw =
        el.getAttribute('data-bit') ||
        el.getAttribute('data-analog') ||
        el.getAttribute('data-output-bit') ||
        el.getAttribute('data-const') || '';
      if (matchesQuery(raw, q)) {
        let kind = 'bit';
        if (el.hasAttribute('data-analog')) kind = 'analog';
        else if (el.hasAttribute('data-output-bit')) kind = 'output';
        else if (el.hasAttribute('data-const')) kind = 'const';
        out.push({ el, name: raw, kind });
      }
    });
    return out;
  }

  // Walk no VAR_INDEX uma vez so. Retorna:
  //   pages  -> Set de safe_page_ids com qualquer match (inclui currentPage)
  //   others -> [{name, page, kind}] pro dropdown (exclui currentPage)
  function computeIndexMatches(q) {
    const out = { pages: new Set(), others: [] };
    if (!q) return out;
    for (const [name, ent] of Object.entries(VAR_INDEX)) {
      if (!matchesQuery(name, q)) continue;
      for (const pg of ent.pages) {
        out.pages.add(pg);
        if (pg !== currentPage) {
          out.others.push({ name, page: pg, kind: ent.kind });
        }
      }
    }
    out.others.sort((a, b) =>
      a.name.localeCompare(b.name) || a.page.localeCompare(b.page));
    return out;
  }

  // Lista ordenada (pela ordem de PAGES) das paginas que tem matches.
  // Usada pra calcular "proxima pagina" na navegacao com setas.
  function orderedPagesWithHits(pageSet) {
    return pageOrder().filter(safe => pageSet.has(safe));
  }

  function focusHit(i) {
    if (!currentHits.length) return;
    // Desmarca anterior
    currentHits.forEach(h => {
      const overlay = h.el.querySelector(':scope > .search-hit');
      if (overlay) overlay.classList.remove('current');
    });
    // Normaliza indice
    const n = currentHits.length;
    currentIdx = ((i % n) + n) % n;
    const target = currentHits[currentIdx];
    const overlay = target.el.querySelector(':scope > .search-hit');
    if (overlay) overlay.classList.add('current');
    // Centraliza no viewport (smooth)
    try {
      target.el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
    } catch (e) { /* navegador antigo */ }
    updateCounter();
  }

  function updateCounter() {
    const n = currentHits.length;
    const other = parseInt(countEl.dataset.other || '0', 10);
    const totalGlobal = n + other;
    if (!input.value.trim()) {
      countEl.textContent = '-';
      countEl.classList.remove('has-hits', 'no-hits');
      btnPrev.disabled = btnNext.disabled = true;
      return;
    }
    if (n === 0) {
      countEl.textContent = other > 0 ? `0 +${other}` : '0';
      countEl.classList.remove('has-hits');
      countEl.classList.toggle('no-hits', other === 0);
      // Enquanto houver QUALQUER match (mesmo em outra pagina), as setas
      // podem pular pra la -- entao mantemos prev/next habilitados.
      btnPrev.disabled = btnNext.disabled = (totalGlobal === 0);
      countEl.title = other > 0
        ? `Nenhum match na página atual; ${other} em outras páginas (setas pulam pra la)`
        : 'Nada encontrado';
      return;
    }
    const pos = currentIdx >= 0 ? (currentIdx + 1) : 1;
    countEl.textContent = other > 0 ? `${pos}/${n} +${other}` : `${pos}/${n}`;
    countEl.classList.add('has-hits');
    countEl.classList.remove('no-hits');
    // Habilitado se ha mais de 1 match global (entao setas podem avancar
    // pra dentro da pagina OU pular pra outra pagina).
    btnPrev.disabled = btnNext.disabled = (totalGlobal < 2);
    countEl.title = other > 0
      ? `${n} na página, ${other} em outras (setas atravessam páginas)`
      : `${n} match(es) na página`;
  }

  function renderDropdown(otherHits) {
    if (!otherHits.length) {
      // So mostra dropdown se nao tem nada na pagina atual tambem
      if (!currentHits.length && input.value.trim()) {
        dropdown.innerHTML = '<div class="vs-empty">Nada encontrado</div>';
        dropdown.classList.add('open');
      } else {
        dropdown.classList.remove('open');
        dropdown.innerHTML = '';
      }
      return;
    }
    const parts = [];
    parts.push(`<div class="vs-section">Outras páginas (${otherHits.length})</div>`);
    for (const row of otherHits.slice(0, 50)) {
      const pname = pageName(row.page);
      const tag = row.kind === 'analog' ? 'analog' : 'bit';
      parts.push(
        `<div class="vs-row" data-page="${row.page}" data-name="${row.name}">` +
          `<span class="vs-name">${escapeHtml(row.name)}</span>` +
          `<span class="vs-page">${escapeHtml(pname)}</span>` +
          `<span class="vs-tag tag-${tag}">${tag}</span>` +
        `</div>`
      );
    }
    if (otherHits.length > 50) {
      parts.push(`<div class="vs-empty">+${otherHits.length - 50} mais... refine a busca</div>`);
    }
    dropdown.innerHTML = parts.join('');
    dropdown.classList.add('open');
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function run(opts) {
    opts = opts || {};
    const q = input.value.trim().toLowerCase();
    lastQuery = q;
    clearHighlights();
    if (!q) {
      countEl.dataset.other = '0';
      pagesWithHits = [];
      pendingFocus = null;
      clearTabHighlights();
      dropdown.classList.remove('open');
      updateCounter();
      return;
    }
    // Walk no VAR_INDEX uma vez so (rows pro dropdown + paginas com hits)
    const idx = computeIndexMatches(q);
    pagesWithHits = orderedPagesWithHits(idx.pages);
    applyTabHighlights(idx.pages);
    // Hits na pagina atual (pinta overlays cyan)
    const hits = findCurrentPageHits(q);
    hits.forEach(h => paintHit(h.el));
    currentHits = hits;
    countEl.dataset.other = String(idx.others.length);
    // Em reapply (apos troca de pagina disparada por click no dropdown ou
    // navegacao cross-page com setas), nao re-abre o dropdown -- o usuario
    // acabou de escolher onde ir.
    if (opts.suppressDropdown) {
      dropdown.classList.remove('open');
    } else {
      renderDropdown(idx.others);
    }
    if (currentHits.length) {
      // Decide indice inicial: 'last' quando viemos de Shift+Enter cruzando
      // paginas pra tras; 'first' (default) caso contrario.
      let initial = 0;
      if (pendingFocus === 'last') {
        initial = currentHits.length - 1;
      } else if (opts.preserveIdx && currentIdx >= 0) {
        initial = Math.min(currentIdx, currentHits.length - 1);
      }
      pendingFocus = null;
      focusHit(initial);
    } else {
      pendingFocus = null;
      updateCounter();
    }
  }

  // Pula pra uma pagina especifica e marca como deve focar o primeiro ou o
  // ultimo hit apos o re-paint. Usado pela navegacao cross-page.
  function jumpToPage(safe, focus) {
    pendingFocus = focus;     // 'first' | 'last'
    const name = pageName(safe);
    // loadPage e async (fetch + innerHTML + reapply); pendingFocus persiste
    // ate run() le-lo no proximo reapply.
    loadPage(safe, name);
  }

  // Proximo match na ordem global (avanca dentro da pagina, depois pula pra
  // proxima pagina com matches; faz wrap no fim).
  function goNext() {
    if (currentHits.length && currentIdx + 1 < currentHits.length) {
      focusHit(currentIdx + 1);
      return;
    }
    if (pagesWithHits.length === 0) return;
    if (pagesWithHits.length === 1 && pagesWithHits[0] === currentPage) {
      // So essa pagina tem matches -- wrap local pro inicio
      if (currentHits.length) focusHit(0);
      return;
    }
    const i = pagesWithHits.indexOf(currentPage);
    // Se a pagina atual nao tem matches (i === -1), comeca do indice 0;
    // senao avanca pra proxima, com wrap.
    const next = (i === -1)
      ? pagesWithHits[0]
      : pagesWithHits[(i + 1) % pagesWithHits.length];
    jumpToPage(next, 'first');
  }

  // Match anterior na ordem global (espelho de goNext).
  function goPrev() {
    if (currentHits.length && currentIdx > 0) {
      focusHit(currentIdx - 1);
      return;
    }
    if (pagesWithHits.length === 0) return;
    if (pagesWithHits.length === 1 && pagesWithHits[0] === currentPage) {
      if (currentHits.length) focusHit(currentHits.length - 1);
      return;
    }
    const i = pagesWithHits.indexOf(currentPage);
    const prev = (i === -1)
      ? pagesWithHits[pagesWithHits.length - 1]
      : pagesWithHits[(i - 1 + pagesWithHits.length) % pagesWithHits.length];
    jumpToPage(prev, 'last');
  }

  function scheduleRun() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { debounceTimer = null; run(); }, 80);
  }

  input.addEventListener('input', scheduleRun);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (e.shiftKey) goPrev(); else goNext();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      goNext();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      goPrev();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      input.value = '';
      run();
      input.blur();
    }
  });
  btnNext.addEventListener('click', () => goNext());
  btnPrev.addEventListener('click', () => goPrev());
  btnClear.addEventListener('click', () => {
    input.value = '';
    run();
    input.focus();
  });

  // Click em row do dropdown -> troca pagina e re-aplica busca
  dropdown.addEventListener('click', (e) => {
    const row = e.target.closest('.vs-row');
    if (!row) return;
    const safe = row.getAttribute('data-page');
    if (!safe) return;
    const pname = pageName(safe);
    loadPage(safe, pname);  // loadPage chama varSearch.reapply() no final
    dropdown.classList.remove('open');
  });

  // Fecha dropdown ao clicar fora
  document.addEventListener('click', (e) => {
    if (!document.getElementById('var-search-wrap').contains(e.target)) {
      dropdown.classList.remove('open');
    }
  });
  input.addEventListener('focus', () => {
    if (input.value.trim()) {
      // re-mostra dropdown se ha resultados off-page
      renderDropdown(computeIndexMatches(input.value.trim().toLowerCase()).others);
    }
  });

  // Atalho global: "/" foca a busca (ignora se ja esta em input/textarea/contenteditable)
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t === input) return;
    const tag = (t && t.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable)) return;
    e.preventDefault();
    input.focus();
    input.select();
  });

  // API publica pro loadPage()
  varSearch = {
    reapply() {
      if (!input.value.trim()) return;
      // Re-roda a busca na nova pagina; suprime o dropdown porque o usuario
      // acabou de escolher pra onde ir (ou veio de outro caminho).
      run({ suppressDropdown: true });
    }
  };
})();

// =============================================================================
// Checkboxes dos <group> -- estado persistido no servidor por DEVID
// =============================================================================
let groupState = {};   // {group_id (string): true}

async function loadGroupState() {
  try {
    const r = await fetch(withD('/group-state'));
    const d = await r.json();
    groupState = {};
    for (const id of (d.checked || [])) groupState[String(id)] = true;
    applyGroupState();
  } catch (e) {
    console.warn('falha ao carregar group-state:', e);
  }
}

function applyGroupState() {
  document.querySelectorAll('.group-grp').forEach(g => {
    const id = g.getAttribute('data-group-id');
    g.classList.toggle('checked', !!groupState[id]);
  });
}

document.getElementById('viewer').addEventListener('click', async (e) => {
  const cb = e.target.closest('.group-checkbox');
  if (!cb) return;
  const id = cb.getAttribute('data-group-id');
  if (!id) return;
  const next = !groupState[id];
  if (next) groupState[id] = true;
  else delete groupState[id];
  applyGroupState();
  try {
    await fetch(withD('/group-state'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group_id: id, checked: next})
    });
  } catch (err) {
    console.warn('falha ao salvar group-state:', err);
  }
});


// =============================================================================
// Bloco de notas -- abas Rele / Pagina, autosave por DEVID
// =============================================================================
let notesCtl = null;  // expose para hooks externos (page-switch)

(function setupNotes() {
  const panel    = document.getElementById('notes-panel');
  const editor   = document.getElementById('notes-editor');
  const toolbar  = document.getElementById('notes-toolbar');
  const tabsEl   = document.getElementById('notes-tabs');
  const toggleBt = document.getElementById('notes-toggle');
  const closeBt  = document.getElementById('notes-close');
  const statusEl = document.getElementById('notes-status');
  const pageNameEl = document.getElementById('notes-page-name');
  const OPEN_KEY = 'sel411-notes-open';
  const SCOPE_KEY = 'sel411-notes-scope';

  // Estado em memoria
  let scope = 'relay';   // 'relay' | 'page'
  let scopePage = null;  // safe page id quando scope==='page'
  let cache = { html_relay: '', pages: {} };
  let saveTimer = null;
  let lastSaved = '';
  let suppressInput = false;

  function setOpen(open) {
    document.body.classList.toggle('notes-open', open);
    panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    toggleBt.setAttribute('aria-expanded', open ? 'true' : 'false');
    try { localStorage.setItem(OPEN_KEY, open ? '1' : '0'); } catch (e) {}
  }
  try { setOpen(localStorage.getItem(OPEN_KEY) === '1'); } catch (e) { setOpen(false); }

  toggleBt.addEventListener('click', () => {
    setOpen(!document.body.classList.contains('notes-open'));
    if (document.body.classList.contains('notes-open')) editor.focus();
  });
  closeBt.addEventListener('click', () => setOpen(false));

  // --- Helpers ---
  function pageDisplayName(safe) {
    if (!safe) return '-';
    for (const [name, s] of PAGES) if (s === safe) return name;
    return safe;
  }
  function currentContent() {
    if (scope === 'relay') return cache.html_relay || '';
    return (scopePage && cache.pages[scopePage]) || '';
  }
  function tabFor(s) {
    return tabsEl.querySelector(`.notes-tab[data-scope="${s}"]`);
  }
  function updateTabIndicators() {
    tabFor('relay').classList.toggle('has-note', !!(cache.html_relay && cache.html_relay.trim()));
    const pageHas = !!(scopePage && cache.pages[scopePage] && cache.pages[scopePage].trim());
    tabFor('page').classList.toggle('has-note', pageHas);
    updatePageNavIndicators();
  }
  function updatePageNavIndicators() {
    document.querySelectorAll('#pages button[data-page]').forEach(btn => {
      const safe = btn.getAttribute('data-page');
      const has = !!(cache.pages[safe] && cache.pages[safe].trim());
      btn.classList.toggle('has-note', has);
    });
  }
  function updatePageTabLabel() {
    pageNameEl.textContent = scopePage ? pageDisplayName(scopePage) : '-';
    // Pagina atual definida => habilita a aba; caso contrario, desabilita
    const tab = tabFor('page');
    tab.disabled = !scopePage;
    tab.style.opacity = scopePage ? '1' : '0.5';
  }
  function loadEditorFromScope() {
    suppressInput = true;
    editor.innerHTML = currentContent();
    lastSaved = editor.innerHTML;
    suppressInput = false;
    if (scope === 'page' && !scopePage) {
      statusEl.className = '';
      statusEl.textContent = '(sem página ativa)';
      editor.setAttribute('contenteditable', 'false');
    } else {
      editor.setAttribute('contenteditable', 'true');
      statusEl.className = '';
      statusEl.textContent = lastSaved ? 'salvo' : '-';
    }
  }
  function setActiveTab(s) {
    if (s === 'page' && !scopePage) return;   // não tem como ativar sem página
    if (scope === s) return;
    scope = s;
    tabsEl.querySelectorAll('.notes-tab').forEach(t => {
      const on = t.getAttribute('data-scope') === s;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    loadEditorFromScope();
    try { localStorage.setItem(SCOPE_KEY, s); } catch (e) {}
  }

  tabsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.notes-tab');
    if (!btn || btn.disabled) return;
    setActiveTab(btn.getAttribute('data-scope'));
    editor.focus();
  });

  // Toolbar
  toolbar.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cmd]');
    if (!btn) return;
    e.preventDefault();
    const cmd = btn.getAttribute('data-cmd');
    const arg = btn.getAttribute('data-arg') || null;
    editor.focus();
    try { document.execCommand(cmd, false, arg); }
    catch (err) { console.warn('execCommand falhou:', cmd, err); }
    scheduleSave();
  });

  editor.addEventListener('input', () => { if (!suppressInput) scheduleSave(); });
  editor.addEventListener('blur', () => { if (saveTimer) flushSave(); });

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    statusEl.className = 'saving';
    statusEl.textContent = '...';
    saveTimer = setTimeout(flushSave, 700);
  }
  async function flushSave() {
    saveTimer = null;
    if (scope === 'page' && !scopePage) return;
    const html = editor.innerHTML;
    if (html === lastSaved) {
      statusEl.className = ''; statusEl.textContent = 'salvo'; return;
    }
    // Atualiza cache local antes do POST para indicadores ficarem em sincronia
    if (scope === 'relay') cache.html_relay = html;
    else cache.pages[scopePage] = html;
    updateTabIndicators();
    try {
      const body = scope === 'relay'
        ? {scope: 'relay', html: html}
        : {scope: 'page', page: scopePage, html: html};
      const r = await fetch(withD('/note'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      if (!r.ok) throw new Error('http ' + r.status);
      lastSaved = html;
      statusEl.className = ''; statusEl.textContent = 'salvo';
    } catch (err) {
      statusEl.className = 'error'; statusEl.textContent = 'erro';
      console.warn('falha ao salvar nota:', err);
    }
  }

  async function loadNotes() {
    try {
      const r = await fetch(withD('/note'));
      const d = await r.json();
      cache.html_relay = d.html_relay || '';
      cache.pages = d.pages || {};
      // Restaura escopo preferido
      let saved = 'relay';
      try { saved = localStorage.getItem(SCOPE_KEY) || 'relay'; } catch (e) {}
      scope = (saved === 'page' && scopePage) ? 'page' : 'relay';
      tabsEl.querySelectorAll('.notes-tab').forEach(t => {
        const on = t.getAttribute('data-scope') === scope;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      loadEditorFromScope();
      updateTabIndicators();
    } catch (err) {
      console.warn('falha ao carregar notas:', err);
    }
  }

  // API exposta para hooks externos (page-switch)
  notesCtl = {
    // Trocar de diagrama troca o rele, e portanto as notas inteiras.
    reload(safe) {
      if (saveTimer) flushSave();
      cache.html_relay = ''; cache.pages = {};
      scopePage = safe || null;
      updatePageTabLabel();
      loadNotes();
    },
    onPageChange(safe) {
      const wasPageScope = (scope === 'page');
      // Flush pendente se estivessemos editando uma nota de pagina
      if (saveTimer && wasPageScope) flushSave();
      scopePage = safe || null;
      updatePageTabLabel();
      // Se a aba ativa e Pagina, troca conteudo para a nova pagina
      if (wasPageScope) loadEditorFromScope();
      updateTabIndicators();
    },
  };

  // Define a pagina inicial ANTES de loadNotes() para que o escopo salvo
  // ('page') possa ser restaurado corretamente.
  scopePage = currentPage || null;
  updatePageTabLabel();
})();

// =============================================================================
// Ferramentas: Mouse (select) / Highlighter
// =============================================================================
let currentTool = 'select';
// highlightsState[pageSafeId] = Set(itemIds)
const highlightsState = {};

(function setupTools() {
  const btnSel = document.getElementById('tool-select');
  const btnHl  = document.getElementById('tool-highlight');
  const TOOL_KEY = 'sel411-current-tool';

  function applyTool(name) {
    currentTool = name;
    btnSel.classList.toggle('active', name === 'select');
    btnHl .classList.toggle('active', name === 'highlight');
    btnSel.setAttribute('aria-checked', name === 'select' ? 'true' : 'false');
    btnHl .setAttribute('aria-checked', name === 'highlight' ? 'true' : 'false');
    document.body.classList.toggle('tool-highlight', name === 'highlight');
    try { localStorage.setItem(TOOL_KEY, name); } catch (e) {}
  }
  try {
    const saved = localStorage.getItem(TOOL_KEY);
    applyTool(saved === 'highlight' ? 'highlight' : 'select');
  } catch (e) { applyTool('select'); }

  btnSel.addEventListener('click', () => applyTool('select'));
  btnHl .addEventListener('click', () => applyTool('highlight'));
})();

// =============================================================================
// Zoom / pan do diagrama
// O SVG e renderizado com viewBox + preserveAspectRatio, entao basta escalar a
// largura: zoom 1 = a largura util do #viewer, >1 estoura e o proprio #viewer
// (overflow:auto) vira a area de scroll. Como toda a matematica de coordenadas
// usa getScreenCTM(), marcador/rubber-band continuam corretos em qualquer zoom.
//
// Modos:
//   'page'   (padrao) -- pagina inteira ocupando o maximo possivel da tela
//   'width'           -- ajustado a largura (pode passar da altura -> scroll)
//   'manual'          -- usuario mexeu no +/-/roda; zoom fixo ate pedir ajuste
// Nos modos de ajuste o zoom e recalculado a cada troca de pagina e a cada
// mudanca de tamanho do #viewer (janela, painel lateral, quebra do header).
// =============================================================================
zoomCtl = (function setupZoom() {
  const viewer  = document.getElementById('viewer');
  const btnIn   = document.getElementById('zoom-in');
  const btnOut  = document.getElementById('zoom-out');
  const btnPct  = document.getElementById('zoom-level');
  const btnWide = document.getElementById('zoom-fit-width');
  const btnPage = document.getElementById('zoom-fit-page');
  if (!viewer || !btnIn) return null;

  const ZOOM_MIN = 0.15, ZOOM_MAX = 8, STEP = 1.25;
  const PAD = 24;          // padding do #viewer (12px de cada lado)
  const SAFETY = 0.995;    // folga pra barra de rolagem não reaparecer
  const ZOOM_KEY = 'sel411-gle-zoom';
  const MODE_KEY = 'sel411-gle-zoom-mode';
  let zoom = 1;
  let mode = 'page';

  function render() {
    viewer.style.setProperty('--gle-zoom', String(zoom));
    btnPct.textContent = Math.round(zoom * 100) + '%';
    btnIn.disabled  = zoom >= ZOOM_MAX - 1e-6;
    btnOut.disabled = zoom <= ZOOM_MIN + 1e-6;
    btnWide.classList.toggle('active', mode === 'width');
    btnPage.classList.toggle('active', mode === 'page');
    try {
      localStorage.setItem(ZOOM_KEY, String(zoom));
      localStorage.setItem(MODE_KEY, mode);
    } catch (e) {}
  }

  // anchor = {clientX, clientY} -- ponto da tela que deve continuar sob o cursor
  function setZoom(z, anchor) {
    const prev = zoom;
    zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
    if (Math.abs(zoom - prev) < 1e-9) { render(); return; }
    const rect = viewer.getBoundingClientRect();
    const ax = anchor ? anchor.clientX - rect.left : viewer.clientWidth / 2;
    const ay = anchor ? anchor.clientY - rect.top  : viewer.clientHeight / 2;
    // posicao do ponto no conteudo, normalizada pelo zoom anterior
    const cx = (viewer.scrollLeft + ax) / prev;
    const cy = (viewer.scrollTop  + ay) / prev;
    render();
    viewer.scrollLeft = cx * zoom - ax;
    viewer.scrollTop  = cy * zoom - ay;
  }

  function manualZoom(z, anchor) {
    mode = 'manual';
    setZoom(z, anchor);
  }

  // Zoom que faz a pagina inteira caber. Usa a proporcao do viewBox (e nao a
  // altura medida) pra nao depender do zoom atual nem da barra de rolagem.
  function pageZoom() {
    const svg = viewer.querySelector('svg');
    if (!svg) return null;
    const vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    if (vb.length !== 4 || !vb[2] || !vb[3]) return null;
    const aspect = vb[3] / vb[2];                     // altura/largura do desenho
    const availW = viewer.clientWidth  - PAD;
    const availH = viewer.clientHeight - PAD;
    if (availW <= 0 || availH <= 0) return null;
    // largura(z) = availW*z  ->  altura(z) = availW*z*aspect <= availH
    return Math.min(1, (availH / (availW * aspect)) * SAFETY);
  }

  // Aplica o modo de ajuste corrente (no-op em 'manual')
  function applyFit(force) {
    if (mode === 'manual' && !force) return;
    const z = mode === 'width' ? 1 : pageZoom();
    if (z === null) return;
    const prev = zoom;
    zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
    render();
    if (Math.abs(zoom - prev) > 1e-9) { viewer.scrollTop = 0; viewer.scrollLeft = 0; }
  }

  function fitPage()  { mode = 'page';  applyFit(true); }
  function fitWidth() { mode = 'width'; applyFit(true); }

  btnIn  .addEventListener('click', () => manualZoom(zoom * STEP));
  btnOut .addEventListener('click', () => manualZoom(zoom / STEP));
  btnPct .addEventListener('click', fitPage);
  btnWide.addEventListener('click', fitWidth);
  btnPage.addEventListener('click', fitPage);

  // Ctrl/Cmd + roda = zoom ancorado no cursor
  viewer.addEventListener('wheel', (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    manualZoom(zoom * Math.exp(-e.deltaY * 0.0015), {clientX: e.clientX, clientY: e.clientY});
  }, {passive: false});

  // Atalhos: + / - / 0 (pagina inteira) / 9 (largura)
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    const tag = (t && t.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable)) return;
    if (e.key === '+' || e.key === '=') { e.preventDefault(); manualZoom(zoom * STEP); }
    else if (e.key === '-' || e.key === '_') { e.preventDefault(); manualZoom(zoom / STEP); }
    else if (e.key === '0') { e.preventDefault(); fitPage(); }
    else if (e.key === '9') { e.preventDefault(); fitWidth(); }
  });

  // Reajusta quando o #viewer muda de tamanho (janela, painel lateral oculto,
  // header quebrando linha). Debounce por rAF; em 'manual' nao faz nada.
  let rafId = null, lastW = 0, lastH = 0;
  const onResize = () => {
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      const w = viewer.clientWidth, h = viewer.clientHeight;
      if (Math.abs(w - lastW) < 2 && Math.abs(h - lastH) < 2) return;
      lastW = w; lastH = h;
      applyFit(false);
    });
  };
  if (window.ResizeObserver) new ResizeObserver(onResize).observe(viewer);
  window.addEventListener('resize', onResize);

  // --- Pan: arrastar com o botao do meio (qualquer ferramenta) ou com o
  // esquerdo na ferramenta Mouse. So entra em modo pan depois de mover alguns
  // px, pra nao roubar cliques (ex.: checkbox de grupo).
  const PAN_THRESHOLD_PX = 4;
  let pan = null;

  viewer.addEventListener('mousedown', (e) => {
    const middle = e.button === 1;
    const leftSelect = e.button === 0 && currentTool === 'select' &&
                       !e.target.closest('.group-checkbox');
    if (!middle && !leftSelect) return;
    const scrollable = viewer.scrollWidth  > viewer.clientWidth + 1 ||
                       viewer.scrollHeight > viewer.clientHeight + 1;
    if (!scrollable) return;
    if (middle) e.preventDefault();
    pan = {
      x: e.clientX, y: e.clientY,
      left: viewer.scrollLeft, top: viewer.scrollTop,
      active: false,
    };
  });

  document.addEventListener('mousemove', (e) => {
    if (!pan) return;
    const dx = e.clientX - pan.x, dy = e.clientY - pan.y;
    if (!pan.active) {
      if (Math.hypot(dx, dy) < PAN_THRESHOLD_PX) return;
      pan.active = true;
      document.body.classList.add('panning');
    }
    e.preventDefault();
    viewer.scrollLeft = pan.left - dx;
    viewer.scrollTop  = pan.top  - dy;
  });

  function endPan() {
    if (!pan) return;
    pan = null;
    document.body.classList.remove('panning');
  }
  document.addEventListener('mouseup', endPan);
  document.addEventListener('mouseleave', endPan);

  // Estado inicial: modo salvo (padrao 'page' -- diagrama ja preenchendo a tela)
  try {
    const savedMode = localStorage.getItem(MODE_KEY);
    if (savedMode === 'width' || savedMode === 'manual') mode = savedMode;
    const saved = parseFloat(localStorage.getItem(ZOOM_KEY));
    if (mode === 'manual' && isFinite(saved) && saved > 0) {
      zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, saved));
    }
  } catch (e) {}
  render();
  applyFit(false);
  // O layout so esta final depois do primeiro frame (fontes, header quebrando,
  // barra de rolagem do painel) -- reajusta uma vez.
  requestAnimationFrame(() => applyFit(false));
  window.addEventListener('load', () => applyFit(false));

  return {
    onPageChange() { applyFit(false); },
  };
})();

function connectionId(poly) {
  return 'c-' + (poly.getAttribute('data-src') || '?') +
         '-'  + (poly.getAttribute('data-src-port') || '0') +
         '-'  + (poly.getAttribute('data-dst') || '?') +
         '-'  + (poly.getAttribute('data-dst-port') || '0');
}

function findHighlightableTarget(eventTarget) {
  // Sobe pelo DOM ate achar um alvo "highlightavel". Ignora cliques em
  // checkboxes de <group>.
  if (eventTarget.closest('.group-checkbox')) return null;
  const block = eventTarget.closest('.symbol-grp, .gate-grp');
  if (block) return {kind: 'block', el: block, id: block.id};
  const poly = eventTarget.closest('polyline.connection');
  if (poly) return {kind: 'line', el: poly, id: connectionId(poly)};
  return null;
}

function applyBlockHighlight(blockEl) {
  if (blockEl.querySelector(':scope > .hl-overlay')) return;
  const rect = blockEl.querySelector('rect:not(.hl-overlay)');
  if (!rect) return;
  const x = parseFloat(rect.getAttribute('x'));
  const y = parseFloat(rect.getAttribute('y'));
  const w = parseFloat(rect.getAttribute('width'));
  const h = parseFloat(rect.getAttribute('height'));
  const pad = 2.5;
  const hl = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  hl.setAttribute('class', 'hl-overlay');
  hl.setAttribute('x', x - pad);
  hl.setAttribute('y', y - pad);
  hl.setAttribute('width', w + 2 * pad);
  hl.setAttribute('height', h + 2 * pad);
  hl.setAttribute('rx', '2.5');
  blockEl.insertBefore(hl, blockEl.firstChild);
  blockEl.classList.add('highlighted');
}

function removeBlockHighlight(blockEl) {
  const overlay = blockEl.querySelector(':scope > .hl-overlay');
  if (overlay) overlay.remove();
  blockEl.classList.remove('highlighted');
}

function applyLineHighlight(poly) {
  if (poly.parentNode.querySelector(
        `polyline.hl-line-overlay[data-for="${CSS.escape(connectionId(poly))}"]`)) return;
  const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  overlay.setAttribute('class', 'hl-line-overlay');
  overlay.setAttribute('data-for', connectionId(poly));
  overlay.setAttribute('points', poly.getAttribute('points') || '');
  poly.parentNode.insertBefore(overlay, poly);
  poly.classList.add('highlighted');
}

function removeLineHighlight(poly) {
  const id = connectionId(poly);
  const overlay = poly.parentNode.querySelector(
    `polyline.hl-line-overlay[data-for="${CSS.escape(id)}"]`);
  if (overlay) overlay.remove();
  poly.classList.remove('highlighted');
}

function applyHighlightsForCurrentPage() {
  const ids = highlightsState[currentPage];
  if (!ids) return;
  const svg = document.querySelector('#viewer svg');
  if (!svg) return;
  ids.forEach(itemId => {
    if (itemId.startsWith('c-')) {
      svg.querySelectorAll('polyline.connection').forEach(p => {
        if (connectionId(p) === itemId) applyLineHighlight(p);
      });
    } else {
      const blk = svg.querySelector(`.symbol-grp#${CSS.escape(itemId)}, .gate-grp#${CSS.escape(itemId)}`);
      if (blk) applyBlockHighlight(blk);
    }
  });
}

async function loadHighlights() {
  try {
    const r = await fetch(withD('/highlights'));
    const d = await r.json();
    // Zera antes: os marcadores sao do rele do diagrama, nao do navegador.
    for (const k of Object.keys(highlightsState)) delete highlightsState[k];
    const pages = d.pages || {};
    for (const [pg, items] of Object.entries(pages)) {
      highlightsState[pg] = new Set(Object.keys(items || {}));
    }
    applyHighlightsForCurrentPage();
  } catch (e) {
    console.warn('falha ao carregar highlights:', e);
  }
}

async function postHighlight(page, itemId, highlighted) {
  try {
    await fetch(withD('/highlights'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({page: page, item_id: itemId, highlighted: highlighted})
    });
  } catch (e) {
    console.warn('falha ao salvar highlight:', e);
  }
}

// --- Utilitarios de coordenadas / geometria em SVG ---
function svgPointFromEvent(svg, e) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return {x: 0, y: 0};
  const p = pt.matrixTransform(ctm.inverse());
  return {x: p.x, y: p.y};
}
function parsePolylinePoints(poly) {
  return (poly.getAttribute('points') || '').trim().split(/\s+/).map(s => {
    const [x, y] = s.split(',').map(parseFloat);
    return {x, y};
  }).filter(p => !isNaN(p.x) && !isNaN(p.y));
}
function distPointSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx*dx + dy*dy;
  if (len2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t*dx), py - (y1 + t*dy));
}
function findNearestLine(svg, x, y, maxDist) {
  let best = null, bestD = maxDist;
  svg.querySelectorAll('polyline.connection').forEach(p => {
    const pts = parsePolylinePoints(p);
    for (let i = 1; i < pts.length; i++) {
      const d = distPointSegment(x, y, pts[i-1].x, pts[i-1].y, pts[i].x, pts[i].y);
      if (d < bestD) { bestD = d; best = p; }
    }
  });
  return best;
}
function rectIntersectsBlock(rx0, ry0, rx1, ry1, blockEl) {
  const rect = blockEl.querySelector('rect:not(.hl-overlay)');
  if (!rect) return false;
  const x = parseFloat(rect.getAttribute('x'));
  const y = parseFloat(rect.getAttribute('y'));
  const w = parseFloat(rect.getAttribute('width'));
  const h = parseFloat(rect.getAttribute('height'));
  return !(x > rx1 || x + w < rx0 || y > ry1 || y + h < ry0);
}
function segmentInRect(x1, y1, x2, y2, rx0, ry0, rx1, ry1) {
  // Endpoint dentro do rect
  if (x1 >= rx0 && x1 <= rx1 && y1 >= ry0 && y1 <= ry1) return true;
  if (x2 >= rx0 && x2 <= rx1 && y2 >= ry0 && y2 <= ry1) return true;
  // Segmentos ortogonais (caso comum em GLE)
  if (x1 === x2) {
    return x1 >= rx0 && x1 <= rx1 &&
           Math.min(y1, y2) <= ry1 && Math.max(y1, y2) >= ry0;
  }
  if (y1 === y2) {
    return y1 >= ry0 && y1 <= ry1 &&
           Math.min(x1, x2) <= rx1 && Math.max(x1, x2) >= rx0;
  }
  return false;
}
function rectIntersectsPolyline(rx0, ry0, rx1, ry1, poly) {
  const pts = parsePolylinePoints(poly);
  for (let i = 1; i < pts.length; i++) {
    if (segmentInRect(pts[i-1].x, pts[i-1].y, pts[i].x, pts[i].y,
                      rx0, ry0, rx1, ry1)) return true;
  }
  return false;
}

// --- Toggle wrapper (single item) ---
function toggleHighlight(target) {
  const set = highlightsState[currentPage] || (highlightsState[currentPage] = new Set());
  const isOn = set.has(target.id);
  if (isOn) {
    set.delete(target.id);
    if (target.kind === 'block') removeBlockHighlight(target.el);
    else removeLineHighlight(target.el);
  } else {
    set.add(target.id);
    if (target.kind === 'block') applyBlockHighlight(target.el);
    else applyLineHighlight(target.el);
  }
  postHighlight(currentPage, target.id, !isOn);
}
function highlightOn(target) {
  const set = highlightsState[currentPage] || (highlightsState[currentPage] = new Set());
  if (set.has(target.id)) return;
  set.add(target.id);
  if (target.kind === 'block') applyBlockHighlight(target.el);
  else applyLineHighlight(target.el);
  postHighlight(currentPage, target.id, true);
}

// --- State machine: mousedown -> [drag] -> mouseup ---
(function setupHighlightInteraction() {
  const viewer = document.getElementById('viewer');
  const DRAG_THRESHOLD_PX = 4;   // px de tela
  const LINE_MARGIN_SVG = 5;     // unidades SVG -- área de clique generosa
  let dragging = null;           // null | {svg, startScreen, startSvg, rubber}

  viewer.addEventListener('mousedown', (e) => {
    if (currentTool !== 'highlight') return;
    if (e.button !== 0) return;
    const svg = viewer.querySelector('svg');
    if (!svg) return;
    e.preventDefault();
    dragging = {
      svg,
      startScreen: {x: e.clientX, y: e.clientY},
      startSvg: svgPointFromEvent(svg, e),
      rubber: null,
      moved: false,
      directTarget: findHighlightableTarget(e.target),
    };
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const d = Math.hypot(e.clientX - dragging.startScreen.x,
                         e.clientY - dragging.startScreen.y);
    if (!dragging.moved && d < DRAG_THRESHOLD_PX) return;
    dragging.moved = true;
    // Cria rubber-band se ainda nao existe
    if (!dragging.rubber) {
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      r.setAttribute('class', 'rubber-band');
      dragging.svg.appendChild(r);
      dragging.rubber = r;
    }
    const p = svgPointFromEvent(dragging.svg, e);
    const x0 = Math.min(dragging.startSvg.x, p.x);
    const y0 = Math.min(dragging.startSvg.y, p.y);
    const w  = Math.abs(p.x - dragging.startSvg.x);
    const h  = Math.abs(p.y - dragging.startSvg.y);
    dragging.rubber.setAttribute('x', x0);
    dragging.rubber.setAttribute('y', y0);
    dragging.rubber.setAttribute('width', w);
    dragging.rubber.setAttribute('height', h);
  });

  document.addEventListener('mouseup', (e) => {
    if (!dragging) return;
    const drag = dragging;
    dragging = null;
    if (drag.rubber) drag.rubber.remove();

    if (!drag.moved) {
      // Single click
      if (drag.directTarget) {
        toggleHighlight(drag.directTarget);
        return;
      }
      // Click "vazio" -- procura linha proxima dentro da margem
      const near = findNearestLine(drag.svg, drag.startSvg.x, drag.startSvg.y,
                                   LINE_MARGIN_SVG);
      if (near) {
        toggleHighlight({kind: 'line', el: near, id: connectionId(near)});
      }
      return;
    }

    // Drag-select: marca tudo que intersecta o rect (apenas adiciona, nao toggla)
    const endSvg = svgPointFromEvent(drag.svg, e);
    const rx0 = Math.min(drag.startSvg.x, endSvg.x);
    const ry0 = Math.min(drag.startSvg.y, endSvg.y);
    const rx1 = Math.max(drag.startSvg.x, endSvg.x);
    const ry1 = Math.max(drag.startSvg.y, endSvg.y);
    drag.svg.querySelectorAll('.symbol-grp, .gate-grp').forEach(blk => {
      if (rectIntersectsBlock(rx0, ry0, rx1, ry1, blk)) {
        highlightOn({kind: 'block', el: blk, id: blk.id});
      }
    });
    drag.svg.querySelectorAll('polyline.connection').forEach(poly => {
      if (rectIntersectsPolyline(rx0, ry0, rx1, ry1, poly)) {
        highlightOn({kind: 'line', el: poly, id: connectionId(poly)});
      }
    });
  });

  // Esc cancela drag em andamento
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dragging) {
      if (dragging.rubber) dragging.rubber.remove();
      dragging = null;
    }
  });
})();


// =============================================================================
// Avaliacao da logica para propagar sinais atraves dos gates
// =============================================================================
// Avalia a arvore de um conector. O servidor manda ESTRUTURA
// (`pacct/web/glv/connectors.py`); a semantica mora aqui, uma vez so -- uma
// segunda implementacao de NOT/RTRIG/AND em Python divergiria desta, e a
// lista de gotchas deste projeto e' em boa parte isso acontecendo.
//
// `undefined` = indeterminado, e propaga: um E com uma entrada que ninguem
// leu nao e' 0, e' desconhecido. Mesma regra do `allKnown` do avaliador dos
// blocos desenhados.
// Pinta a equacao de um conector: cada VARIAVEL ganha a cor do estado dela,
// os operadores ficam neutros. A equacao vem em notacao SELOGIC do servidor
// (`*` E, `+` OU, `!` NAO, `↑`/`↓` borda) -- medido, nenhum bloco aritmetico
// aparece dentro de equacao de conector em todo o corpus, entao `*` e `+` nao
// sao ambiguos aqui.
//
// Escapa antes de colorir: os labels reais sao texto livre ("TC - LOP",
// "RX 50BF C/ BARRAS UNIDAS"), e a equacao carrega nomes de bit vindos do
// arquivo.
function renderConnectorEquation(equation, bits, digitals) {
  const esc = String(equation || '').replace(/[&<>]/g, ch => (
    {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[ch]));
  // Colore so' o que a propria rede declarou como bit. Casar por formato de
  // identificador seria errado nos dois sentidos: nomes SEL comecam com
  // digito (`52A`, `89CL01`, `3PO`), e uma constante do desenho (o preset `6`
  // de um contador) nao e' bit nenhum e nao pode sair pintada de
  // indeterminado.
  const known = new Set((bits || []).map(b => b.toUpperCase()));
  return esc.replace(/[A-Za-z0-9_]+/g, name => {
    if (!known.has(name.toUpperCase())) return name;
    const v = digitals[name.toUpperCase()];
    const cls = (v === 1) ? 'bit-1' : (v === 0) ? 'bit-0' : 'bit-unknown';
    return `<span class="conn-var ${cls}">${name}</span>`;
  });
}

// A secao Conectores da legenda: so' os da PAGINA ABERTA (a legenda e'
// contexto da pagina, e um label chega a 10 pontas). Fica escondida quando a
// pagina nao tem nenhum -- 107 dos 418 GLE do corpus usam conector, entao a
// maioria das telas nao deve ganhar uma secao vazia.
function renderConnectorsPanel(nets, netValue, digitals, currentPage) {
  const section = document.getElementById('connectors-section');
  const list = document.getElementById('connectors-list');
  if (!section || !list) return;
  const labels = Object.keys(nets || {}).sort();
  section.hidden = labels.length === 0;
  if (!labels.length) { list.innerHTML = ''; return; }
  list.innerHTML = labels.map(label => {
    const net = nets[label];
    const v = netValue[label];
    const cls = (v === 1) ? 'bit-1' : (v === 0) ? 'bit-0' : 'bit-unknown';
    const safeLabel = label.replace(/[&<>]/g, ch => (
      {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[ch]));
    // De onde vem o sinal, quando NAO e' desta pagina. E' a informacao que
    // faltava pra entender um conector que a pagina so' deriva: sem ela a
    // equacao aparece sem nada que diga onde ela e' desenhada.
    const where = (net.driver_page && net.driver_page !== currentPage)
      ? `<span class="conn-where">acionado em ${net.driver_page.replace(/_/g, ' ')}</span>`
      : '';
    return `<div class="conn-item">
      <div class="conn-head">
        <span class="legend-swatch conn-var ${cls}"></span>
        <span class="conn-name">${safeLabel}</span>${where}
      </div>
      <div class="conn-eq">${renderConnectorEquation(net.equation, net.bits, digitals)}</div>
    </div>`;
  }).join('');
}

function evalConnectorTree(node, digitals) {
  if (!node || !node.op) return undefined;
  switch (node.op) {
    case 'BIT': {
      const v = digitals[(node.name || '').toUpperCase()];
      return (v === 0 || v === 1) ? v : undefined;
    }
    case 'CONST': return node.value;
    case 'CUT':   return undefined;   // ramo cortado: nunca inventa valor
  }
  const args = (node.args || []).map(a => evalConnectorTree(a, digitals));
  if (args.some(v => v === undefined)) return undefined;
  switch (node.op) {
    case 'NOT':   return 1 - args[0];
    // Sem historico entre voltas do polling nao da' pra avaliar borda. E' o
    // que o avaliador dos blocos desenhados ja faz com RTRIG.
    case 'RTRIG':
    case 'FTRIG': return args[0];
    case 'AND':   return args.every(v => v === 1) ? 1 : 0;
    case 'OR':    return args.some(v => v === 1) ? 1 : 0;
    default:      return args[0];     // bloco de passagem
  }
}

// =============================================================================
// A PAGINA COMPILADA.
//
// A topologia de um desenho nao muda enquanto a pagina esta aberta: quem sao
// os blocos, que bit cada SYMBOL publica, que fio chega em que porta -- tudo
// isso e' atributo do SVG, escrito uma vez pelo servidor. `evaluatePage` lia
// tudo de volta do DOM a CADA leitura.
//
// Medido no Chrome, na pagina mais pesada do corpus de 418 GLE (118
// elementos, 83 conexoes, 588 nos SVG), o que isso custava por volta:
// 7 varreduras `querySelectorAll`, 118 `querySelector` aninhados (o `rect` de
// cada bloco), ~1238 `getAttribute` e ~806 escritas de classe -- com o rele
// parado, nenhuma delas mudando um pixel.
//
// Agora o SVG e' lido UMA vez, quando entra na tela, e a volta so' avalia e
// pinta. Cada no guarda a ultima classe que recebeu (`cls`), entao a pintura
// e' um diff: numa subestacao em repouso nao ha' escrita nenhuma.
// =============================================================================
let pageModel = null;
let drawnRev = null;
let drawnPage = null;

//: Qual das tres classes de estado o no ja' carrega. Lido do DOM na compilacao
//: e nao assumido vazio, porque uma pagina pode voltar do cache do cliente
//: (ver `loadPage`) ainda pintada da ultima vez que esteve aberta -- e o diff
//: partiria de "sem classe" e somaria a nova sem tirar a antiga.
function _currentClass(node, names) {
  if (!node) return '';
  for (const n of names) if (node.classList.contains(n)) return n;
  return '';
}

const _BIT_CLASSES = ['bit-1', 'bit-0', 'bit-unknown'];
const _WIRE_CLASSES = ['active', 'off', 'unknown'];

function compilePage(svg) {
  // SYMBOLs primeiro, gates depois -- exatamente a ordem em que o `elType`
  // antigo era preenchido (duas varreduras, nessa sequencia) e portanto a
  // ordem em que o laco de convergencia percorria os elementos. Uma ordem
  // diferente daria o mesmo ponto fixo, mas em outro numero de passadas, e o
  // laco tem teto de 10.
  const nodes = [];
  const byId = new Map();
  const add = (g, isSymbol) => {
    const id = g.id.startsWith('el-') ? g.id.slice(3) : g.id;
    const constStr = g.getAttribute('data-const');
    const n = {
      id, g, isSymbol,
      rect: g.querySelector('rect:not(.hl-overlay)'),
      type: isSymbol ? 'SYMBOL' : (g.getAttribute('data-type') || '?'),
      hasConst: constStr !== null,
      constVal: constStr === null ? NaN : Number(constStr),
      analog: (g.getAttribute('data-analog') || '').toUpperCase(),
      analogRaw: g.getAttribute('data-analog') || '',
      bit: (g.getAttribute('data-bit') || '').toUpperCase(),
      outBit: (g.getAttribute('data-output-bit') || '').toUpperCase(),
      connector: g.getAttribute('data-connector') || '',
      analogText: g.querySelector('.analog-value'),
      analogRect: g.querySelector('.element-analog'),
      incoming: null,
      cls: '',
    };
    n.cls = _currentClass(n.rect, _BIT_CLASSES);
    nodes.push(n);
    byId.set(id, n);
  };
  svg.querySelectorAll('.symbol-grp').forEach(g => add(g, true));
  svg.querySelectorAll('.gate-grp').forEach(g => add(g, false));

  const conns = [];
  svg.querySelectorAll('polyline.connection').forEach(c => {
    const e = {
      c,
      src: c.getAttribute('data-src') || '',
      dst: c.getAttribute('data-dst') || '',
      mod: c.getAttribute('data-sink-mod') || '',
      srcMod: c.getAttribute('data-src-mod') || '',
      port: parseInt(c.getAttribute('data-dst-port') || '0', 10) || 0,
      // Os pontos viram lista aqui e nao a cada volta: era um `getAttribute`
      // mais um `split` por conexao ativa, so' para achar os dots de juncao.
      pts: (c.getAttribute('points') || '').trim().split(/\s+/),
      cls: '',
    };
    e.cls = _currentClass(c, _WIRE_CLASSES);
    conns.push(e);
    const sink = byId.get(e.dst);
    if (sink) (sink.incoming = sink.incoming || []).push(e);
  });
  // Ordenado UMA vez. O `ins.sort(...)` vivia dentro do laco de convergencia,
  // entao o mesmo array era reordenado ate' dez vezes por leitura.
  for (const n of nodes) if (n.incoming) n.incoming.sort((a, b) => a.port - b.port);

  const dots = [];
  svg.querySelectorAll('circle.junction-dot').forEach(d => {
    dots.push({
      d,
      key: `${d.getAttribute('data-jx')},${d.getAttribute('data-jy')}`,
      on: d.classList.contains('active'),
    });
  });

  // Os SYMBOLs analogicos, ja' separados: a pintura dos valores inline
  // varria `[data-analog]` no SVG inteiro de novo.
  const analogNodes = nodes.filter(n => n.analogRaw);
  return {svg, nodes, byId, conns, dots, analogNodes};
}

// Avalia a logica da pagina sobre o modelo compilado. Mesma semantica do
// `evaluatePage` que lia o DOM: mesmos primitivos, mesma ordem de visita,
// mesmo teto de 10 passadas.
function evaluateModel(model, digitals, analogs, netValue) {
  const val = new Map();
  for (const n of model.nodes) {
    if (n.isSymbol) {
      // Constante (literal numerico, ex: 0, 1, 600). Nao consultar o rele.
      // Guardamos o valor REAL para que comparadores como EQ/LT/GT possam
      // comparar AMVs contra thresholds.
      if (n.hasConst) { if (!isNaN(n.constVal)) val.set(n.id, n.constVal); continue; }
      // SYMBOL analogico: valor vem dos canais Fast Meter, float bruto.
      if (n.analog) {
        const v = analogs[n.analog];
        if (typeof v === 'number' && !isNaN(v)) val.set(n.id, v);
        continue;
      }
      if (n.bit) {
        const v = digitals[n.bit];
        if (v === 0 || v === 1) val.set(n.id, v);   // null/undefined = indeterminado
      }
      continue;
    }
    // Bloco com bit de saida nomeado (PLT04, PCT03Q, AST01Q...): usa o valor
    // REAL do rele em vez de avaliar a logica.
    if (n.outBit) {
      const v = digitals[n.outBit];
      if (v === 0 || v === 1) val.set(n.id, v);
    }
    // Conectores: a rede nomeada que o desenho usa no lugar de uma linha. O
    // valor vem da equacao que o servidor extraiu, entao as duas pontas ficam
    // iguais mesmo quando o acionamento esta em outra pagina. Depois do
    // `outBit` de proposito -- era a terceira varredura do codigo antigo, e
    // sobrescrevia.
    if (n.connector && netValue && n.connector in netValue) {
      val.set(n.id, netValue[n.connector]);
    }
  }

  for (let iter = 0; iter < 10; iter++) {
    let changed = false;
    for (const n of model.nodes) {
      if (n.isSymbol) {
        // SYMBOL pode tb ser sink: pega valor da conexao de entrada.
        if (!val.has(n.id) && n.incoming && n.incoming.length) {
          const src = n.incoming[0];
          if (val.has(src.src)) {
            let v = val.get(src.src);
            if (src.srcMod === 'NOT') v = 1 - v;
            if (src.mod === 'NOT') v = 1 - v;
            val.set(n.id, v);
            changed = true;
          }
        }
        continue;
      }
      if (val.has(n.id)) continue;            // ja' calculado
      const ins = n.incoming;
      if (!ins || !ins.length) continue;
      const vals = [];
      let allKnown = true;
      for (const inp of ins) {                // ja' ordenado por porta
        if (!val.has(inp.src)) { allKnown = false; break; }
        let v = val.get(inp.src);
        if (inp.srcMod === 'NOT') v = 1 - v;
        if (inp.mod === 'NOT') v = 1 - v;
        // RTRIG/FTRIG: nao conseguimos avaliar sem historico; trata como passagem
        vals.push(v);
      }
      if (!allKnown) continue;
      let out;
      // Comparadores aritmeticos: AMVs chegam como float (ex.: 60.0001) e
      // constantes do GLE costumam ser inteiras. Trunca os dois lados antes de
      // comparar, conforme convencao SEL.
      const ta = Math.trunc(vals[0]);
      const tb = Math.trunc(vals[1]);
      switch (n.type) {
        case 'AND':  out = vals.every(v => v === 1) ? 1 : 0; break;
        case 'OR':   out = vals.some(v => v === 1) ? 1 : 0; break;
        case 'NOT':  out = vals[0] === 1 ? 0 : 1; break;
        case 'EQ':   out = ta === tb ? 1 : 0; break;
        case 'NE':   out = ta !== tb ? 1 : 0; break;
        case 'LT':   out = ta <  tb ? 1 : 0; break;
        case 'LE':   out = ta <= tb ? 1 : 0; break;
        case 'GT':   out = ta >  tb ? 1 : 0; break;
        case 'GE':   out = ta >= tb ? 1 : 0; break;
        case 'MULT': out = vals.every(v => v === 1) ? 1 : 0; break;
        case 'RTRIG': out = vals[0]; break;   // sem historico, propaga
        case 'PLT':  // latch S/R: sem estado persistente, melhor estimativa
          out = (vals[0] === 1) ? 1 : (vals[1] === 1) ? 0 : null;
          if (out === null) continue;
          break;
        case 'PCNDTIMER': out = vals[0]; break; // ignora pickup/dropout, propaga in
        case 'AST': out = vals[0]; break;
        default: continue;
      }
      val.set(n.id, out);
      changed = true;
    }
    if (!changed) break;
  }
  return val;
}

// =============================================================================
// Cabecalho (duas faixas): identidade na linha 1, estado na pastilha ao lado.
// =============================================================================

// Nome do IED e arquivo GLE sao dois elementos, e nao um titulo concatenado:
// assim cada um corta com reticencias sem levar o outro junto, e o nome do
// rele -- que e' o que identifica a tela -- nunca some primeiro.
function setHeaderTitle(relay, gle) {
  const h1 = document.getElementById('relay-title');
  const g  = document.getElementById('gle-name');
  if (h1) { h1.textContent = relay || 'Graphical Logic Viewer'; h1.title = relay || ''; }
  if (g)  { g.textContent = gle || ''; g.title = gle || ''; g.hidden = !gle; }
}

// A pastilha de estado corta com reticencias, entao o texto inteiro vai
// tambem no `title`: uma mensagem de erro do rele nao cabe na faixa.
function setStatus(cls, text) {
  const el = document.getElementById('status');
  if (!el) return;
  el.className = cls;
  el.textContent = text;
  el.title = text;
}

const MAP_SOURCE_LABELS = {
  'scd': 'SCD do projeto',
  'tabela': 'tabela de fábrica',
  'scd+tabela': 'SCD do projeto + tabela de fábrica',
};

// A cobertura e' da PAGINA aberta, nunca do diagrama inteiro -- roda perto de
// 100% numa pagina de GOOSE e perto de 50% numa de CS89/LED, e um numero so'
// pro diagrama inteiro pareceria tranquilizador bem onde importa menos.
// `coverage: null` (telnet) nao vira badge nenhum, nem "0/0".
function renderPollStatus(values) {
  const mms = activeScanMode === 'mms';
  pollCtl.hidden = !mms;
  if (!mms) { pollStatus.textContent = ''; return; }
  const parts = [];
  if (values && values.coverage) {
    parts.push(`${values.coverage.mapped}/${values.coverage.total} bits nesta página`);
    // De onde veio o mapa. A decisão de cabeceira desta branch é "o SCD do
    // projeto primeiro, porque ele é a verdade como construída"; sem dizer
    // qual das duas fontes respondeu, uma queda pro mapa de fábrica só
    // aparecia no log do servidor.
    const src = MAP_SOURCE_LABELS[values.coverage.source];
    if (src) parts.push(src);
  }
  if (periodNotice) parts.push(periodNotice);
  pollStatus.textContent = parts.length ? 'MMS · ' + parts.join(' · ') : '';
}

pollMs.addEventListener('change', async () => {
  const requested = parseInt(pollMs.value, 10);
  if (!Number.isFinite(requested)) return;
  let d;
  try {
    const r = await fetch(withD('/period'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({interval_ms: requested}),
    });
    d = await r.json();
  } catch (e) {
    periodNotice = 'falha ao aplicar: ' + e;
    renderPollStatus(lastValues);
    return;
  }
  // O servidor devolve o que REALMENTE aplicou: sem leitura ativa pra
  // aplicar agora, ou um pedido recusado (telnet), nao acelera nada -- e a
  // tela nao pode mentir mostrando o numero pedido. A comparacao e' com
  // `null`, e nao "se for verdadeiro": 0 e' um periodo valido ("o mais
  // rapido que der"), e `if (d.interval_ms)` deixaria no campo o que o
  // usuario digitou -- inclusive um negativo que o servidor cortou em 0.
  if (d.interval_ms != null) pollMs.value = d.interval_ms;
  // A tela acompanha na hora, sem esperar a proxima volta de `refreshTabs`.
  setScreenPace(d.interval_ms);
  periodNotice = d.status === 'aplicado' ? null
    : (d.status || 'recusado') + (d.reason ? ': ' + d.reason : '');
  renderPollStatus(lastValues);
});

// =============================================================================
// A PINTURA, e quando ela NAO acontece.
//
// `applyValues` nao desenha mais: ela guarda a leitura e agenda um quadro. Duas
// coisas saem disso.
//
// (a) Duas leituras que chegam dentro do mesmo quadro do monitor viram UMA
//     pintura -- o navegador nao mostraria a primeira de qualquer jeito. Com o
//     `/events` empurrando no ritmo do rele (o MMS le a cada 10 ms), isso e' o
//     que impede a tela de tentar pintar 100 vezes por segundo.
// (b) A pintura fica presa ao relogio do monitor, nao a um `setTimeout`. E' o
//     limite real de "o mais rapido possivel": abaixo de um quadro nao existe
//     tela mais rapida, so' CPU gasta em quadros que ninguem ve.
//
// E o desenho so' e' refeito quando MUDOU. `rev` vem do servidor e cobre
// exatamente pagina + digitais + analogicos -- nao o payload inteiro, porque
// `ts` e `age` mudam a cada leitura por construcao. A pastilha de estado fica
// FORA da comparacao: e' ela que mostra a idade, que precisa envelhecer na
// tela mesmo com o rele parado.
//
// Medido no Chrome, na pagina mais pesada do corpus (118 elementos, 83
// conexoes, 588 nos SVG), com recalc e layout dentro da conta.
// =============================================================================
let _pendingValues = null;
let _drawRaf = null;
let _lastKnownCount = 0;

function applyValues(values) {
  if (!values) return;
  _pendingValues = values;
  if (_drawRaf === null) _drawRaf = requestAnimationFrame(_flushDraw);
}

function _flushDraw() {
  _drawRaf = null;
  const values = _pendingValues;
  _pendingValues = null;
  if (values) drawValues(values);
}

//: Forca a proxima pintura mesmo que `rev` nao tenha mudado. Uma pagina nova
//: (ou uma que voltou do cache) tem o SVG inteiro por pintar.
function invalidateDrawing() {
  drawnRev = null;
  drawnPage = null;
}

function drawValues(values) {
  const svg = document.querySelector('#viewer svg');
  if (!svg) return;
  // Um SVG diferente e' uma pagina diferente: recompila e pinta tudo.
  if (!pageModel || pageModel.svg !== svg) {
    pageModel = compilePage(svg);
    invalidateDrawing();
  }

  if (values.rev == null || values.rev !== drawnRev || values.page !== drawnPage) {
    _repaint(values, pageModel);
    drawnRev = values.rev;
    drawnPage = values.page;
  }
  _renderStatusBadge(values);
}

function _repaint(values, model) {
  // Normaliza: digitais com valor null = indeterminado. Chaves em UPPER, que
  // e' como o modelo compilado guarda os nomes.
  const digitalsU = {};
  for (const [k, v] of Object.entries(values.digitals || {})) {
    digitalsU[k.toUpperCase()] = v;
  }
  // Analogs pode vir como {NAME:{value,group}} (modo agrupado) ou
  // {NAME: numero} (modo legado). Achata pra {NAME_UPPER: numero}.
  const analogsU = {};
  for (const [k, e] of Object.entries(values.analogs || {})) {
    let v = e;
    if (e !== null && e !== undefined && typeof e === 'object') v = e.value;
    if (typeof v === 'number' && !isNaN(v)) analogsU[k.toUpperCase()] = v;
  }

  // Valor de cada rede de conector, avaliado UMA vez: alimenta tanto a
  // coloracao quanto a secao Conectores da legenda.
  const netValue = {};
  for (const [label, net] of Object.entries(values.connectors || {})) {
    const v = evalConnectorTree(net.tree, digitalsU);
    if (v === 0 || v === 1) netValue[label] = v;
  }

  const val = evaluateModel(model, digitalsU, analogsU, netValue);
  renderConnectorsPanel(values.connectors || {}, netValue, digitalsU,
                        values.page || '');

  // Blocos. Constantes e analogicos tem estilo proprio e nao recebem
  // bit-0/bit-1/bit-unknown; a classe vazia e' o que os deixa neutros.
  for (const n of model.nodes) {
    if (!n.rect) continue;
    let cls = '';
    if (!n.hasConst && !n.analogRaw) {
      const v = val.get(n.id);
      cls = (val.has(n.id) && v !== null && v !== undefined)
        ? (v === 1 ? 'bit-1' : 'bit-0')
        : 'bit-unknown';
    }
    if (cls !== n.cls) {
      if (n.cls) n.rect.classList.remove(n.cls);
      if (cls) n.rect.classList.add(cls);
      n.cls = cls;
    }
  }

  // Fios, em 3 estados. O operador na porta de ORIGEM (NOT) inverte o valor
  // que o fio carrega.
  for (const e of model.conns) {
    let v = val.get(e.src);
    if (e.srcMod === 'NOT' && (v === 0 || v === 1)) v = 1 - v;
    const cls = v === 1 ? 'active' : v === 0 ? 'off' : 'unknown';
    if (cls !== e.cls) {
      if (e.cls) e.c.classList.remove(e.cls);
      e.c.classList.add(cls);
      e.cls = cls;
    }
  }

  // Dots de juncao: verde se alguma linha que passa por ele esta ativa.
  if (model.dots.length) {
    const activeXY = new Set();
    for (const e of model.conns) {
      if (e.cls === 'active') for (const p of e.pts) activeXY.add(p);
    }
    for (const d of model.dots) {
      const on = activeXY.has(d.key);
      if (on !== d.on) { d.d.classList.toggle('active', on); d.on = on; }
    }
  }

  // Paineis: distingue conhecidos/desconhecidos.
  const known = Object.entries(values.digitals || {}).filter(([k, v]) => v !== null);
  const unknown = Object.entries(values.digitals || {}).filter(([k, v]) => v === null);
  const active = known.filter(([k, v]) => v === 1).map(([k]) => k).sort();
  const inactive_count = known.filter(([k, v]) => v === 0).length;
  _lastKnownCount = known.length;

  document.getElementById('bit-counts').innerHTML =
    `<div>Total da página: <b>${values.page_bits_total || Object.keys(values.digitals || {}).length}</b></div>` +
    `<div>Conhecidos: <b>${known.length}</b> (ativos: ${active.length}, inativos: ${inactive_count})</div>` +
    `<div>Indeterminados: <b>${unknown.length}</b></div>`;

  document.getElementById('active-bits').innerHTML =
    active.length ? active.map(b => `<div class="v-active">${b}</div>`).join('') :
                    '<div class="v-inactive">(nenhum)</div>';

  const unknownNames = unknown.map(([k]) => k).sort().slice(0, 30);
  document.getElementById('unknown-bits').innerHTML =
    unknownNames.length ? unknownNames.map(b => `<div style="color:#daa">${b}</div>`).join('') +
                          (unknown.length > 30 ? `<div style="color:#888">+${unknown.length-30} mais...</div>` : '') :
                          '<div class="v-inactive">(nenhum)</div>';

  _renderAnalogs(values, model);
}

function _renderAnalogs(values, model) {
  const analogEntries = Object.entries(values.analogs || {});
  const analogGroupsMeta = values.analog_groups || {};
  const fmtAnalog = (v) => {
    if (v === null || v === undefined) return 'N/A';
    if (typeof v === 'number') {
      const abs = Math.abs(v);
      if (abs >= 1000 || abs < 0.01) return v.toExponential(2);
      return v.toFixed(2);
    }
    return String(v);
  };

  // 1) Valores inline nos SYMBOLs analogicos. A lista vem do modelo compilado;
  // era mais uma varredura `[data-analog]` do SVG inteiro por leitura.
  for (const n of model.analogNodes) {
    const entry = values.analogs ? values.analogs[n.analog] : null;
    let val = null;
    let hasValue = false;
    if (entry !== undefined && entry !== null && typeof entry === 'object') {
      val = entry.value;
      hasValue = val !== null && val !== undefined;
    } else if (entry !== undefined && entry !== null) {
      val = entry;
      hasValue = true;
    }
    if (n.analogText) {
      const txt = hasValue ? fmtAnalog(val) : 'N/A';
      if (n.analogText.textContent !== txt) n.analogText.textContent = txt;
      n.analogText.classList.toggle('live', hasValue);
    }
    if (n.analogRect) n.analogRect.classList.toggle('has-value', hasValue);
  }

  // 2) Painel agrupado por familia.
  const panelEl = document.getElementById('analog-values');
  if (analogEntries.length === 0) {
    panelEl.innerHTML = '-';
  } else if (analogEntries[0][1] !== null && typeof analogEntries[0][1] === 'object'
             && 'group' in (analogEntries[0][1] || {})) {
    const byGroup = {};
    analogEntries.forEach(([k, e]) => {
      const g = (e && e.group) || '_';
      (byGroup[g] = byGroup[g] || []).push([k, e.value]);
    });
    const order = Object.keys(analogGroupsMeta);
    Object.keys(byGroup).forEach(g => { if (!order.includes(g)) order.push(g); });
    panelEl.innerHTML = order
      .filter(g => byGroup[g] && byGroup[g].length)
      .map(g => {
        const lbl = analogGroupsMeta[g] || g;
        const rows = byGroup[g]
          .sort((a, b) => a[0].localeCompare(b[0]))
          .map(([k, v]) => {
            const hasV = v !== null && v !== undefined;
            return `<div class="${hasV ? 'an-row' : 'an-row na'}">`
              + `<span class="an-name">${k}</span> `
              + `<span class="an-val">${hasV ? fmtAnalog(v) : 'N/A'}</span>`
              + `</div>`;
          }).join('');
        return `<div class="an-group"><div class="an-group-hdr">${lbl}</div>${rows}</div>`;
      }).join('') || '-';
  } else {
    panelEl.innerHTML = analogEntries.slice(0, 20).map(([k, v]) =>
      `<div>${k} = ${fmtAnalog(v)}</div>`).join('') || '-';
  }
}

function _renderStatusBadge(values) {
  // A idade vem PRONTA do servidor (`LiveState.snapshot`), medida la' com o
  // relogio monotonico. Era `Date.now()/1000 - values.ts`: uma subtracao
  // entre DOIS relogios diferentes -- medido nesta maquina, o do WSL (onde
  // roda o servidor) estava 82,5 s atras do relogio do Windows (onde roda o
  // navegador), e a barra anunciava "valores antigos (82,5s atras)" com o
  // rele respondendo a cada 10 ms e o Wireshark limpo. `null` = ainda nao se
  // leu nada.
  //
  // Fica fora do teste de `rev` de proposito: a idade muda a cada leitura, e
  // e' justamente com o rele parado que alguem quer saber quao velha ela e'.
  const age = values.age;
  const known = (values.page_bits_known != null)
    ? values.page_bits_known : _lastKnownCount;
  if (values.error) {
    setStatus('status error', `ERRO: ${values.error}`);
  } else if (values.connected === false) {
    // Diagrama aberto e desconectado: nao ha leitura nenhuma, entao nao faz
    // sentido falar em "valores antigos" -- nem medir idade de um ts zerado.
    setStatus('status stale',
              `desconectado · ${values.page_bits_total || 0} bits indeterminados`);
  } else if (values.status === 'connecting') {
    setStatus('status stale', 'conectando ao relé...');
  } else if (age == null) {
    setStatus('status stale', 'ainda sem leitura');
  } else if (age > 5) {
    setStatus('status stale', `valores antigos (${age.toFixed(1)}s atras)`);
  } else {
    setStatus('status',
              `ao vivo · ${age.toFixed(1)}s · ${known}/${(values.page_bits_total||known)} bits conhecidos`);
  }
}

// =============================================================================
// O TRANSPORTE: o servidor avisa, a tela nao pergunta.
//
// Medido ponta a ponta antes disto: o rele responde em 1,5 ms, `/values` custa
// 0,03 ms no servidor, a ida e volta HTTP 1 ms e a repintura ~1,2 ms na pagina
// mais pesada do corpus. Soma ~3,7 ms -- dentro de 100 a 500 ms de ESPERA pela
// proxima volta do relogio da tela. A espera era a latencia inteira, e nada
// que se faca no desenho encosta nela. Pior: com o MMS lendo a cada 10 ms, 49
// de cada 50 leituras morriam sem nunca chegar ao desenho.
//
// `/events` e' um SSE: uma conexao por aba aberta, um quadro quando o rele diz
// alguma coisa (`LiveState.wait_for_change`) e um heartbeat de 1 s para a
// idade da pastilha continuar envelhecendo. A latencia passa a ser a ida e
// volta, nao a espera.
//
// O polling continua aqui inteiro, como RESERVA: um navegador sem EventSource,
// ou um stream que fechou de vez, nao pode deixar a tela parada.
// =============================================================================
let evtSource = null;
let streamKey = '';          // "diagrama|pagina" que o stream atual segue
let streamGaveUp = false;    // um fechamento definitivo devolve a tela ao poll

function stopStream() {
  if (evtSource) { evtSource.close(); evtSource = null; }
  streamKey = '';
}

function startStream() {
  if (!window.EventSource || streamGaveUp) return false;
  const key = `${activeDiagram}|${currentPage}`;
  if (evtSource && streamKey === key) return true;   // ja' e' esse
  stopStream();
  streamKey = key;
  // `MOUNT_URL` a mao: o shim de prefixo do `mount.py` remenda `fetch` e
  // `XMLHttpRequest`, e `EventSource` nao e' nenhum dos dois -- sem isto a
  // conexao vai para `/events` na raiz, onde o dispatcher responde 404 e a
  // tela cai no polling sem ninguem entender por que.
  const raw = withD('/events?page=' + encodeURIComponent(currentPage));
  const src = new EventSource(window.MOUNT_URL ? window.MOUNT_URL(raw) : raw);
  evtSource = src;
  src.onmessage = (ev) => {
    if (evtSource !== src) return;                   // stream velho
    let v;
    try { v = JSON.parse(ev.data); } catch (e) { return; }
    lastValues = v;
    applyValues(v);
    renderPollStatus(v);
  };
  src.onerror = () => {
    // O EventSource reconecta sozinho quando o servidor encerra o stream no
    // seu tempo maximo -- isso e' normal e nao e' erro. So' desistimos quando
    // ele fecha de vez, e ai o polling assume para a tela nao congelar.
    if (src.readyState !== EventSource.CLOSED) return;
    if (evtSource === src) { evtSource = null; streamKey = ''; }
    streamGaveUp = true;
    console.warn('/events fechou; voltando ao polling');
    valuesTick();
  };
  return true;
}

async function pollValues() {
  try {
    // Filtra no backend pela pagina atual (economiza payload JSON)
    const r = await fetch(withD('/values?page=' + encodeURIComponent(currentPage)));
    const v = await r.json();
    lastValues = v;
    applyValues(v);
    renderPollStatus(v);
  } catch (e) {
    setStatus('status error', `falha de polling: ${e}`);
  }
}

// =============================================================================
// A cadencia da TELA -- que nao e' a cadencia da leitura, e ate' aqui nao
// tinha nada a ver com ela.
//
// Era `setInterval(pollValues, 500)`: 2 Hz fixos, viesse o valor de onde
// viesse. Medido ponta a ponta: o rele responde em 1,5 ms (no fio, do pcap
// de campo), `/values` custa 0,03 ms no servidor, a ida e volta HTTP 1 ms, e
// a repintura 0,80 ms no navegador -- recalc e layout ja' dentro da conta, na
// pagina mais pesada de um GLE real (440 elementos, 41 simbolos, 28 blocos,
// 63 conexoes). Soma ~3 ms, contra 0 a 500 ms de espera pela proxima volta do
// relogio. A ESPERA era o tempo todo; e com o MMS a 10 ms, 49 de cada 50
// leituras morriam sem nunca chegar ao desenho.
//
// Agora a tela segue o periodo de leitura do PROPRIO diagrama (`interval_ms`,
// que `tab()` e `meta()` ja mandavam pro controle de periodo), preso entre:
//   - piso de 100 ms, que e' orcamento de REPINTURA e nao limite de
//     protocolo: 0,8 ms a cada 100 ms e' ~1% de uma CPU. E' a razao de o piso
//     existir aqui depois de ter sido removido do polling MMS -- la o custo e'
//     do rele, que se defende sozinho respondendo no ritmo dele; aqui o custo
//     e' do navegador de quem esta comissionando, e ninguem ganha nada com
//     uma tela repintada mais vezes do que o olho ve.
//   - teto de 500 ms, o de sempre: um diagrama telnet lido a cada 0,5 s nao
//     fica mais atual sendo perguntado dez vezes por segundo -- e o teto nao
//     acompanha um periodo AINDA MAIOR porque a pastilha mostra a IDADE da
//     leitura, que precisa envelhecer na tela mesmo quando nenhum bit muda.
// =============================================================================
const SCREEN_MIN_MS = 100, SCREEN_MAX_MS = 500;
let screenMs = SCREEN_MAX_MS;
let valuesTimer = null;
let valuesInFlight = false;

function setScreenPace(readMs) {
  const ms = Number(readMs);
  screenMs = Number.isFinite(ms) && ms > 0
    ? Math.min(SCREEN_MAX_MS, Math.max(SCREEN_MIN_MS, ms))
    : SCREEN_MAX_MS;
}

// Duas guardas que o `setInterval` nao tinha, e que so' aparecem quando a
// cadencia sobe. (a) `valuesInFlight`: o `setInterval` disparava mesmo com a
// volta anterior em voo, e a 10 Hz isso empilha requisicoes em vez de
// adiantar a tela. (b) o proximo tique e' agendado ANTES do `await`: um fetch
// pendurado (servidor ocupado, rede da subestacao) nao pode parar o relogio
// da tela -- que e' justamente o que um `await` no comeco da volta faria.
async function valuesTick() {
  // Enquanto o `/events` esta de pe' ele e' quem alimenta a tela: um poll em
  // paralelo faria o mesmo trabalho duas vezes e desfaria o ganho. O tique
  // simplesmente nao se reagenda, e `startStream`/`onerror` sao os dois
  // pontos que o trazem de volta.
  if (evtSource) { valuesTimer = null; return; }
  valuesTimer = setTimeout(valuesTick, screenMs);
  if (valuesInFlight) return;
  valuesInFlight = true;
  try {
    await pollValues();
  } finally {
    valuesInFlight = false;
  }
}

// =============================================================================
// "Fora do modelo": as variaveis do desenho INTEIRO que a conexao escolhida
// nao alcanca -- as que precisam entrar no modelo do servidor do IED (MMS) ou
// que simplesmente nao existem nesta Relay Word (telnet). Ate aqui elas eram
// indistinguiveis, na tela, de um bit que ainda nao chegou: as duas coisas
// pintam de indeterminado, e so' uma delas espera resolver sozinha.
//
// Fica FORA do /values de proposito. A lista e' do diagrama inteiro (e' pra
// montar o modelo do IED; pagina por pagina o usuario teria que percorrer o
// desenho todo pra juntar os nomes) e so' muda quando a conexao muda -- entao
// e' buscada na troca de aba, quando o estado da conexao vira e quando o
// usuario abre o painel, e nao duas vezes por segundo junto do polling.
// =============================================================================
const unreachCtl = (function () {
  const details = document.getElementById('unreachable');
  const countEl = document.getElementById('unreachable-count');
  const hintEl = document.getElementById('unreachable-hint');
  const listEl = document.getElementById('unreachable-list');
  const copyBtn = document.getElementById('unreachable-copy');
  const txtLink = document.getElementById('unreachable-txt');
  if (!details) return { refresh() {}, reset() {}, onTabs() {} };

  // Um `<a download>` e' navegacao direta do navegador, e o shim de fetch nao
  // alcanca isso -- o prefixo de montagem tem que entrar na mao. Ele sai da
  // propria URL da pagina, que e' `<prefixo>/` ou `<prefixo>/index.html`.
  const MOUNT = location.pathname.replace(/\/(index\.html)?$/, '');

  const HINTS = {
    mms: 'Não estão no modelo do servidor deste IED — nem no SCD do projeto, '
       + 'nem na tabela de fábrica. Adicione os pontos ao modelo do IED e '
       + 'reconecte.',
    relay_word: 'Não existem na Relay Word deste relé (FID atual). Bits VB '
       + '(GOOSE) ficam em outra região e só são legíveis por MMS.',
    dna: 'Não estão no DNA configurado neste relé, que é o subconjunto de '
       + 'digitais que o Fast Meter entrega.',
  };

  let names = [];
  let lastKey = null;      // aba + estado da conexao da ultima busca

  function esc(t) {
    return String(t).replace(/[&<>"]/g, c => (
      {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  }

  function enable(on) {
    copyBtn.setAttribute('aria-disabled', on ? 'false' : 'true');
    txtLink.setAttribute('aria-disabled', on ? 'false' : 'true');
  }

  function paint(data) {
    names = data.names || [];
    txtLink.href = MOUNT + '/unreachable.txt'
                 + (activeDiagram ? '?d=' + encodeURIComponent(activeDiagram) : '');
    if (!data.available) {
      // Desconectado, sem mapa ou sem DNA: um "0" aqui leria como "está tudo
      // no relé" numa tela em que ninguém leu nada.
      countEl.textContent = '—';
      countEl.dataset.state = 'unknown';
      hintEl.textContent = 'Conecte o diagrama para saber o que esta leitura alcança.';
      listEl.innerHTML = '';
      enable(false);
      return;
    }
    countEl.textContent = names.length + '/' + data.total;
    countEl.dataset.state = names.length ? 'some' : 'none';
    hintEl.textContent = names.length
      ? (HINTS[data.reason] || '')
      : ('Todas as ' + data.total + ' variáveis do desenho são legíveis por '
         + 'esta conexão.');
    listEl.innerHTML = names.map(n => '<div>' + esc(n) + '</div>').join('');
    enable(names.length > 0);
  }

  async function refresh() {
    try {
      const r = await fetch(withD('/unreachable'), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      paint(await r.json());
    } catch (e) {
      console.warn('falha ao buscar as variáveis fora do modelo:', e);
    }
  }

  copyBtn.addEventListener('click', async () => {
    if (!names.length) return;
    const text = names.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = 'Copiado!';
    } catch (e) {
      // Sem permissão de área de transferência (http em rede da subestação,
      // por exemplo): o .txt ao lado continua resolvendo.
      copyBtn.textContent = 'Falhou — use o .txt';
    }
    setTimeout(() => { copyBtn.textContent = 'Copiar'; }, 2000);
  });

  details.addEventListener('toggle', () => { if (details.open) refresh(); });

  function keyNow() {
    const t = tabFromId(activeDiagram);
    return activeDiagram + ':' + (t ? t.status + ':' + t.connected : '-');
  }

  return {
    // Troca de aba: busca agora, sem esperar a proxima volta de
    // `refreshTabs`. Nao fecha o painel -- quem o abriu quer continuar vendo
    // a lista, agora a do outro diagrama.
    reset() { lastKey = keyNow(); refresh(); },
    refresh,
    // Chamado a cada volta de `refreshTabs`: a lista so' muda quando a
    // conexao muda, entao e' isso que dispara a busca -- inclusive a que
    // termina sozinha, minutos depois, quando a descoberta de bits acaba.
    onTabs() {
      const key = keyNow();
      if (key === lastKey) return;
      lastKey = key;
      refresh();
    },
  };
})();

// =============================================================================
// Faixa de abas: um diagrama por aba, com o estado da conexao na propria aba.
// Trocar de aba NAO recarrega a pagina -- busca /meta?d= e re-renderiza a
// faixa de paginas e o viewer.
// =============================================================================
function tabFromId(id) { return TABS.find(t => t.id === id) || null; }

function renderTabs() {
  const bar = document.getElementById('tabs');
  bar.innerHTML = '';
  TABS.forEach(t => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'tab' + (t.id === activeDiagram ? ' active' : '');
    el.setAttribute('data-status', t.status);
    el.setAttribute('data-id', t.id);
    el.title = t.error || `${t.relay} · ${t.gle} · ${t.ip || 'sem IP'}`;
    const dot = document.createElement('span');
    dot.className = 'dot';
    el.appendChild(dot);
    const name = document.createElement('span');
    name.className = 'label';
    name.textContent = t.relay + ' · ' + t.gle;
    el.appendChild(name);
    const ip = document.createElement('span');
    ip.className = 'ip';
    ip.textContent = t.ip || 'sem IP';
    el.appendChild(ip);
    if (t.refs > 1) {
      // Dois diagramas no mesmo rele dividem UMA sessao telnet.
      const sh = document.createElement('span');
      sh.className = 'shared';
      sh.textContent = '↔' + t.refs;
      sh.title = t.refs + ' diagramas dividem esta conexão';
      el.appendChild(sh);
    }
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'close';
    x.textContent = '×';
    x.title = 'Fechar este diagrama (solta a conexão)';
    x.addEventListener('click', (e) => { e.stopPropagation(); closeDiagram(t.id); });
    el.appendChild(x);
    el.addEventListener('click', () => {
      if (t.id !== activeDiagram) switchDiagram(t.id);
    });
    bar.appendChild(el);
  });
  const add = document.createElement('a');
  add.className = 'tab-new';
  add.href = './novo';
  add.textContent = '+ Diagrama';
  add.title = 'Abrir outro GLE (mesmo relé ou outro)';
  bar.appendChild(add);
  updateConnButton();
  // A tela acompanha o periodo de leitura da aba ativa (ver setScreenPace).
  const ativa = tabFromId(activeDiagram);
  setScreenPace(ativa ? ativa.interval_ms : null);
}

function updateConnButton() {
  const btn = document.getElementById('conn-toggle');
  const t = tabFromId(activeDiagram);
  if (!btn) return;
  if (!t) { btn.disabled = true; btn.textContent = 'Conectar'; return; }
  btn.setAttribute('data-state', t.status);
  if (BOOT.no_relay) {
    btn.disabled = true;
    btn.textContent = 'Conectar';
    btn.title = 'Modo visualização (--no-relay): conexão desabilitada';
    return;
  }
  if (t.status === 'connecting') {
    // Cancelavel de proposito: um IP errado custaria a espera inteira do
    // watchdog, e o servidor ja sabe abandonar a tentativa em voo.
    btn.disabled = false; btn.textContent = 'Cancelar';
    btn.title = 'Cancelar a tentativa de conexão';
  } else if (t.connected) {
    btn.disabled = false; btn.textContent = 'Desconectar';
    btn.title = 'Parar de ler este relé (o diagrama continua aberto)';
  } else {
    btn.disabled = false; btn.textContent = 'Conectar';
    btn.title = t.error || ('Conectar em ' + (t.ip || 'sem IP'));
  }
  // A faixa de abas se atualiza sozinha a cada 2 s, entao esta e' a chamada
  // que descobre uma conexao aberta noutro lugar -- ou uma que caiu.
  if (vbSourceCtl) vbSourceCtl.onConnection(t.connected);
  setHeaderTitle(t.relay, t.gle);
}

async function refreshTabs() {
  try {
    const r = await fetch('/diagrams', { cache: 'no-store' });
    const d = await r.json();
    TABS = d.diagrams || [];
    if (!TABS.some(t => t.id === activeDiagram)) {
      activeDiagram = d.active || (TABS[0] && TABS[0].id) || null;
    }
    renderTabs();
    unreachCtl.onTabs();
  } catch (e) {
    console.warn('falha ao atualizar abas:', e);
  }
}

function renderPageStrip() {
  const nav = document.getElementById('pages');
  nav.innerHTML = '';
  PAGES.forEach(([nome, safe]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('data-page', safe);
    b.textContent = nome;
    b.addEventListener('click', () => loadPage(safe, nome));
    nav.appendChild(b);
  });
}

function applyMeta(meta) {
  PAGES = meta.pages || [];
  VAR_INDEX = meta.var_index || {};
  renderPageStrip();
  setHeaderTitle(meta.relay, meta.gle);
  document.title = meta.relay + ' · ' + meta.gle + ' — GLV';
  lastValues = { digitals: {}, analogs: {}, ts: 0, error: '' };
  // scan_mode vem de tab(), embutido em meta() -- e' o que decide se o
  // controle de período existe nesta aba. Cada troca de diagrama zera o
  // aviso do período anterior: e' de outro diagrama, nao deste.
  activeScanMode = meta.scan_mode || 'telnet';
  periodNotice = null;
  // O periodo real desta aba, nao o que sobrou da aba anterior -- sem isto,
  // trocar de um diagrama MMS a 500ms pra outro no default de 100ms deixava
  // o campo mostrando 500 pro segundo, mentindo sobre o que esta em vigor.
  if (meta.interval_ms != null) pollMs.value = meta.interval_ms;
  setScreenPace(meta.interval_ms);
  renderPollStatus(lastValues);
  // Aba nova, lista nova: o que sobrou na tela e' de outro rele.
  unreachCtl.reset();
  if (vbSourceCtl) vbSourceCtl.reset();
  return loadPage(meta.initial, '');
}

async function switchDiagram(id) {
  activeDiagram = id;
  renderTabs();
  try {
    await fetch(withD('/diagrams/activate'), { method: 'POST' });
    const r = await fetch(withD('/meta'), { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const meta = await r.json();
    await applyMeta(meta);
    await loadGroupState();
    await loadHighlights();
    if (notesCtl) notesCtl.reload(currentPage);
    pollValues();
  } catch (e) {
    console.warn('falha ao trocar de diagrama:', e);
  }
}

async function closeDiagram(id) {
  const t = tabFromId(id);
  const label = t ? (t.relay + ' · ' + t.gle) : id;
  if (!confirm('Fechar ' + label + '? A conexão dele é liberada.')) return;
  try {
    const r = await fetch('/diagrams/close?d=' + encodeURIComponent(id),
                          { method: 'POST' });
    if (id === activeDiagram) SelProgress.hide();
    const d = await r.json();
    TABS = d.diagrams || [];
    if (!TABS.length) { window.location.href = './novo'; return; }
    if (id === activeDiagram) {
      await switchDiagram(d.active || TABS[TABS.length - 1].id);
    } else {
      renderTabs();
    }
  } catch (e) {
    alert('Falha ao fechar o diagrama: ' + e);
  }
}

// =============================================================================
// Fonte dos VB -- de onde vem cada Virtual Bit, segundo o SCD do projeto.
//
// Um bloco do GLE diz `VB023` e mais nada: nem quem publica, nem por qual
// GOOSE, nem como o bit se chama do outro lado. As tres coisas estao no SCD,
// e `web/glv/vb_source.py` faz a juncao (o `<ExtRef>` do assinante nomeia um
// ENDERECO dentro do publicador; o `sAddr` do publicador e' que diz que
// aquele endereco e' `PSV05`).
//
// Conectado ou nao, a camada vale; o que a conexao tira e' a TINTA. Com o rele
// sendo lido a cor do bloco e' o estado do bit, e pintar procedencia por cima
// seriam duas linguagens no mesmo desenho, com a mais importante das duas
// perdendo. Ao vivo sobram a assinatura e o cartao -- que e' justamente o que
// se quer com o rele na frente -- e o bloco continua dizendo o estado.
//
// Tres coisas de uma vez, que e' como foram escolhidas depois de desenhadas:
//   . a assinatura do SCD toma o lugar do comentario de porta que o GLE ja'
//     desenha ao lado do VB (o espaco ja' existe e ja' esta alinhado);
//   . a tinta de auditoria classifica o bloco -- assinado, qualidade GOOSE,
//     placeholder -- que e' a unica leitura que responde "o que o integrador
//     esqueceu" (so' desconectado, pelo motivo acima);
//   . o cartao no hover traz o ExtRef inteiro, para quem esta comissionando
//     aquele ponto.
// =============================================================================
vbSourceCtl = (function setupVbSource() {
  const group = document.getElementById('vbsrc-group');
  const btn = document.getElementById('vbsrc-toggle');
  const viewer = document.getElementById('viewer');
  const section = document.getElementById('vbsrc-section');
  const legend = document.getElementById('vbsrc-legend');
  if (!group || !btn || !viewer) return null;

  const KINDS = {
    signal:      {stroke: '#1f7a45', fill: '#e6f3ea', width: '1.8'},
    quality:     {stroke: '#2b4a6f', fill: '#e2eaf3', width: '1.8'},
    placeholder: {stroke: '#b8b5ad', fill: '#f2f0ea', width: '0.9',
                  dash: '2,1.6'},
  };
  const KIND_LABEL = {
    signal: 'com assinatura', quality: 'qualidade GOOSE',
    placeholder: 'placeholder',
  };

  // Uma resposta por diagrama: `/vb-source` manda o mapa inteiro (256 no
  // maximo) e o servidor le o SCD uma vez so'. Trocar de aba e voltar nao
  // repete nem a viagem nem o parse.
  const cache = {};
  let data = null;      // payload do diagrama ativo, ou null
  let on = false;
  let connected = false;
  let card = null;
  // O que esta camada mexeu no SVG desta pagina, para desfazer exatamente
  // isso. O no da pagina e' cacheado e reaproveitado (ver `pageSvgCache`),
  // entao deixar rastro aqui e' deixa-lo para sempre.
  let touched = [];

  function svgNode() { return viewer.querySelector('svg'); }

  function normalise(name) {
    const m = /^VB0*(\d+)$/i.exec((name || '').trim());
    if (!m) return '';
    const n = String(m[1]);
    return 'VB' + (n.length >= 3 ? n : ('00' + n).slice(-3));
  }

  // O nome do publicador vai INTEIRO. Ele ja' foi encurtado aqui, tirando um
  // `QPC<n>_` inicial porque "o prefixo do painel se repete em todos" -- e nao
  // se repete: uma baia assina ATRAVESSANDO paineis. No SCD de referencia todo
  // assinante puxa de tres ou quatro paineis, e em cada um deles ha' ao menos
  // um par que desaba no mesmo rotulo sem o prefixo (QPC1_TR2_UPC2 e
  // QPC2_TR2_UPC2 viravam os dois `TR2_UPC2`, na mesma pagina). Num mapa de
  // assinatura GOOSE a pergunta E' de qual rele veio o bit, e meia resposta
  // ambigua e' pior que uma linha mais larga. Quem precisar do detalhe tem o
  // cartao, que sempre mostrou o nome cheio.
  function signature(r) {
    if (r.kind === 'placeholder') return '';
    if (r.kind === 'quality') return r.ied + ' · ' + r.cb;
    return r.ied + ' · ' + r.ln;
  }

  function bitLabel(r) {
    if (r.kind === 'placeholder') return '';
    return r.kind === 'quality' ? 'saúde GOOSE' : (r.bit || '(sem sAddr)');
  }

  function vbGroups(svg) {
    return Array.from(svg.querySelectorAll('g.symbol-grp[data-bit]'))
      .filter(g => normalise(g.getAttribute('data-bit')));
  }

  function recordOf(g) {
    if (!data || !data.sources) return null;
    return data.sources[normalise(g.getAttribute('data-bit'))] || null;
  }

  // -- pintar / despintar ---------------------------------------------------

  function paint(el, prop, value) {
    // `!important` na mao: as regras de estado (`.element-symbol.bit-unknown`)
    // ja' sao `!important`, e um `style` sem ele perde para elas. Foi assim
    // que a primeira versao saiu inteiramente branca.
    el.style.setProperty(prop, value, 'important');
  }

  function apply() {
    const svg = svgNode();
    if (!svg || !on || !data) return;
    clearSvg();
    const layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('class', 'vbsrc-layer');
    for (const g of vbGroups(svg)) {
      const r = recordOf(g);
      if (!r) continue;
      const c = KINDS[r.kind] || KINDS.placeholder;
      g.style.setProperty('cursor', 'help');
      touched.push(g);
      // Conectado, o preenchimento do bloco pertence ao estado do bit: a
      // camada nao encosta nele. O texto e o cartao saem nos dois casos.
      const rect = connected ? null : g.querySelector('rect');
      if (rect) {
        paint(rect, 'fill', c.fill);
        paint(rect, 'stroke', c.stroke);
        paint(rect, 'stroke-width', c.width);
        if (c.dash) paint(rect, 'stroke-dasharray', c.dash);
        touched.push(rect);
      }
      // O comentario de porta e' o unico texto ja' reservado e ja' alinhado
      // ao lado do bloco. Um placeholder mantem o do GLE, apagado: nao ha
      // assinatura para por no lugar dele.
      const pc = g.querySelector('text.port-comment');
      if (!pc) continue;
      if (r.kind === 'placeholder') {
        pc.style.setProperty('opacity', '.35');
        touched.push(pc);
        continue;
      }
      pc.style.setProperty('display', 'none');
      touched.push(pc);
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', pc.getAttribute('x'));
      t.setAttribute('y', pc.getAttribute('y'));
      t.setAttribute('font-family', 'monospace');
      t.setAttribute('font-size', '5.4');
      t.setAttribute('font-weight', 'bold');
      t.setAttribute('fill', c.stroke);
      t.setAttribute('pointer-events', 'none');
      t.textContent = signature(r) + ' → ' + bitLabel(r);
      layer.appendChild(t);
    }
    svg.appendChild(layer);
  }

  function clearSvg() {
    const svg = svgNode();
    for (const el of touched) {
      el.style.removeProperty('fill');
      el.style.removeProperty('stroke');
      el.style.removeProperty('stroke-width');
      el.style.removeProperty('stroke-dasharray');
      el.style.removeProperty('display');
      el.style.removeProperty('opacity');
      el.style.removeProperty('cursor');
      // `removeProperty` esvazia a declaracao e deixa o `style=""` no
      // elemento. Sem efeito nenhum, mas o no da pagina fica no cache com o
      // atributo vazio, e "o desenho volta como estava" deixa de ser
      // literal. So' sai quando esta vazio: se outra coisa tiver posto uma
      // propriedade ali, o atributo nao esta vazio e nao e' nosso.
      if (!el.getAttribute('style')) el.removeAttribute('style');
    }
    touched = [];
    if (svg) svg.querySelectorAll('g.vbsrc-layer').forEach(n => n.remove());
    hideCard();
  }

  // -- cartao no hover ------------------------------------------------------

  function ensureCard() {
    if (!card) {
      card = document.createElement('div');
      card.id = 'vbsrc-card';
      card.hidden = true;
    }
    // Reancorar, e nao so' criar uma vez: `loadPage` troca o conteudo do
    // visualizador com `replaceChildren`, que leva o cartao junto. Guardar a
    // referencia bastava para o `if (card)` passar e devolver um no fora do
    // documento -- o hover parava de mostrar qualquer coisa, em silencio,
    // a partir da primeira troca de pagina.
    if (card.parentNode !== viewer) viewer.appendChild(card);
    return card;
  }

  function hideCard() { if (card) card.hidden = true; }

  function cardHtml(vb, r) {
    const row = (k, v) => '<div><span class="k">' + k + '</span>' + esc(v) + '</div>';
    if (r.kind === 'placeholder') {
      return '<div class="t">' + esc(vb) + '</div>'
           + row('ExtRef', 'sem publicador (placeholder)')
           + (r.desc ? row('Descrição', r.desc) : '');
    }
    let h = '<div class="t">' + esc(vb) + (r.desc ? ' — ' + esc(r.desc) : '') + '</div>';
    h += row('IED', r.ied);
    h += row('GoCB', (r.ld_cb ? r.ld_cb + '/' : '') + r.cb);
    if (r.kind === 'quality') {
      h += row('Assina', 'a saúde da mensagem GOOSE');
      h += '<div><span class="k">Relay Word</span>'
         + '<span class="bit">a qualidade não tem bit</span></div>';
      return h;
    }
    h += row('LD / LN', r.ld + ' / ' + r.ln);
    h += row('Dado', r.do + '.' + r.da);
    h += '<div><span class="k">Relay Word</span><span class="bit">'
       + esc(r.bit || 'o publicador não mapeia este ponto') + '</span></div>';
    return h;
  }

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Delegado no visualizador, e registrado UMA vez. Por bloco seria um par
  // de listeners por `apply()`, e `apply()` roda de novo a cada troca de
  // pagina e a cada religada do botao -- o no da pagina volta do cache com
  // os listeners da vez anterior ainda nele.
  function groupUnder(node) {
    return node && node.closest ? node.closest('g.symbol-grp[data-bit]') : null;
  }

  viewer.addEventListener('mouseover', (e) => {
    if (!on) return;
    const g = groupUnder(e.target);
    if (!g) return;
    const r = recordOf(g);
    if (!r) return;
    const c = ensureCard();
    c.className = r.kind === 'quality' ? 'quality' : '';
    c.innerHTML = cardHtml(g.getAttribute('data-bit'), r);
    c.hidden = false;
    const vb = viewer.getBoundingClientRect();
    const gb = g.getBoundingClientRect();
    // Dentro do visualizador, sempre: o cartao mede ate 320 px e o bloco
    // pode estar encostado na borda direita da folha.
    const left = Math.min(gb.right - vb.left + 12 + viewer.scrollLeft,
                          viewer.scrollLeft + vb.width - 332);
    c.style.left = Math.max(viewer.scrollLeft + 8, left) + 'px';
    c.style.top = (gb.top - vb.top + viewer.scrollTop - 6) + 'px';
  });

  viewer.addEventListener('mouseout', (e) => {
    // Andar entre dois filhos do MESMO bloco tambem dispara mouseout; so'
    // sair do bloco fecha o cartao.
    if (groupUnder(e.target) !== groupUnder(e.relatedTarget)) hideCard();
  });

  // -- painel ---------------------------------------------------------------

  function renderLegend() {
    if (!section || !legend) return;
    if (!on || !data) { section.hidden = true; return; }
    section.hidden = false;
    let h = '';
    if (data.scd) {
      h += '<div class="head">' + esc(data.scd)
         + (data.ied ? ' · IED <b>' + esc(data.ied) + '</b>'
                       + (data.matched_by ? ' (por ' + esc(data.matched_by) + ')' : '')
                     : '') + '</div>';
    }
    if (data.error) h += '<div class="err">' + esc(data.error) + '</div>';
    // A legenda EXPLICA a tinta, e ao vivo nao ha tinta. Repetir as contagens
    // aqui seria legendar um desenho que nao esta na tela: o que o visitante
    // ve nos blocos e' o estado dos bits.
    if (connected) {
      h += '<div class="note">Ao vivo, só a assinatura: a cor dos blocos é o '
         + 'estado dos bits.</div>';
      legend.innerHTML = h;
      return;
    }
    const census = data.census || {};
    const total = Object.values(census).reduce((a, b) => a + b, 0);
    // Do RELE inteiro, nao da pagina aberta: e' a pergunta que a tinta
    // responde ("o que o integrador esqueceu"), e num painel ao lado de uma
    // pagina de 18 VB a contagem de 256 precisa dizer de onde vem.
    if (total) h += '<div class="head">' + total + ' VB do IED:</div>';
    for (const kind of ['signal', 'quality', 'placeholder']) {
      if (!(kind in census)) continue;
      h += '<div class="row"><span class="sw ' + kind + '"></span>'
         + census[kind] + ' ' + KIND_LABEL[kind] + '</div>';
    }
    legend.innerHTML = h;
  }

  // -- ligar / desligar -----------------------------------------------------

  async function load() {
    if (cache[activeDiagram] !== undefined) return cache[activeDiagram];
    try {
      const r = await fetch(withD('/vb-source'), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      cache[activeDiagram] = await r.json();
    } catch (e) {
      cache[activeDiagram] = {sources: {}, census: {},
                              error: 'falha ao ler a fonte dos VB: ' + e};
    }
    return cache[activeDiagram];
  }

  function setPressed(v) {
    btn.setAttribute('aria-pressed', v ? 'true' : 'false');
    btn.classList.toggle('active', v);
  }

  async function turnOn() {
    const want = activeDiagram;
    // A primeira ligada de cada diagrama paga o parse do SCD -- medido em
    // 1536 ms no `substation_demo.scd` de 22 MB, que e' o tamanho real de
    // uma subestacao. Um segundo e meio de botao mudo depois do clique e' o
    // que faz alguem clicar de novo.
    const label = btn.querySelector('span');
    const was = label ? label.textContent : '';
    if (label && cache[want] === undefined) label.textContent = 'lendo SCD...';
    btn.disabled = true;
    let payload;
    try {
      payload = await load();
    } finally {
      btn.disabled = false;
      if (label) label.textContent = was;
    }
    // O visitante pode ter trocado de aba durante a viagem, e ai' esta
    // resposta e' de outro rele. Ter conectado no meio nao invalida nada:
    // `apply()` le `connected` na hora de desenhar.
    if (want !== activeDiagram) return;
    data = payload;
    on = true;
    setPressed(true);
    apply();
    renderLegend();
  }

  function turnOff() {
    on = false;
    setPressed(false);
    clearSvg();
    renderLegend();
  }

  btn.addEventListener('click', () => { on ? turnOff() : turnOn(); });

  return {
    // Pagina nova no mesmo diagrama: o SVG e' outro no e nao tem a camada.
    onPageChange() { if (on) apply(); },
    // Aba nova: outro rele, outro SCD, e a camada volta desligada. Sem isto
    // o mapa de um diagrama pintaria os VB de outro, que tem os mesmos nomes.
    reset() { data = null; if (on) turnOff(); },
    // Conectar nao desliga a camada, muda o que ela desenha. Repintar aqui
    // e' o que troca a tinta pela assinatura sozinha no instante em que o
    // rele entra, e o contrario quando ele sai. Chamada a cada volta da
    // faixa de abas (2 s), entao so' a MUDANCA de estado faz trabalho.
    onConnection(isConnected) {
      const was = connected;
      connected = !!isConnected;
      if (was === connected || !on) return;
      apply();
      renderLegend();
    },
  };
})();

(function setupConnToggle() {
  const btn = document.getElementById('conn-toggle');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const t = tabFromId(activeDiagram);
    if (!t) return;
    btn.disabled = true;
    try {
      if (t.connected || t.status === 'connecting') {
        await fetch(withD('/disconnect'), { method: 'POST' });
        // Quem cancelou foi o usuario: a barra sai na hora, sem esperar o
        // job sumir do registro.
        SelProgress.hide();
        await refreshTabs();
        // Volta tudo a indeterminado na proxima volta do poll.
        pollValues();
        return;
      }
      // Conectar nao bloqueia a resposta: o servidor responde 202 com o id do
      // job e a barra acompanha o resto (a descoberta de bits num FID sem
      // cache leva minutos). Por isso e' fetch + track, e nao
      // SelProgress.post -- este daria a barra por encerrada no 202.
      SelProgress.begin('Conectando ao relé...');
      const r = await fetch(withD('/connect'), { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = d.error || ('falha ao conectar (HTTP ' + r.status + ')');
        SelProgress.fail(msg);
        alert(msg);
        await refreshTabs();
        return;
      }
      if (d.job) SelProgress.track(d.job, {from: 0, to: 100});
      await refreshTabs();
    } catch (e) {
      SelProgress.fail(String(e));
      alert('Falha ao conectar: ' + e);
    } finally {
      btn.disabled = false;
      updateConnButton();
    }
  });
})();

// A faixa de abas se atualiza sozinha: e' por ela que uma conexao que falha
// em segundo plano aparece (bolinha vermelha + motivo no title).
setInterval(refreshTabs, 2000);
valuesTick();

renderTabs();
if (activeDiagram) switchDiagram(activeDiagram);
