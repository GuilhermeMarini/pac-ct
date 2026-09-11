(() => {
  const $ = (id) => document.getElementById(id);

  // RDB-derived names (IED/relaytype and session names) come from the uploaded
  // file's OLE storage/content and are never sanitized server-side, so they
  // must be escaped before reaching innerHTML. Same helpers as landing.html
  // and editor.html, kept in sync on purpose.
  function escapeHtml(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function setStatus(msg, kind) {
    $('status').textContent = msg || '';
    $('status').className = kind || '';
  }

  // Espelha set_dnp.same_model de proposito: se a tela oferecesse um destino
  // que o servidor recusa, o usuario so descobriria no clique final.
  function sameModel(a, b) {
    const ka = (a == null ? '' : String(a)).trim().toUpperCase();
    const kb = (b == null ? '' : String(b)).trim().toUpperCase();
    return ka !== '' && ka === kb;
  }

  // Os dois seletores de arquivo, atribuidos no fim deste bloco. Declarados
  // aqui why `drawStep1` os toca e roda antes, na carga.
  let PICK1 = null, PICK2 = null;

  const S = {
    rdbs: [],            // /rdbs: [{rdb, name, dirty}] -- so' pra pendencias
    relays: {},           // rdb -> /relays payload, buscado sob demanda
    srcRdb: null, srcRelay: null, srcSession: null,
    dstRdb: null, dstRelays: new Set(),
    policy: 'same',      // 'same' | 'all'
    mapData: null,       // payload de /map da sessao de origem
    picked: new Set(), // chaves dos pontos que serao copiados
    tab: null,           // bloco aberto no passo 2 (BI/BO/AI/AO/CO)
  };

  // Mesma chave da pagina de relays: quem chega pelo menu, sem `?rdb=`,
  // continua no RDB em que estava trabalhando.
  const PICK_KEY = 'dnp-map:rdb';
  function rememberPick(key) {
    try { sessionStorage.setItem(PICK_KEY, key); } catch (e) { /* sem storage */ }
  }
  function rememberedPick() {
    try { return sessionStorage.getItem(PICK_KEY); } catch (e) { return null; }
  }

  function setStep(n) {
    document.querySelectorAll('.stepper .step').forEach((el) => {
      const k = +el.dataset.step;
      el.classList.toggle('active', k === n);
      el.classList.toggle('done', k < n);
    });
    document.querySelectorAll('.view').forEach((el) => {
      el.classList.toggle('active', el.id === ('view-' + n));
    });
  }

  function pendingOf(key) {
    const item = S.rdbs.find(x => x.rdb === key);
    if (!item) return 0;
    return (item.dirty || []).reduce(
      (n, d) => n + Object.values(d.sessions).reduce((a, b) => a + b, 0), 0);
  }

  function rdbName(key) {
    const r = S.rdbs.find(x => x.rdb === key);
    return r ? r.name : key;
  }
  // Na confirmacao o name sozinho nao basta: dois arquivos do projeto podem
  // se chamar igual (dois `source.rdb`, duas revisoes baixadas na mesma
  // pasta), e a chave curta e' o sha256 do conteudo -- e' ela que decide qual
  // arquivo vai ser escrito.
  function rdbFullName(key) {
    return rdbName(key) + ' (' + key + ')';
  }
  function relayOf(rdb, name) {
    return ((S.relays[rdb] || {}).relays || []).find(r => r.name === name) || null;
  }
  function sourceRelay() { return relayOf(S.srcRdb, S.srcRelay); }

  // ----- carga ------------------------------------------------------------

  async function loadRdbs() {
    try {
      const r = await (await fetch('/rdbs')).json();
      S.rdbs = (r && r.rdbs) || [];
    } catch (e) { S.rdbs = []; }
  }

  // /relays parseia todo SET_D de todo rele do RDB; guardar o resultado e' o
  // que impede de refazer isso a cada clique. O RDB e' enderecado pelo sha256
  // do conteudo, entao a list nao muda enquanto a chave existir.
  async function loadRelays(rdb) {
    if (S.relays[rdb]) return S.relays[rdb];
    const r = await (await fetch('/relays?rdb=' + encodeURIComponent(rdb))).json();
    if (!r.ok) { setStatus(r.error || 'RDB não está nesta sessão.', 'err'); return null; }
    S.relays[rdb] = r;
    return r;
  }

  function drawPending(dirty) {
    const list = dirty || [];
    $('pending-count').textContent = list.reduce(
      (n, d) => n + Object.values(d.sessions).reduce((a, b) => a + b, 0), 0);
    const ul = $('pending-list');
    ul.innerHTML = '';
    for (const d of list) {
      const li = document.createElement('li');
      li.textContent = d.relay + ' — ' + Object.entries(d.sessions)
        .map(([k, v]) => k + ' (' + v + ')').join(', ');
      ul.appendChild(li);
    }
  }

  // ----- passo 1: origem --------------------------------------------------

  async function pickSourceRdb(rdb) {
    setStatus('Lendo os mapas DNP do RDB…');
    const r = await loadRelays(rdb);
    if (!r) return;
    setStatus('');
    S.srcRdb = rdb;
    rememberPick(rdb);
    if (!relayOf(rdb, S.srcRelay)) { S.srcRelay = null; S.srcSession = null; }
    // O destino acompanha a origem ate' o usuario dizer o contrario: copiar
    // dentro do mesmo RDB e' o caso comum, e o passo 2 ja abre pronto.
    S.dstRdb = rdb;
    S.dstRelays.clear();
    drawStep1();
  }

  function drawStep1() {
    if (PICK1) PICK1.select(S.srcRdb);
    const el = $('list-1');
    const relays = ((S.relays[S.srcRdb] || {}).relays) || [];
    el.innerHTML = '';
    if (!S.srcRdb || !relays.length) {
      el.innerHTML = '<div class="empty-state">Escolha um RDB acima.</div>';
      $('count-1').textContent = '';
      $('session-label').hidden = true;
      $('to-points').disabled = true;
      return;
    }
    for (const r of relays) {
      const sel = r.name === S.srcRelay;
      const row = document.createElement('label');
      row.className = 'relay-row' + (sel ? ' selected' : '');
      row.innerHTML =
        '<input type="radio" name="source"' + (sel ? ' checked' : '') + '>' +
        '<span class="name">' + escapeHtml(r.name) + '</span>' +
        '<span class="model">' + escapeHtml(r.relaytype || 'modelo desconhecido') + '</span>' +
        '<span class="sess">' + escapeHtml(r.sessions.join(' · ')) + '</span>' +
        '<span class="why">' + escapeHtml(
          r.groups.length < r.sessions.length
            ? 'sessões iguais: ' + r.groups.map(g => g.join('=')).join(' · ') : '') +
        '</span>';
      row.addEventListener('click', (ev) => {
        ev.preventDefault();
        S.srcRelay = r.name;
        S.srcSession = r.sessions.includes(S.srcSession) ? S.srcSession : r.sessions[0];
        S.dstRelays.clear();
        drawStep1();
      });
      el.appendChild(row);
    }
    $('count-1').textContent = relays.length + ' IED(s) com mapa DNP';

    const src = sourceRelay();
    $('session-label').hidden = !src;
    if (src) {
      // A sessao pode vir do `?d=` da URL (o link "Copiar" da lista de reles
      // manda uma) e nao existir neste IED.
      if (!src.sessions.includes(S.srcSession)) S.srcSession = src.sessions[0];
      const sel = $('source-session');
      sel.innerHTML = '';
      for (const sn of src.sessions) {
        const o = document.createElement('option');
        o.value = o.textContent = sn;
        o.selected = sn === S.srcSession;
        sel.appendChild(o);
      }
    }
    $('to-points').disabled = !src;
  }

  $('source-session').addEventListener('change', (e) => {
    S.srcSession = e.target.value;
  });

  // ----- passo 2: quais pontos --------------------------------------------
  //
  // O mapData inteiro raramente e' o que se quer levar: um banco de relays iguais
  // costuma compartilhar os binarios e divergir nos analogicos, ou receber so'
  // o bloco que acabou de ser corrigido. Por padrao vem tudo isPicked -- copiar
  // o mapData e' o caso comum -- e desmarcar e' o que custa cliques, nao o
  // contrario.
  //
  // A selecao e' por PONTO BASE (`BI_00`, `AI_3`). A escala e a banda morta
  // nao sao pontos: viajam com o ponto que qualificam, aqui e no servidor,
  // como ja acontece no arrastar do editor.

  async function loadMap() {
    setStatus('Lendo o mapa DNP…');
    let m;
    try {
      m = await (await fetch('/map?rdb=' + encodeURIComponent(S.srcRdb) +
        '&relay=' + encodeURIComponent(S.srcRelay) +
        '&d=' + encodeURIComponent(S.srcSession))).json();
    } catch (e) {
      setStatus('Falha ao ler o mapa: ' + e, 'err');
      return;
    }
    if (!m.ok) { setStatus(m.error || 'Falha ao ler o mapa.', 'err'); return; }
    setStatus('');
    S.mapData = m;
    S.picked = new Set(allPoints());
    const blocks = Object.keys(m.blocks);
    if (!blocks.includes(S.tab)) S.tab = blocks[0] || null;
    drawStep2();
  }

  function allPoints() {
    if (!S.mapData) return [];
    return Object.values(S.mapData.blocks).flat().map((p) => p.key);
  }

  function tabPoints() {
    return (S.mapData && S.mapData.blocks[S.tab]) || [];
  }

  // Um point "filled" e' um que tem variavel mapeada. O slot livre e'
  // escrito `""` no 411L e `"NA"` no 751/2440 -- os dois querem dizer a mesma
  // coisa e nenhum e' um point que valha copiar sozinho.
  function filled(p) {
    const v = (p.value || '').trim().toUpperCase();
    return v !== '' && v !== 'NA';
  }

  function drawStep2() {
    if (!S.mapData) return;
    const tabs = $('block-tabs');
    tabs.innerHTML = '';
    for (const k of Object.keys(S.mapData.blocks)) {
      const rows = S.mapData.blocks[k];
      const pickedCount = rows.filter((p) => S.picked.has(p.key)).length;
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = k + ' ' + pickedCount + '/' + rows.length;
      b.setAttribute('aria-selected', k === S.tab);
      b.addEventListener('click', () => { S.tab = k; drawStep2(); });
      tabs.appendChild(b);
    }

    const rows = tabPoints();
    const hasSca = rows.some((p) => p.sca_key);
    const hasDbd = rows.some((p) => p.dbd_key);
    $('points-header').innerHTML =
      '<th class="mark"><input type="checkbox" id="block-box"></th>' +
      '<th>#</th><th>Variável</th>' +
      (hasSca ? '<th>Escala</th>' : '') +
      (hasDbd ? '<th>Banda morta</th>' : '');

    const tb = document.querySelector('#points-table tbody');
    tb.innerHTML = '';
    for (const point of rows) {
      const isPicked = S.picked.has(point.key);
      const tr = document.createElement('tr');
      tr.className = (isPicked ? '' : 'out ') + (filled(point) ? '' : 'blank');
      tr.innerHTML =
        '<td class="mark"><input type="checkbox"' + (isPicked ? ' checked' : '') +
          ' data-key="' + escapeAttr(point.key) + '"></td>' +
        '<td class="idx">' + point.index + '</td>' +
        '<td class="mono">' + escapeHtml(point.value || '—') + '</td>' +
        (hasSca ? '<td class="mono">' + escapeHtml(point.sca || '') + '</td>' : '') +
        (hasDbd ? '<td class="mono">' + escapeHtml(point.dbd || '') + '</td>' : '');
      // A linha inteira alterna a marca: acertar um checkbox de 15px numa
      // tabela de 300 linhas e' trabalho que a tela nao precisa dar.
      tr.addEventListener('click', (ev) => {
        if (ev.target.tagName !== 'INPUT') ev.preventDefault();
        toggle(point.key);
      });
      tb.appendChild(tr);
    }

    const blockBox = $('block-box');
    const pickedInTab = rows.filter((p) => S.picked.has(p.key)).length;
    blockBox.checked = pickedInTab === rows.length && rows.length > 0;
    blockBox.indeterminate = pickedInTab > 0 && pickedInTab < rows.length;
    blockBox.addEventListener('click', (ev) => {
      const on = ev.target.checked;
      for (const p of rows) {
        if (on) S.picked.add(p.key); else S.picked.delete(p.key);
      }
      drawStep2();
    });

    const total = allPoints().length;
    $('count-points').textContent =
      S.picked.size + ' de ' + total + ' ponto(s) marcado(s)';
    $('points-footnote').textContent =
      'Escala e banda morta viajam com o ponto. O que não estiver marcado ' +
      'fica como está no destino — a cópia não apaga ponto nenhum.';
    $('to-target').disabled = S.picked.size === 0;
  }

  function toggle(key) {
    if (S.picked.has(key)) S.picked.delete(key);
    else S.picked.add(key);
    drawStep2();
  }

  $('points-all').addEventListener('click', () => {
    S.picked = new Set(allPoints());
    drawStep2();
  });
  $('points-none').addEventListener('click', () => {
    S.picked = new Set();
    drawStep2();
  });
  $('points-filled').addEventListener('click', () => {
    S.picked = new Set(Object.values(S.mapData.blocks).flat()
      .filter(filled).map((p) => p.key));
    drawStep2();
  });

  // ----- passo 3: destino -------------------------------------------------

  async function pickTargetRdb(rdb) {
    setStatus('Lendo os mapas DNP do RDB…');
    const r = await loadRelays(rdb);
    if (!r) return;
    setStatus('');
    S.dstRdb = rdb;
    S.dstRelays.clear();
    drawStep3();
  }

  // Quem pode receber: mesmo modelo da origem, e nunca a propria origem
  // (o mesmo IED no mesmo RDB). Copiar entre as sessions de um mesmo IED tem
  // botao proprio no editor.
  function eligible(r) {
    const src = sourceRelay();
    if (!src) return {ok: false, why: ''};
    if (S.dstRdb === S.srcRdb && r.name === S.srcRelay) {
      return {ok: false, why: 'é a origem'};
    }
    if (!sameModel(r.relaytype, src.relaytype)) {
      return {ok: false, why: 'outro modelo'};
    }
    return {ok: true, why: ''};
  }

  function drawStep3() {
    if (PICK2) PICK2.select(S.dstRdb);
    const src = sourceRelay();
    $('dst-hint').textContent = src
      ? 'Pode ser outro RDB — só aparecem IEDs ' +
        (src.relaytype || 'do mesmo modelo') : '';
    $('session-name').textContent = S.srcSession || '?';
    drawPending(((S.relays[S.dstRdb] || {}).dirty) || []);

    const el = $('list-2');
    const relays = ((S.relays[S.dstRdb] || {}).relays) || [];
    el.innerHTML = '';
    if (!S.dstRdb || !relays.length) {
      el.innerHTML = '<div class="empty-state">Escolha um RDB acima.</div>';
      $('count-2').textContent = '';
      $('to-confirm').disabled = true;
      return;
    }
    let eligibleCount = 0;
    // Elegiveis primeiro. Num RDB de 30 IEDs os do mesmo modelo sao poucos e
    // caem no meio da list; quem nao pode receber continua visivel (e cinza,
    // com o motivo), porque "esse rele nao esta aqui" e "esse rele nao serve"
    // sao respostas diferentes. Sort estavel: dentro de cada grupo a ordem do
    // RDB e' preservada.
    const sorted = [...relays].sort(
      (a, b) => (eligible(b).ok ? 1 : 0) - (eligible(a).ok ? 1 : 0));
    for (const r of sorted) {
      const e = eligible(r);
      if (e.ok) eligibleCount++;
      const isPicked = S.dstRelays.has(r.name);
      const row = document.createElement('label');
      row.className = 'relay-row' + (isPicked ? ' selected' : '') +
                      (e.ok ? '' : ' disabled');
      row.innerHTML =
        '<input type="checkbox"' + (isPicked ? ' checked' : '') +
          (e.ok ? '' : ' disabled') + '>' +
        '<span class="name">' + escapeHtml(r.name) + '</span>' +
        '<span class="model">' + escapeHtml(r.relaytype || 'modelo desconhecido') + '</span>' +
        '<span class="sess">' + escapeHtml(r.sessions.join(' · ')) + '</span>' +
        '<span class="why">' + escapeHtml(e.why) + '</span>';
      if (e.ok) {
        row.addEventListener('click', (ev) => {
          ev.preventDefault();
          if (S.dstRelays.has(r.name)) S.dstRelays.delete(r.name);
          else S.dstRelays.add(r.name);
          drawStep3();
        });
      }
      el.appendChild(row);
    }
    $('count-2').textContent = S.dstRelays.size + ' marcado(s) de ' +
      eligibleCount + ' do mesmo modelo (' + relays.length + ' IEDs no RDB)';
    $('to-confirm').disabled = S.dstRelays.size === 0;
  }

  $('pick-all').addEventListener('click', () => {
    for (const r of ((S.relays[S.dstRdb] || {}).relays) || []) {
      if (eligible(r).ok) S.dstRelays.add(r.name);
    }
    drawStep3();
  });
  $('clear').addEventListener('click', () => {
    S.dstRelays.clear();
    drawStep3();
  });
  document.querySelectorAll('input[name=policy]').forEach((el) => {
    el.addEventListener('change', () => { S.policy = el.value; });
  });

  // ----- passo 4: confirmacao ---------------------------------------------

  // Quais sessoes de cada destino serao escritas. Com a politica "mesma", um
  // IED que nao tem uma sessao com esse nome nao ganha nenhuma -- e a tela diz
  // isso, em vez de escrever numa sessao que o usuario nao escolheu.
  function copyPlan() {
    const plan = [];
    for (const name of [...S.dstRelays].sort()) {
      const r = relayOf(S.dstRdb, name);
      if (!r) continue;
      const sessions = S.policy === 'all'
        ? r.sessions.slice()
        : (r.sessions.includes(S.srcSession) ? [S.srcSession] : []);
      plan.push({relay: r, sessions: sessions});
    }
    return plan;
  }

  function planTargets(plan) {
    const out = [];
    for (const p of plan) {
      for (const sn of p.sessions) out.push({relay: p.relay.name, session: sn});
    }
    return out;
  }

  function drawStep4() {
    const src = sourceRelay();
    const plan = copyPlan();
    const targets = planTargets(plan);
    $('result').innerHTML = '';

    // A contagem sai do que foi isPicked no passo 2, por bloco: dizer "150 de
    // 296 pontos — BI 100/100 · AI 50/100" antes de confirmar e' a diferenca
    // entre uma confirmacao e um "tem certeza?".
    const total = allPoints().length;
    const parts = Object.entries((S.mapData && S.mapData.blocks) || {})
      .map(([k, v]) => k + ' ' + v.filter((x) => S.picked.has(x.key)).length +
                       '/' + v.length);
    const pointsLine = S.picked.size + ' de ' + total + ' ponto(s) — ' +
      parts.join(' · ');

    $('summary').innerHTML =
      '<dl>' +
      '<dt>RDB de origem</dt><dd>' + escapeHtml(rdbFullName(S.srcRdb)) + '</dd>' +
      '<dt>IED de origem</dt><dd>' + escapeHtml(S.srcRelay) + ' · sessão ' +
        escapeHtml(S.srcSession) + '</dd>' +
      '<dt>Modelo</dt><dd>' + escapeHtml((src && src.relaytype) || '—') + '</dd>' +
      '<dt>Pontos</dt><dd>' + escapeHtml(pointsLine) + '</dd>' +
      '<dt>RDB de destino</dt><dd>' + escapeHtml(rdbFullName(S.dstRdb)) +
        (S.dstRdb === S.srcRdb ? ' (o mesmo)' : '') + '</dd>' +
      '<dt>Destinos</dt><dd>' + plan.length + ' IED(s), ' + targets.length +
        ' sessão(ões)</dd>' +
      '</dl>';

    const tb = document.querySelector('#targets-table tbody');
    tb.innerHTML = '';
    for (const p of plan) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="mono">' + escapeHtml(p.relay.name) + '</td>' +
        '<td>' + escapeHtml(p.relay.relaytype || '—') + '</td>' +
        '<td class="mono">' + escapeHtml(p.sessions.join(' · ') || '—') + '</td>' +
        '<td class="warn">' + escapeHtml(p.sessions.length ? '' :
          'não tem a sessão ' + S.srcSession + ' — nada será escrito') + '</td>';
      tb.appendChild(tr);
    }
    $('finish').disabled = targets.length === 0;
  }

  $('to-points').addEventListener('click', async () => {
    setStep(2);
    await loadMap();
  });
  $('back-source').addEventListener('click', () => { setStep(1); });
  $('to-target').addEventListener('click', async () => {
    if (!S.dstRdb) S.dstRdb = S.srcRdb;
    await loadRelays(S.dstRdb);
    drawStep3();
    setStep(3);
  });
  $('back-points').addEventListener('click', () => { setStep(2); });
  $('to-confirm').addEventListener('click', () => {
    setStep(4);
    drawStep4();
  });
  $('back-target').addEventListener('click', () => { setStep(3); });

  $('finish').addEventListener('click', async () => {
    const targets = planTargets(copyPlan());
    if (!targets.length) return;
    const r = await (await fetch('/copy-to-relays', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rdb: S.srcRdb, relay: S.srcRelay, session: S.srcSession,
        dst_rdb: S.dstRdb, targets: targets, points: [...S.picked],
      }),
    })).json();
    if (!r.ok) {
      $('result').innerHTML = '<span class="warn">' + escapeHtml(r.error) + '</span>';
      return;
    }
    // O RDB de destino mudou de pendencias: a lista guardada em S.reles
    // carrega `dirty` junto, e ela e' a fonte do painel lateral.
    if (S.relays[S.dstRdb]) S.relays[S.dstRdb].dirty = r.dirty;
    drawPending(r.dirty);
    await loadRdbs();
    $('finish').disabled = true;
    $('result').innerHTML =
      '<b>' + r.touched + ' campo(s) alterado(s) em ' +
      escapeHtml(rdbName(S.dstRdb)) + '.</b><ul>' +
      r.targets.map(t => {
        const warnings = [];
        if (t.missing) warnings.push(t.missing + ' ponto(s) da origem sem campo ' +
                                   'correspondente no destino');
        if (t.extra) warnings.push(t.extra + ' ponto(s) do destino que a origem ' +
                                 'não tem (ficaram como estavam)');
        return '<li>' + escapeHtml(t.relay + ' · ' + t.session + ' — ' +
          (t.touched ? t.touched + ' campo(s) alterado(s)'
                     : 'já era igual, nada a fazer')) +
          (warnings.length ? ' <span class="warn">' +
            escapeHtml('⚠ ' + warnings.join('; ')) + '</span>' : '') + '</li>';
      }).join('') + '</ul>' +
      '<p>Nada foi gravado no arquivo ainda: exporte o RDB de destino em ' +
      '<a href="./">Relés</a>.</p>';
  });

  // ----- inicio -----------------------------------------------------------

  // Os dois passos escolhem do MESMO acervo, na mesma list: a chave curta de
  // um arquivo do projeto e' a chave desta ferramenta (as duas sao o sha256 do
  // conteudo), entao clicar numa linha ja carrega os IEDs dela.
  const annotate = (f) => pendingOf(f.short_sha)
    ? pendingOf(f.short_sha) + ' alteração(ões) pendente(s)' : '';
  PICK1 = SelLibrary.picker('rdb-list-1', {
    kind: 'rdb', label: 'RDB de origem', annotate: annotate,
    onPick: (f) => pickSourceRdb(f.short_sha),
  });
  PICK2 = SelLibrary.picker('rdb-list-2', {
    kind: 'rdb', label: 'RDB de destino', annotate: annotate,
    onPick: (f) => pickTargetRdb(f.short_sha),
  });

  (async () => {
    await loadRdbs();
    const P = new URLSearchParams(location.search);
    const wanted = P.get('rdb') || rememberedPick();
    const target = S.rdbs.find(x => x.rdb === wanted) || S.rdbs[0];
    if (!target) { drawStep1(); return; }
    S.srcRelay = P.get('relay');
    S.srcSession = P.get('d');
    await pickSourceRdb(target.rdb);
  })();
})();
