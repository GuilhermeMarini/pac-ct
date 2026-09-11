(() => {
  const $ = (id) => document.getElementById(id);
  const status = $('status');
  function setStatus(msg, kind) {
    status.textContent = msg || '';
    status.className = kind || '';
  }

  // ----- estado client-side ------------------------------------------------
  // A lista de arquivos do projeto; atribuida mais abaixo, mas `loadState()`
  // a toca antes disso na primeira carga.
  let PICKER = null;
  const S = {
    rdbs: [],                 // lista de RDBs carregados (state do server)
    selectedRdbKeys: new Set(),
    relays: [],               // [{rdb_key, name, model, ip, family, is_relay}]
    selectedRelays: [],       // [{rdb_key, relay_name}]
    targetFamily: null,
    groups: [],               // catalogo com `present_in_all`
    selectedGroups: new Set(),
    diff: null,               // payload retornado por /diff
    activeTab: null,
    filterText: '',
    visibleVerdicts: new Set([
      'EQUAL', 'EQUAL_LOGIC_DIFF_COMMENT', 'DISPLACED', 'VB_DIFF',
      'EQUIVALENT', 'DIFFERENT', 'MISSING',
    ]),
    relayOrder: [],   // ordem das colunas (drag-and-drop nos headers)
  };

  // ----- stepper -----------------------------------------------------------
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

  // ----- back button -------------------------------------------------------
  // O menu fica sempre no ar em "/" -- nao ha mais teardown/espera de porta.
  // Guardado contra null porque o cabecalho tematizado troca o <button> por um
  // <a class="lnk"> que navega sozinho: sem a guarda, o TypeError aborta o
  // resto deste IIFE e nada abaixo daqui chega a ser ligado -- inclusive o
  // upload de RDB e o loadState() do fim. Mesmo padrao de vlan_mapper.py,
  // vb_updater.py e gle_exporter.py.
  const btnMenu = document.getElementById('back-to-menu');
  if (btnMenu) btnMenu.addEventListener('click', () => { window.location.href = '/'; });

  // ----- step 1: RDB list / upload / relay list ---------------------------
  async function loadState() {
    let j;
    try {
      const r = await fetch('/state');
      if (!r.ok) {
        setStatus(`Falha ao ler o estado: HTTP ${r.status}`, 'err');
        return;
      }
      j = await r.json();
    } catch (e) {
      setStatus(`Falha ao ler o estado: ${e}`, 'err');
      return;
    }
    S.rdbs = j.rdbs || [];
    // A contagem de reles por linha vem daqui, entao a lista se redesenha
    // quando o estado chega -- e a marcacao das linhas ja escolhidas volta
    // com ela (o picker guarda o pedido ate ter o que marcar).
    if (PICKER) PICKER.refresh();
    renderRelayList();
  }

  // A lista de RDBs e' a do PROJETO, desenhada pelo `SelLibrary.picker`
  // (multi): clicar numa linha marca/desmarca aquele arquivo e a lista de
  // reles abaixo se refaz na hora. Nao ha "carregar" antes -- `/state` ja
  // devolve todos os RDBs do acervo com seus reles.
  function aoEscolherRdbs(files) {
    const antes = new Set(S.selectedRdbKeys);
    S.selectedRdbKeys = new Set(files.map((f) => f.short_sha));
    for (const k of antes) {
      if (S.selectedRdbKeys.has(k)) continue;
      // Tirou o RDB da selecao: os reles dele saem junto, senao a familia
      // "fantasma" continua filtrando a lista.
      S.selectedRelays = S.selectedRelays.filter((s) => s.rdb_key !== k);
    }
    if (S.selectedRelays.length === 0) S.targetFamily = null;
    setStatus('');
    renderRelayList();
  }

  function renderRelayList() {
    const el = $('relay-list');
    el.innerHTML = '';
    const flat = [];
    for (const rdb of S.rdbs) {
      if (!S.selectedRdbKeys.has(rdb.key)) continue;
      for (const r of rdb.relays) {
        flat.push({ ...r, rdb_key: rdb.key, rdb_filename: rdb.filename });
      }
    }
    S.relays = flat;
    if (flat.length === 0) {
      el.innerHTML = '<div class="empty-state">Selecione um RDB acima.</div>';
      updateRelayCount(0, 0);
      updateContinueButton();
      return;
    }
    // Sort by family then name
    flat.sort((a, b) => {
      const fa = a.family || 'z'; const fb = b.family || 'z';
      if (fa !== fb) return fa < fb ? -1 : 1;
      return a.name < b.name ? -1 : 1;
    });
    // Quando uma familia ja foi escolhida, escondemos os reles das outras
    // familias da lista (em vez de mostrar disabled). Reles que nao sao
    // de protecao (SEL-2414/2440) ainda aparecem disabled, porque o usuario
    // pode querer ver que eles existem mas nao sao comparaveis.
    const visible = flat.filter(r =>
      !S.targetFamily || !r.family || r.family === S.targetFamily
    );
    const hidden = flat.length - visible.length;
    updateRelayCount(visible.length, hidden);
    for (const r of visible) {
      const isSelected = S.selectedRelays.some(s => s.rdb_key === r.rdb_key && s.relay_name === r.name);
      const disabled = !r.is_relay;
      const row = document.createElement('label');
      row.className = 'relay-row';
      if (isSelected) row.classList.add('selected');
      if (disabled) row.classList.add('disabled');
      const badge = r.family
        ? `<span class="badge fam-${r.family}">${r.family.toUpperCase()}</span>`
        : `<span class="badge nonrelay">N/A</span>`;
      row.innerHTML = `
        <input type="checkbox" ${isSelected ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
        <span class="name">${escapeHtml(r.name)}</span>
        <span class="model">${escapeHtml(r.model || '?')}</span>
        <span class="ip">${escapeHtml(r.ip || '')}</span>
        ${badge}
        <span class="rdb-tag" title="${escapeHtml(r.rdb_filename)}">${escapeHtml(r.rdb_filename.length > 28 ? r.rdb_filename.slice(0, 25) + '...' : r.rdb_filename)}</span>
      `;
      if (disabled) row.title = 'Nao e relé de protecao (provavelmente automacao/comunicacao).';
      row.addEventListener('click', (ev) => {
        ev.preventDefault();
        if (disabled) return;
        if (isSelected) {
          S.selectedRelays = S.selectedRelays.filter(s => !(s.rdb_key === r.rdb_key && s.relay_name === r.name));
          if (S.selectedRelays.length === 0) S.targetFamily = null;
        } else {
          if (S.selectedRelays.length >= 7) {
            setStatus('Limite de 7 relés por comparação.', 'err');
            return;
          }
          S.selectedRelays.push({ rdb_key: r.rdb_key, relay_name: r.name });
          S.targetFamily = r.family;
        }
        setStatus('');
        renderRelayList();
      });
      el.appendChild(row);
    }
    updateContinueButton();
  }

  function updateContinueButton() {
    const btn = $('to-step-2');
    btn.disabled = S.selectedRelays.length < 2;
  }

  function updateRelayCount(visible, hidden) {
    const el = $('relay-count');
    if (!el) return;
    const sel = S.selectedRelays.length;
    const fam = S.targetFamily ? ` ${S.targetFamily}` : '';
    const hiddenTxt = hidden > 0 ? `, ${hidden} ocultos (família diferente)` : '';
    el.textContent = `${sel}/7 selecionados${fam} - ${visible} visiveis${hiddenTxt}`;
  }

  $('to-step-2').addEventListener('click', async () => {
    setStatus('Carregando catalogo de grupos...');
    // Sem o try o rejeito da rede fica sem tratamento: o status continua em
    // "Carregando catalogo de grupos..." para sempre e o stepper nao avanca --
    // a tela simplesmente para, sem dizer nada.
    let j;
    try {
      const resp = await fetch('/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ relays: S.selectedRelays }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setStatus(`Falha: ${err.error || resp.statusText}`, 'err');
        return;
      }
      j = await resp.json();
    } catch (e) {
      setStatus(`Falha ao carregar os grupos: ${e}`, 'err');
      return;
    }
    S.groups = j.groups || [];
    S.targetFamily = j.family;
    // Pre-seleciona o primeiro grupo de ajustes -- 'L1' ou '1', conforme o
    // dialeto da familia -- e so' se ele existir em TODOS os reles escolhidos;
    // caso contrario a selecao comeca vazia. Quem decide e' a ordem de
    // S.groups: num 3xx, que tem os dois, '1' (Grupo 1) vem antes de 'L1'.
    const l1 = S.groups.find(g => g.key === 'L1' || g.key === '1');
    if (l1 && l1.present_in_all) {
      S.selectedGroups = new Set([l1.key]);
    } else {
      S.selectedGroups = new Set();
    }
    renderGroupGrid();
    setStep(2);
    setStatus('');
  });

  // ----- step 2: group picker ---------------------------------------------
  function renderGroupGrid() {
    const el = $('group-grid');
    el.innerHTML = '';
    for (const g of S.groups) {
      const disabled = g.present_count === 0;
      const card = document.createElement('div');
      card.className = 'group-card';
      if (S.selectedGroups.has(g.key)) card.classList.add('selected');
      if (disabled) card.classList.add('disabled');
      const partial = g.present_in_any && !g.present_in_all
        ? `<span class="partial" title="Presente em ${g.present_count}/${g.total}">parcial</span>`
        : '';
      card.innerHTML = `
        <span class="gkey">${escapeHtml(g.key)}</span>
        <span class="glabel">${escapeHtml(g.label)}</span>
        ${partial}
      `;
      card.title = `${g.file} - presente em ${g.present_count}/${g.total} relés`;
      card.addEventListener('click', () => {
        if (disabled) return;
        if (S.selectedGroups.has(g.key)) S.selectedGroups.delete(g.key);
        else S.selectedGroups.add(g.key);
        renderGroupGrid();
        $('to-step-3').disabled = S.selectedGroups.size === 0;
      });
      el.appendChild(card);
    }
    $('to-step-3').disabled = S.selectedGroups.size === 0;
  }

  $('group-all').addEventListener('click', () => {
    for (const g of S.groups) if (g.present_in_any) S.selectedGroups.add(g.key);
    renderGroupGrid();
  });
  $('group-none').addEventListener('click', () => { S.selectedGroups.clear(); renderGroupGrid(); });
  $('group-logic').addEventListener('click', () => {
    S.selectedGroups.clear();
    for (const g of S.groups) if (g.has_logic && g.present_in_any) S.selectedGroups.add(g.key);
    renderGroupGrid();
  });
  $('back-to-1').addEventListener('click', () => setStep(1));

  $('to-step-3').addEventListener('click', async () => {
    setStatus('Computando diff...');
    const groups = [...S.selectedGroups];
    const resp = await SelProgress.post('/diff',
      { relays: S.selectedRelays, groups },
      { label: 'Computando diff...', doneLabel: 'Diff pronto.' });
    if (!resp.ok) {
      setStatus(`Falha: ${(resp.data && resp.data.error) || resp.status}`, 'err');
      return;
    }
    S.diff = resp.data;
    if (S.diff.error) { setStatus(S.diff.error, 'err'); return; }
    setStatus('');
    S.activeTab = (S.diff.groups[0] || {}).key || null;
    S.relayOrder = S.diff.relays.map(r => r.key);
    // setStep antes de renderizar: freezeColumns/fitDiffHeight medem o layout,
    // e a view escondida mede zero.
    setStep(3);
    renderTabs(); renderVerdictFilters(); renderDiffContent();
  });

  // ----- step 3: diff render ---------------------------------------------
  function flattenVars(group) {
    const out = [];
    for (const s of group.sections || []) {
      for (const v of s.variables) out.push(v);
    }
    return out;
  }
  function countVerdicts(group) {
    const counter = {
      EQUAL: 0, EQUAL_LOGIC_DIFF_COMMENT: 0, DISPLACED: 0, VB_DIFF: 0,
      EQUIVALENT: 0, DIFFERENT: 0, MISSING: 0,
    };
    for (const v of flattenVars(group)) counter[v.worst_verdict] = (counter[v.worst_verdict] || 0) + 1;
    return counter;
  }
  function diffCount(group) {
    let n = 0;
    for (const v of flattenVars(group)) if (v.worst_verdict !== 'EQUAL') n++;
    return n;
  }

  function renderTabs() {
    const el = $('tabs');
    el.innerHTML = '';
    for (const g of S.diff.groups) {
      const tab = document.createElement('button');
      tab.className = 'grp-tab';
      if (g.key === S.activeTab) tab.classList.add('active');
      const label = S.diff.group_labels[g.key] || g.key;
      const dc = diffCount(g);
      const dcStr = dc > 0 ? `<span class="stat">${dc}</span>` : '';
      tab.title = dc > 0 ? `${label} - ${dc} diff` : label;
      tab.innerHTML = `<span class="k">${escapeHtml(g.key)}</span>`
        + `<span class="lbl">${escapeHtml(label)}</span>${dcStr}`;
      tab.addEventListener('click', () => {
        S.activeTab = g.key; renderTabs(); renderVerdictFilters(); renderDiffContent();
      });
      el.appendChild(tab);
    }
  }

  // Pills de filtro por verdict. Cada checkbox controla se variaveis
  // daquele worst_verdict aparecem no diff. Counts sao da aba ativa.
  // Rotulos PT-BR exibidos no diff (celulas da coluna Status + pills de
  // filtro). Chaves internas (EQUAL, DISPLACED, ...) ficam em ingles --
  // sao usadas em classes CSS, no estado do front e no payload do backend.
  const VERDICT_LABEL = {
    EQUAL: 'IGUAL',
    EQUAL_LOGIC_DIFF_COMMENT: 'SÓ O COMENTÁRIO',
    DISPLACED: 'DESLOCADO',
    EQUIVALENT: 'EQUIVALENTE',
    VB_DIFF: 'DIFERENÇA DE VB',
    DIFFERENT: 'DIFERENTE',
    MISSING: 'FALTANTE',
  };
  // Veredito e' sempre <span class="j j-*">: pastilha na Regua, rotulo
  // tipografico na Folha, carimbo girado no Caderno. Quem pinta e' o tema.
  const VERDICT_J = {
    EQUAL: 'j-ok',
    EQUAL_LOGIC_DIFF_COMMENT: 'j-comment',
    DISPLACED: 'j-displaced',
    EQUIVALENT: 'j-equiv',
    VB_DIFF: 'j-vb',
    DIFFERENT: 'j-dif',
    MISSING: 'j-falta',
  };
  const verdictSpan = (vk) =>
    `<span class="j ${VERDICT_J[vk] || 'j-falta'}">`
    + escapeHtml(VERDICT_LABEL[vk] || vk) + '</span>';
  const VERDICT_ORDER = [
    ['EQUAL',                    'eq'],
    ['EQUAL_LOGIC_DIFF_COMMENT', 'cm'],
    ['DISPLACED',                'disp'],
    ['EQUIVALENT',               'equiv'],
    ['VB_DIFF',                  'vbd'],
    ['DIFFERENT',                'diff'],
    ['MISSING',                  'miss'],
  ];
  const VERDICT_TITLES = {
    EQUAL: 'Texto idêntico',
    EQUAL_LOGIC_DIFF_COMMENT: 'Lógica igual, só o comentário difere',
    DISPLACED: 'Mesmo conteúdo, slot/posição diferente',
    EQUIVALENT: 'Lógica equivalente após normalização',
    VB_DIFF: 'Diferença apenas em números de VB###',
    DIFFERENT: 'Conteúdo realmente diferente',
    MISSING: 'Ausente em pelo menos um relé',
  };

  function renderVerdictFilters() {
    const el = $('verdict-filters');
    el.innerHTML = '';
    const grp = S.diff && S.diff.groups.find(g => g.key === S.activeTab);
    const counts = grp ? countVerdicts(grp) : {};
    for (const [vk, dotClass] of VERDICT_ORDER) {
      const on = S.visibleVerdicts.has(vk);
      const count = counts[vk] || 0;
      const label = VERDICT_LABEL[vk] || vk;
      const pill = document.createElement('label');
      pill.className = 'vf' + (on ? '' : ' off');
      pill.title = VERDICT_TITLES[vk] || vk;
      pill.innerHTML = `
        <input type="checkbox" ${on ? 'checked' : ''}>
        <span class="dot ${dotClass}"></span>
        <span>${escapeHtml(label)}</span>
        <span class="count">${count}</span>
      `;
      pill.querySelector('input').addEventListener('change', (ev) => {
        if (ev.target.checked) S.visibleVerdicts.add(vk);
        else S.visibleVerdicts.delete(vk);
        renderVerdictFilters(); renderDiffContent();
      });
      el.appendChild(pill);
    }
  }

  function renderDiffContent() {
    const out = $('diff-content');
    out.innerHTML = '';
    const grp = S.diff.groups.find(g => g.key === S.activeTab);
    if (!grp) { out.innerHTML = '<div class="empty-state">Sem dados.</div>'; return; }
    const flat = flattenVars(grp);
    const counts = countVerdicts(grp);
    $('diff-summary').textContent =
      `${flat.length} variáveis | ${counts.EQUAL} IGUAL | ${counts.EQUAL_LOGIC_DIFF_COMMENT} +coment | ${counts.DISPLACED} DESLOC | ${counts.VB_DIFF} VB | ${counts.EQUIVALENT} EQUIV | ${counts.DIFFERENT} DIF | ${counts.MISSING} faltam`;

    const filterText = S.filterText.toLowerCase();
    // Re-filtra preservando a estrutura de secoes; secao vazia some.
    const sections = (grp.sections || []).map(s => ({
      key: s.key,
      label: s.label,
      variables: s.variables.filter(v => {
        if (!S.visibleVerdicts.has(v.worst_verdict)) return false;
        if (filterText && !v.name.toLowerCase().includes(filterText)) return false;
        return true;
      }),
    })).filter(s => s.variables.length > 0);

    if (sections.length === 0) {
      out.innerHTML = '<div class="empty-state">Nada a mostrar com os filtros atuais.</div>';
      fitDiffHeight();
      return;
    }
    const wrap = document.createElement('div');
    wrap.className = 'diff-wrap';

    // Cabecalho com os reles
    const relayByKey = {};
    for (const r of S.diff.relays) relayByKey[r.key] = r;
    // Order = S.relayOrder, mas filtra chaves obsoletas se o diff for refeito
    // com selecao diferente, e anexa novos reles ao fim.
    const orderedKeys = S.relayOrder.filter(k => relayByKey[k]);
    for (const r of S.diff.relays) if (!orderedKeys.includes(r.key)) orderedKeys.push(r.key);
    S.relayOrder = orderedKeys;
    const nCols = 3 + orderedKeys.length + 1;  // kind + var + field + relays + verdict
    let thead = '<thead><tr><th></th><th>Variável</th><th>Campo</th>';
    for (const key of orderedKeys) {
      const r = relayByKey[key];
      const title = r.rdb_filename ? `${r.label}\n${r.rdb_filename}\n\n(arraste para reordenar)` : r.label + '\n\n(arraste para reordenar)';
      const sub = r.rdb_filename
        ? `<span class="relay-rdb">${escapeHtml(r.rdb_filename)}</span>`
        : '';
      thead += `<th draggable="true" class="relay-col" data-relay-key="${escapeHtml(key)}" title="${escapeHtml(title)}"><span class="relay-name">${escapeHtml(r.label)}</span>${sub}</th>`;
    }
    thead += '<th>Status</th></tr></thead>';

    let tbody = '<tbody>';
    for (const sec of sections) {
      // Header de secao -- omitido na secao "_default" (variaveis sem
      // secao definida em familias/grupos sem catalogo de secoes).
      if (sec.label) {
        tbody += `<tr class="section-header"><td colspan="${nCols}">${escapeHtml(sec.label)}</td></tr>`;
      }
      for (const v of sec.variables) {
        const vClass = `v-${v.worst_verdict}`;
        const firstField = v.fields[0];
        const restFields = v.fields.slice(1);
        const kindIcon = ({
          latch: 'L', timer: 'T', counter: 'C', math: 'M',
          direct: 'D', enum: 'E', number: 'N', string: 'S',
        })[v.var_kind] || '?';
        tbody += `<tr class="var-row ${vClass}">`;
        tbody += `<td class="kind-icon" title="${escapeHtml(v.var_kind)}">${escapeHtml(kindIcon)}</td>`;
        tbody += `<td class="var-name">${escapeHtml(v.name)}</td>`;
        tbody += `<td class="var-name">${escapeHtml(firstField.name)}</td>`;
        const firstMap = cellsByKey(firstField);
        for (const key of orderedKeys) tbody += renderCell(firstMap[key]);
        const note = firstField.note ? ` <span style="opacity:.7;font-size:.85em">(${escapeHtml(firstField.note)})</span>` : '';
        tbody += `<td class="verdict-cell v-${firstField.verdict}">${verdictSpan(firstField.verdict)}${note}</td>`;
        tbody += '</tr>';
        for (const fr of restFields) {
          tbody += `<tr class="field-row ${vClass}">`;
          tbody += '<td></td><td class="var-name"></td>';
          tbody += `<td class="var-name">${escapeHtml(fr.name)}</td>`;
          const fMap = cellsByKey(fr);
          for (const key of orderedKeys) tbody += renderCell(fMap[key]);
          const note2 = fr.note ? ` <span style="opacity:.7;font-size:.85em">(${escapeHtml(fr.note)})</span>` : '';
          tbody += `<td class="verdict-cell v-${fr.verdict}">${verdictSpan(fr.verdict)}${note2}</td>`;
          tbody += '</tr>';
        }
      }
    }
    tbody += '</tbody>';
    wrap.innerHTML = `<table class="diff">${thead}${tbody}</table>`;
    out.appendChild(wrap);
    attachColumnReorder(wrap);
    freezeColumns(wrap);
    fitDiffHeight();
  }

  // As tres primeiras colunas ficam congeladas a esquerda; como a largura da
  // coluna "Variavel" depende do conteudo, o deslocamento das seguintes so da
  // para saber depois do layout.
  function freezeColumns(wrap) {
    const head = wrap.querySelector('table.diff thead tr');
    if (!head || head.cells.length < 3) return;
    const w0 = head.cells[0].getBoundingClientRect().width;
    const w1 = head.cells[1].getBoundingClientRect().width;
    const table = wrap.querySelector('table.diff');
    table.style.setProperty('--frz-1', w0 + 'px');
    table.style.setProperty('--frz-2', (w0 + w1) + 'px');
  }

  // A tabela precisa rolar dentro do proprio quadro para o cabecalho grudar no
  // topo. A altura util sobra do que os filtros ocuparam acima e vale tambem
  // para o menu de grupos, que rola em paralelo.
  function fitDiffHeight() {
    const layout = $('diff-layout');
    const tabs = $('tabs');
    const top = layout.getBoundingClientRect().top + window.scrollY;
    const h = Math.max(260, window.innerHeight - top - 46);
    layout.style.setProperty('--diff-h', h + 'px');
    // Em tela estreita o menu vira uma faixa acima da tabela e come altura
    // util; a tabela ganha uma variavel propria com o que sobrou.
    const stacked = tabs.getBoundingClientRect().top
                  < $('diff-content').getBoundingClientRect().top - 4;
    layout.style.setProperty(
      '--table-h', (stacked ? Math.max(220, h - tabs.offsetHeight - 10) : h) + 'px');
  }

  let fitTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(() => {
      const wrap = document.querySelector('#diff-content .diff-wrap');
      if (wrap) freezeColumns(wrap);
      if (S.diff) fitDiffHeight();
    }, 120);
  });

  function cellsByKey(fr) {
    const m = {};
    for (const c of fr.cells) m[c.relay] = c;
    return m;
  }

  function attachColumnReorder(wrap) {
    let dragKey = null;
    wrap.querySelectorAll('th.relay-col').forEach(th => {
      th.addEventListener('dragstart', (ev) => {
        dragKey = th.dataset.relayKey;
        ev.dataTransfer.effectAllowed = 'move';
        try { ev.dataTransfer.setData('text/plain', dragKey); } catch (e) {}
        th.classList.add('dragging');
      });
      th.addEventListener('dragend', () => {
        th.classList.remove('dragging');
        wrap.querySelectorAll('th.relay-col.drop-target').forEach(t => t.classList.remove('drop-target'));
        dragKey = null;
      });
      th.addEventListener('dragover', (ev) => {
        if (!dragKey || dragKey === th.dataset.relayKey) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        th.classList.add('drop-target');
      });
      th.addEventListener('dragleave', () => th.classList.remove('drop-target'));
      th.addEventListener('drop', (ev) => {
        ev.preventDefault();
        th.classList.remove('drop-target');
        const src = dragKey;
        const dst = th.dataset.relayKey;
        if (!src || src === dst) return;
        const order = S.relayOrder.slice();
        const si = order.indexOf(src);
        const di = order.indexOf(dst);
        if (si < 0 || di < 0) return;
        order.splice(si, 1);
        order.splice(di, 0, src);
        S.relayOrder = order;
        renderDiffContent();
      });
    });
  }

  function renderCell(c) {
    if (!c.present) return '<td class="value-cell absent">&mdash;</td>';
    const src = c.source_file
      ? `<span class="src" title="${escapeHtml(c.rdb_filename)}">${escapeHtml(c.source_file)}:${c.source_lineno}</span>`
      : '';
    const comment = c.comment
      ? `<span class="comment"># ${escapeHtml(c.comment)}</span>` : '';
    const body = c.body || c.value;
    return `<td class="value-cell"><span class="body">${escapeHtml(body)}</span>${comment}${src}</td>`;
  }

  $('back-to-2').addEventListener('click', () => setStep(2));
  $('filter-name').addEventListener('input', (ev) => { S.filterText = ev.target.value; renderDiffContent(); });
  $('filter-all').addEventListener('click', () => {
    for (const [vk] of VERDICT_ORDER) S.visibleVerdicts.add(vk);
    renderVerdictFilters(); renderDiffContent();
  });
  $('filter-none').addEventListener('click', () => {
    S.visibleVerdicts.clear();
    renderVerdictFilters(); renderDiffContent();
  });
  $('filter-diffs').addEventListener('click', () => {
    S.visibleVerdicts.clear();
    for (const [vk] of VERDICT_ORDER) if (vk !== 'EQUAL') S.visibleVerdicts.add(vk);
    renderVerdictFilters(); renderDiffContent();
  });

  // ----- RDBs do projeto ---------------------------------------------------
  // Multi de proposito: a comparacao aceita ate 7 reles, e eles podem vir de
  // RDBs diferentes -- e' o caso que impede um "RDB ativo" unico.
  // Sem `annotate`: a contagem de reles ja vem no `detail` do proprio acervo,
  // e repetir "27 IED(s)" duas vezes na mesma linha nao informa nada.
  PICKER = SelLibrary.picker('rdb-list', {
    kind: 'rdb', multi: true, label: 'RDBs do projeto (clique para escolher)',
    onPick: (files) => aoEscolherRdbs(files),
  });

  // ----- helpers -----------------------------------------------------------
  function escapeHtml(s) {
    return (s == null ? '' : String(s))
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  }

  // ----- init --------------------------------------------------------------
  loadState();
})();
