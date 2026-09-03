"""The `SelLibrary` browser runtime, injected into every page.

Six tools need the same picker over the same list. Written once here and
injected the way `SelProgress` already is, a seventh tool gets it for free --
and the empty state, which has to link the tab with a RELATIVE href (a
cross-page link is one of the two things the `fetch` shim cannot reach), is
written once too.
"""

from __future__ import annotations

LIBRARY_JS = r"""<script>
(function () {
  if (window.SelLibrary) return;

  function fmtSize(n) {
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(0) + ' kB';
    return n + ' B';
  }

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // O recado de "isto entrou no acervo do projeto", escrito UMA vez aqui e
  // colado no bloco de resultado de cada ferramenta. `pf` e' o `project_file`
  // da resposta -- null quando a adocao falhou, e ai nao se promete nada: o
  // arquivo foi gerado do mesmo jeito e o link de download acima continua
  // valendo.
  function savedNote(pf) {
    if (!pf) return '';
    var verb = pf.duplicate ? 'já estava em' : 'guardado em';
    var usable = (pf.kind === 'rdb' || pf.kind === 'scd')
      ? ' — as outras ferramentas já podem escolhê-lo.' : '';
    // Href relativo: o shim de prefixo reescreve fetch/XHR, nunca um <a href>.
    return '<div class="lib-note"><code>' + esc(pf.name) + '</code> ' + verb +
           ' <a class="lnk" href="../files/">Arquivos do Projeto</a>' +
           usable + '</div>';
  }

  function list(kind) {
    var url = '/library' + (kind ? '?kind=' + encodeURIComponent(kind) : '');
    return fetch(url).then(function (r) { return r.json(); })
                     .then(function (d) { return (d && d.files) || []; });
  }

  // opts: {kind, multi, label, onPick, selected, annotate}
  // Uma LISTA de arquivos do projeto, escolhida no clique. `onPick` recebe uma
  // entrada, ou o array das marcadas quando `multi`.
  //
  // Era um <select> mais um botao "Usar", e o botao era cerimonia: por tras
  // dele so' havia o `POST /select-*`, que na maioria das ferramentas nao faz
  // mais que copiar um ponteiro do acervo pro estado da ferramenta. O usuario
  // via duas listas do mesmo arquivo -- as do projeto no seletor, as
  // "carregadas" ao lado -- e tinha que confirmar uma escolha que ja tinha
  // feito. Clicar na linha E' a escolha; quem tem trabalho de verdade atras
  // dela (ler um SCD, listar os IEDs) mostra isso no proprio status.
  //
  // `selected` (sha256 ou sha curto) marca uma linha ja escolhida; `annotate`
  // devolve um texto por linha pra ferramenta pendurar o que so' ela sabe
  // (quantas alteracoes pendentes, por exemplo).
  function picker(el, opts) {
    opts = opts || {};
    var node = (typeof el === 'string') ? document.getElementById(el) : el;
    if (!node) return {refresh: function () {}, select: function () {}};

    var chosen = {};        // sha256 -> true
    var cache = [];
    // Marcacao pedida antes de a lista chegar (a pagina restaura a escolha da
    // sessao anterior enquanto o `/library` ainda esta no ar). Fica guardada e
    // e' aplicada no proximo render, senao a linha certa aparece sem marca.
    var wanted = null;

    function entryByRef(ref) {
      for (var i = 0; i < cache.length; i++) {
        if (cache[i].sha256 === ref || cache[i].short_sha === ref) return cache[i];
      }
      return null;
    }

    function emit() {
      if (!opts.onPick) return;
      var picked = cache.filter(function (f) { return chosen[f.sha256]; });
      // Multi avisa sempre, inclusive com a lista vazia: desmarcar o ultimo
      // arquivo e' uma escolha tanto quanto marcar o primeiro, e a tela tem
      // que poder esvaziar o que mostrava por causa dele.
      if (opts.multi) opts.onPick(picked);
      else if (picked.length) opts.onPick(picked[0]);
    }

    function paint() {
      Array.prototype.forEach.call(node.querySelectorAll('.filerow'),
        function (row) {
          var on = !!chosen[row.dataset.sha];
          row.classList.toggle('sel', on);
          row.setAttribute('aria-selected', on ? 'true' : 'false');
        });
    }

    function render(files) {
      cache = files;
      node.innerHTML = '';
      var box = document.createElement('div');
      box.className = 'filelist';
      box.setAttribute('role', 'listbox');

      var cap = document.createElement('div');
      cap.className = 'lbl';
      cap.textContent = opts.label || 'Arquivo do projeto';
      box.appendChild(cap);

      if (!files.length) {
        var msg = document.createElement('div');
        msg.className = 'noitems';
        msg.textContent = 'Nenhum ' + (opts.kind || 'arquivo').toUpperCase() +
                          ' no projeto — envie em ';
        var a0 = document.createElement('a');
        a0.className = 'lnk';
        // Relative on purpose: the fetch shim rewrites fetch/XHR, never an
        // <a href>, so an absolute path would break under a mount prefix.
        a0.href = '../files/';
        a0.textContent = 'Arquivos do Projeto';
        msg.appendChild(a0);
        box.appendChild(msg);
        node.appendChild(box);
        return;
      }

      files.forEach(function (f) {
        var row = document.createElement('div');
        row.className = 'filerow';
        row.dataset.sha = f.sha256;
        row.setAttribute('role', 'option');
        row.title = f.short_sha;
        var extra = opts.annotate ? (opts.annotate(f) || '') : '';
        row.innerHTML =
          '<span class="name">' + esc(f.name) + '</span>' +
          '<span class="meta">' + esc(fmtSize(f.size)) +
            (f.detail ? ' · ' + esc(f.detail) : '') +
            (f.origin ? ' · gerado no ' + esc(f.origin) : '') + '</span>' +
          (extra ? '<span class="flag">' + esc(extra) + '</span>' : '') +
          '<span class="hash">' + esc(f.short_sha) + '</span>';
        row.addEventListener('click', function () {
          if (opts.multi) chosen[f.sha256] = !chosen[f.sha256];
          else chosen = (function (o) { o[f.sha256] = true; return o; })({});
          paint();
          emit();
        });
        box.appendChild(row);
      });

      var more = document.createElement('a');
      // Sem `lnk`: nos tres temas essa classe desenha um botao de cabecalho,
      // e um botao logo abaixo das linhas volta a parecer o "Usar" que saiu
      // daqui. Aqui e' um link de texto.
      more.className = 'more';
      more.href = '../files/';
      more.textContent = 'Arquivos do Projeto →';
      box.appendChild(more);

      node.appendChild(box);
      if (wanted) select(wanted);
      paint();
    }

    function select(ref) {
      if (!ref) return;
      var f = entryByRef(ref);
      if (!f) { wanted = ref; return; }
      wanted = null;
      if (!opts.multi) chosen = {};
      chosen[f.sha256] = true;
      paint();
    }

    function refresh() {
      return list(opts.kind).then(function (files) {
        render(files);
        if (opts.selected) select(opts.selected);
      });
    }
    refresh();
    return {refresh: refresh, select: select};
  }

  window.SelLibrary = {list: list, picker: picker, fmtSize: fmtSize,
                      savedNote: savedNote};
})();
</script>
"""


def inject_library_runtime(html: str) -> str:
    """Insert the runtime at the end of `<head>`.

    In the `<head>` and NOT before `</body>`, unlike `SelProgress`: every tool
    calls `SelLibrary.picker(...)` at the TOP LEVEL of its own `<script>`, so a
    runtime defined after that script has not been parsed yet when the call
    runs. The `ReferenceError` aborts the whole inline block, and the tool's
    page comes up blank -- not just the picker, everything the script was going
    to render. `SelProgress` gets away with the tail of the body because it is
    only ever touched from inside an event handler.

    It goes at the END of the head so the prefix shim (`inject_head`) is
    already in place; the runtime touches no DOM at definition time, so
    running it before `<body>` exists is safe.
    """
    idx = html.lower().rfind("</head>")
    if idx != -1:
        return html[:idx] + LIBRARY_JS + html[idx:]
    idx = html.rfind("</body>")
    if idx == -1:
        return html + LIBRARY_JS
    return html[:idx] + LIBRARY_JS + html[idx:]
