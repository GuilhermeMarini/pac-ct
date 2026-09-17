// -----------------------------------------------------------------------------
// SelLibrary -- o seletor sobre o acervo do projeto.
// -----------------------------------------------------------------------------
//
// Modulo em vez de IIFE, pelo mesmo motivo do `page.ts` ao lado: o bundle
// continua sendo um `iife`, o escopo continua privado, e o `export type` no
// fim e' o que faz o `globals.d.ts` derivar o tipo do global DESTE codigo.
//
// Os tipos abaixo sao a outra metade da mesma ideia. Eles nasceram escritos a
// mao no `globals.d.ts` do B16, descrevendo de fora um runtime que ninguem
// conferia; agora moram junto da implementacao que os cumpre, e o
// `globals.d.ts` os importa em vez de repeti-los.

// Uma entrada do acervo do projeto, exatamente como o `FileEntry.to_json()` do
// `library/model.py` a serializa -- nove campos, nenhum opcional. `detail` e
// `origin` chegam como `""` quando vazios, nunca como null.
export interface PacLibraryFile {
  sha256: string;
  short_sha: string;
  kind: string;
  name: string;
  size: number;
  uploaded_at: number;
  detail: string;
  origin: string;
  generated: boolean;
}

// O `project_file` de uma resposta de ferramenta: a mesma entrada mais o
// `duplicate` que o `SessionHandler.publish_output` pendura nela.
export interface PacSavedFile extends PacLibraryFile {
  duplicate: boolean;
}

interface PacPickerBase {
  kind?: string;
  label?: string;
  selected?: string;
  annotate?: (f: PacLibraryFile) => string;
}

// `multi` muda O QUE o `onPick` recebe, e por isso as opcoes sao uma uniao
// discriminada em vez de um objeto so': no modo normal chega a entrada
// escolhida, no modo `multi` chega o array das marcadas. Escritas como um
// objeto unico, o `onPick` de toda ferramenta teria que aceitar os dois, e
// nenhuma delas aceita. A discriminacao tambem e' o que faz o `emit()` la'
// embaixo conferir sozinho, um ramo do `if (opts.multi)` de cada vez.
export interface PacPickerSingle extends PacPickerBase {
  multi?: false;
  onPick?: (f: PacLibraryFile) => void;
}

export interface PacPickerMulti extends PacPickerBase {
  multi: true;
  onPick?: (fs: PacLibraryFile[]) => void;
}

export type PacPickerOpts = PacPickerSingle | PacPickerMulti;

export interface PacPickerHandle {
  refresh: () => void | Promise<void>;
  select: (ref: string) => void;
}
function fmtSize(n: number) {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + ' kB';
  return n + ' B';
}

function esc(s: unknown) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// O recado de "isto entrou no acervo do projeto", escrito UMA vez aqui e
// colado no bloco de resultado de cada ferramenta. `pf` e' o `project_file`
// da resposta -- null quando a adocao falhou, e ai nao se promete nada: o
// arquivo foi gerado do mesmo jeito e o link de download acima continua
// valendo.
function savedNote(pf: PacSavedFile | null) {
  if (!pf) return '';
  var verb = pf.duplicate ? 'já estava em' : 'guardado em';
  var usable = (pf.kind === 'rdb' || pf.kind === 'scd')
    ? ' — as outras ferramentas já podem escolhê-lo.' : '';
  // Href relativo: o shim de prefixo reescreve fetch/XHR, nunca um <a href>.
  return '<div class="lib-note"><code>' + esc(pf.name) + '</code> ' + verb +
         ' <a class="lnk" href="../files/">Arquivos do Projeto</a>' +
         usable + '</div>';
}

function list(kind?: string): Promise<PacLibraryFile[]> {
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
// As duas assinaturas existem por causa do ponto de chamada, e isso foi
// medido: com a uniao sozinha, o `onPick: (f) => selectScd(f)` do Mapeador de
// VLAN perdeu o tipo do `f` (`TS7006`) -- o TypeScript nao escolhe um membro da
// uniao enquanto tipa o argumento por contexto. Com as sobrecargas ele escolhe
// pela presenca de `multi`, e o `f` volta a ser `PacLibraryFile` sem que
// ferramenta nenhuma precise escrever `multi: false`.
function picker(el: string | HTMLElement, opts?: PacPickerSingle): PacPickerHandle;
function picker(el: string | HTMLElement, opts: PacPickerMulti): PacPickerHandle;
function picker(el: string | HTMLElement, optsIn?: PacPickerOpts): PacPickerHandle {
  // `optsIn` existe para que `opts` possa ser const, e isso nao e' estilo: um
  // PARAMETRO volta a ser `PacPickerOpts | undefined` dentro de cada closure
  // aqui embaixo, onde o TypeScript descarta o estreitamento que o
  // `opts = opts || {}` daria. Mesmo valor, mesma ordem, mesmo runtime.
  const opts: PacPickerOpts = optsIn || {};
  const found = (typeof el === 'string') ? document.getElementById(el) : el;
  if (!found) return {refresh: function () {}, select: function () {}};
  // Const pelo mesmo motivo do `opts`: o `if (!found)` acima ja' garante que
  // nao e' null, mas num `var` esse estreitamento se perde dentro de cada
  // closure abaixo -- `paint`, `render` e `refresh` voltariam a ver
  // `HTMLElement | null`.
  const node = found;

  var chosen: Record<string, boolean> = {};   // sha256 -> true
  var cache: PacLibraryFile[] = [];
  // Marcacao pedida antes de a lista chegar (a pagina restaura a escolha da
  // sessao anterior enquanto o `/library` ainda esta no ar). Fica guardada e
  // e' aplicada no proximo render, senao a linha certa aparece sem marca.
  var wanted: string | null = null;

  function entryByRef(ref: string) {
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

  function render(files: PacLibraryFile[]) {
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
        else chosen = (function (o: Record<string, boolean>) { o[f.sha256] = true; return o; })({});
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

  function select(ref: string) {
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

export const libraryApi = {list: list, picker: picker, fmtSize: fmtSize,
                          savedNote: savedNote};
export type SelLibraryApi = typeof libraryApi;

// O mesmo guarda de antes (`if (window.SelLibrary) return;`), agora protegendo
// a atribuicao: uma segunda injecao na mesma pagina nao redefine o runtime.
if (!window.SelLibrary) window.SelLibrary = libraryApi;
