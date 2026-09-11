// O que o navegador ganha em TODA pagina, injetado no fim do <head> por
// `pacct/library/client.py:inject_library_runtime`.
//
// Dois runtimes moram aqui:
//
//   PacPage     -- le o bloco <script type="application/json" id="page-data">,
//                  que e' como o servidor entrega dados a um arquivo .js
//                  externo. Substituir `${...}` no corpo de um <script> deixou
//                  de ser possivel quando o corpo saiu do .html.
//   SelLibrary  -- o seletor sobre o acervo de arquivos do projeto. Seis
//                  ferramentas precisam do mesmo seletor sobre a mesma lista,
//                  e o estado vazio, que tem que linkar a aba com um href
//                  RELATIVO (um link entre paginas e' uma das duas coisas que
//                  o shim de prefixo nao alcanca), e' escrito uma vez so.
//
// O prefixo `Sel` e' historico: veio de quando a aplicacao so' lia arquivos
// SEL. Ela le SCD (IEC 61850) ha' tempos, e `SelLibrary`/`SelProgress` sao os
// dois nomes que sobraram -- renomea-los mexe no topo do script de sete
// ferramentas e nao cabe nesta fase. `PacPage` ja' nasce com o prefixo que os
// substitui.

// -----------------------------------------------------------------------------
// PacPage -- the data the server left on this page.
// -----------------------------------------------------------------------------
(function () {
  if (window.PacPage) return;

  var cache = null;

  // The page's server data, as an object. `{}` when the page carries no
  // `page-data` block -- which is the normal case: only the screens that have
  // something to hand down emit one, and a tool that asks for a key it was
  // never given gets `undefined`, not an exception.
  //
  // A block that IS there and does not parse throws, naming the page. That
  // failure is otherwise invisible: the exception aborts the tool's whole
  // script and the screen comes up blank with a 200, which is exactly the
  // shape the browser check exists to catch.
  function data() {
    if (cache) return cache;
    var el = document.getElementById('page-data');
    if (!el) { cache = {}; return cache; }
    try {
      cache = JSON.parse(el.textContent) || {};
    } catch (e) {
      throw new Error('page-data invalido em ' + location.pathname + ': ' +
                      e.message);
    }
    return cache;
  }

  window.PacPage = {data: data};
})();

// -----------------------------------------------------------------------------
// SelLibrary -- o seletor sobre o acervo do projeto.
// -----------------------------------------------------------------------------
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
