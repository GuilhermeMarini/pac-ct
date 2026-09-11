// A tela de comparacao do VB Updater (`/vb-updater/compare`): a tabela de VBs
// lado a lado, o filtro "Ocultar iguais" e os dois botoes que aplicam a copia
// em uma direcao ou na outra.
//
// Carregado por `<script src>` no mesmo ponto em que o corpo deste arquivo
// ficava embutido -- logo antes de `</body>`, sem `defer`, porque
// `inject_progress_runtime` enfia o `SelProgress` DEPOIS deste script.
//
// Sem bloco `page-data`: o que esta tela precisa saber -- rele, IED e GLE --
// vem da query string com que a landing navegou para ca, e o resto ja chega
// renderizado no HTML.
//
// As chaves deste arquivo sao simples. Enquanto ele estava embutido em
// `compare.html` todas eram duplas: aquele template e preenchido com
// `str.format()` (`{rdb_relay}`, `{rows}`, ...), e `{` solto ali e um campo
// de substituicao. Foi a unica diferenca entre o bloco antigo e este arquivo.
(function () {
  const KEY = 'vb-updater-hide-equal';
  const btn = document.getElementById('toggle-eq');
  function apply(on) {
    document.body.classList.toggle('hide-equal', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? 'Mostrar iguais' : 'Ocultar iguais';
  }
  try { apply(localStorage.getItem(KEY) === '1'); } catch (e) { apply(false); }
  btn.addEventListener('click', () => {
    const next = !document.body.classList.contains('hide-equal');
    apply(next);
    try { localStorage.setItem(KEY, next ? '1' : '0'); } catch (e) {}
  });
})();

// Apply buttons (Copiar SCD<->GLE)
(function setupApply() {
  const qs = new URLSearchParams(window.location.search);
  const relay = qs.get('relay') || '';
  const ied = qs.get('ied') || '';
  const gle = qs.get('gle') || '';
  const resultEl = document.getElementById('apply-result');

  function escHtml(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function escAttr(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
  }

  function renderResult(direction, data, isError) {
    resultEl.className = 'apply-result' + (isError ? ' err' : '');
    resultEl.style.display = 'block';
    if (isError) {
      resultEl.innerHTML = '<div class="title">Falha ao aplicar</div>'
        + '<div>' + escHtml(data.error || JSON.stringify(data)) + '</div>';
      return;
    }
    const st = data.stats || {};
    let lines = [];
    if (direction === 'scd-to-gle') {
      lines.push('Instancias atualizadas no GLE: <strong>' + (st.instances_updated||0) + '</strong>');
      lines.push('VBs com desc no SCD: ' + (st.vbs_in_scd_with_desc||0));
      const reservaN = st.vbs_in_scd_renamed_to_reserva||0;
      const reservaLabel = st.reserva_label || 'reserva';
      lines.push('VBs vazios no SCD renomeados para <code>' + escHtml(reservaLabel) + '</code>: <strong>' + reservaN + '</strong>');
      const qualityN = st.vbs_message_quality||0;
      if (qualityN) {
        // Overlaps "com desc": a message-quality VB with a description keeps
        // it, so this counts what they ARE, not what was relabelled.
        lines.push('VBs de qualidade da mensagem (<code>pubRxStatus</code>): <strong>' + qualityN + '</strong>');
      }
      lines.push('VBs do GLE sem ExtRef no SCD (não tocados): ' + (st.vbs_in_gle_not_in_scd||0));
      lines.push('Stream original/novo: ' + (st.original_stream_bytes||0).toLocaleString() + ' bytes (preservado)');
    } else {
      lines.push('ExtRef desc atualizados: <strong>' + (st.extrefs_updated||0) + '</strong>');
      lines.push('ExtRef sem desc anterior (inseridos): <strong>' + (st.extrefs_inserted_desc||0) + '</strong>');
      lines.push('VBs no SCD não alterados (sem comment no GLE): ' + (st.vbs_unchanged_no_match||0));
      lines.push('Tamanho do SCD: ' + (st.original_bytes||0).toLocaleString() + ' -> ' + (st.output_bytes||0).toLocaleString() + ' bytes');
      const inc = st.vbs_with_inconsistent_gle_comments || [];
      if (inc.length) {
        lines.push('<span style="color:var(--warn)">VBs com comments inconsistentes no GLE (usado o primeiro):</span> <code>'
          + inc.map(escHtml).join(', ') + '</code>');
      }
    }
    resultEl.innerHTML =
      '<div class="title">Arquivo gerado: <code>' + escHtml(data.output_name || '') + '</code></div>'
      + '<ul>' + lines.map(l => '<li>' + l + '</li>').join('') + '</ul>'
      + '<a class="download" href="' + escAttr(data.download_url || '#') + '" download>Baixar arquivo</a>'
      + SelLibrary.savedNote(data.project_file);
  }

  async function clickApply(btn) {
    const direction = btn.dataset.direction;
    if (!relay || !ied || !gle) {
      renderResult(direction, {error: 'parametros relay/ied/gle ausentes na URL'}, true);
      return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = 'Aplicando...';
    resultEl.style.display = 'none';
    try {
      const r = await SelProgress.post('/apply',
        { direction, relay, ied, gle },
        { label: 'Aplicando alteracoes...', doneLabel: 'Arquivo gerado.' });
      renderResult(direction, r.data || {error: 'resposta invalida'}, !r.ok);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }
  document.getElementById('apply-scd-to-gle').addEventListener('click',
    function() { clickApply(this); });
  document.getElementById('apply-gle-to-scd').addEventListener('click',
    function() { clickApply(this); });
})();
