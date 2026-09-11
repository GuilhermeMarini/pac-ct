// A tela de Arquivos do Projeto (`/files/`): envio, listagem, baixar, remover.
//
// Carregado por `<script src>` no mesmo ponto em que o corpo deste arquivo
// ficava embutido -- logo antes de `</body>`, sem `defer`. A posicao importa:
// `inject_progress_runtime` enfia o `SelProgress` ali tambem, DEPOIS deste
// script, e por isso nada aqui pode tocar em `SelProgress` no nivel de cima --
// so' de dentro de um handler, que e' quando ele ja' existe.
//
// `PacPage` e `SelLibrary`, ao contrario, vem no fim do `<head>` e ja' estao
// prontos quando esta primeira linha roda.

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind) {
  const el = $('status');
  el.textContent = msg || '';
  el.className = kind || '';
}

function fmtSize(n) {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + ' kB';
  return n + ' B';
}

function escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Dentro de um atributo entre aspas, escHtml nao basta: um nome de arquivo com
// aspas fecharia o atributo.
function escAttr(s) { return escHtml(s).replace(/"/g, '&quot;'); }

let _rows = [];

function render(highlightSha) {
  const tb = $('rows');
  tb.innerHTML = '';
  $('empty').style.display = _rows.length ? 'none' : '';
  _rows.forEach((f) => {
    const tr = document.createElement('tr');
    if (f.sha256 === highlightSha) tr.className = 'dup';
    tr.innerHTML =
      '<td>' + escHtml(f.name) + '</td>' +
      '<td>' + f.kind.toUpperCase() + '</td>' +
      '<td class="num">' + fmtSize(f.size) + '</td>' +
      '<td>' + escHtml(f.detail) +
        (f.origin ? ' <span class="tag-gen">gerado no ' + escHtml(f.origin) +
                    '</span>' : '') + '</td>' +
      '<td class="hash">' + f.short_sha + '</td>' +
      // Baixar e' um <a download>, e nao um fetch: e' o navegador que tem que
      // salvar o arquivo, e um RDB de 140 MB nao passa por Blob na memoria da
      // pagina. Href RELATIVO -- o shim de prefixo reescreve fetch/XHR, nunca
      // um <a href>, entao "/download" absoluto quebraria sob o prefixo.
      '<td class="acts">' +
        '<a class="btn" href="./download?sha256=' + encodeURIComponent(f.sha256) +
        '" download="' + escAttr(f.name) + '">Baixar</a> ' +
        '<button class="btn" type="button">Remover</button>' +
      '</td>';
    tr.querySelector('button').addEventListener('click', () => remove(f));
    tb.appendChild(tr);
  });
}

async function load(highlightSha) {
  const r = await fetch('/library');
  const d = await r.json();
  _rows = d.files || [];
  render(highlightSha);
}

async function remove(f) {
  if (!confirm('Remover "' + f.name + '" do projeto?')) return;
  const r = await SelProgress.post('/remove', {sha256: f.sha256},
                                   {label: 'Removendo'});
  if (!r.ok) {
    setStatus('Falha ao remover: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus('"' + f.name + '" removido do projeto.', 'ok');
  load();
}

// A tabela de extensoes vem do servidor, pelo `page-data`: e' o `EXTENSIONS`
// de `library.py`, o mesmo dicionario que `library.kind_for` consulta. Estava
// copiado aqui, com um comentario dizendo que espelhava aquele -- e uma copia
// que se sabe copia e' uma copia que ainda nao divergiu. Serve pra recusar na
// hora o que o servidor recusaria depois; devolve null pro que nao e' arquivo
// de projeto. As chaves vem COM o ponto, como no Python.
const KINDS = PacPage.data().kinds || {};
function kindFor(name) {
  const i = String(name || '').lastIndexOf('.');
  if (i < 0) return null;
  return KINDS[name.slice(i).toLowerCase()] || null;
}

// Um arquivo. Devolve o que aconteceu, pra quem chamou somar o resumo.
// `prefix` numera o arquivo dentro do lote ("2/5 ").
async function uploadOne(file, prefix) {
  setStatus(prefix + 'Enviando ' + file.name + '...', '');
  // SelProgress.upload, never fetch: only XMLHttpRequest.upload.onprogress
  // can report progress, and an RDB is 40-140 MB.
  const r = await SelProgress.upload('/upload', file, {
    headers: {'X-Filename': encodeURIComponent(file.name)},
    label: prefix + 'Enviando ' + file.name,
    doneLabel: 'Arquivo carregado.',
  });
  const d = r.data || {};
  const e = d.entry || {};
  if (!r.ok) return {ok: false, name: file.name, error: (d.error || r.status)};
  return {ok: true, name: e.name || file.name, sha256: e.sha256,
          duplicate: !!d.duplicate};
}

// Vários arquivos, um de cada vez. Sequencial de propósito: dois RDBs de
// 140 MB em paralelo brigam pela banda da rede da subestação e pelo mesmo
// job da barra de progresso, e o servidor extrai um RDB por vez de qualquer
// jeito. O laço não para no primeiro erro -- um .scd inválido no meio da
// seleção não pode impedir os outros de entrarem no projeto.
async function upload(files) {
  const picked = Array.prototype.slice.call(files || []).filter(Boolean);
  if (!picked.length) return;
  if (busy) return;

  // Sem `accept` na janela do sistema, o usuario pode escolher qualquer
  // coisa. O que nao e' arquivo de projeto nao vira requisicao -- so entra no
  // resumo, pra nada sumir calado.
  const list = [], rejected = [];
  picked.forEach((f) => (kindFor(f.name) ? list : rejected).push(f));
  if (!list.length) {
    setStatus('Tipo não reconhecido — envie .rdb, .scd ou .xml: ' +
              rejected.map((f) => f.name).join('; ') + '.', 'err');
    rearmInput();   // nada subiu, mas a escolha errada nao pode ficar presa
    return;
  }

  busy = true;
  drop.classList.add('busy');
  const done = [], dup = [], failed = [];
  try {
    for (let i = 0; i < list.length; i++) {
      const prefix = list.length > 1 ? (i + 1) + '/' + list.length + ' ' : '';
      const r = await uploadOne(list[i], prefix);
      if (!r.ok) failed.push(r.name + ' (' + r.error + ')');
      else if (r.duplicate) dup.push(r.name);
      else done.push(r);
    }
  } finally {
    busy = false;
    drop.classList.remove('busy');
    rearmInput();
  }

  const parts = [];
  if (done.length === 1) parts.push('"' + done[0].name + '" adicionado ao projeto.');
  else if (done.length) parts.push(done.length + ' arquivos adicionados ao projeto.');
  if (dup.length === 1) parts.push('"' + dup[0] + '" já estava no projeto.');
  else if (dup.length) parts.push(dup.length + ' já estavam no projeto.');
  if (failed.length) parts.push('Falhou: ' + failed.join('; ') + '.');
  if (rejected.length) parts.push('Ignorado (não é .rdb/.scd/.xml): ' +
                                  rejected.map((f) => f.name).join('; ') + '.');

  setStatus(parts.join(' '),
            (failed.length || rejected.length) ? 'err'
                                               : (done.length ? 'ok' : 'warn'));
  // Destaca a linha nova quando foi um só; num lote, destacar uma escolhida a
  // esmo confunde mais do que ajuda.
  load(done.length === 1 ? done[0].sha256 : undefined);
}

const drop = $('drop');
let input = $('file');
let busy = false;

function onPick(e) { upload(e.target.files); }

// Troca o <input> por um irmao vazio depois de cada envio.
//
// `input.value = ''` NAO resolve, e era o que estava aqui. O elemento guarda
// a selecao anterior, e a janela do sistema reabre em cima dela: no segundo
// envio ela voltava com o arquivo da vez passada ainda selecionado, e o
// "Abrir" so acendia pra ESSE arquivo -- clicar noutro .scd nao trocava a
// selecao e o botao ficava cinza. Um elemento recem-criado nao tem passado,
// entao a janela abre limpa toda vez.
//
// Vem no `finally` do lote, e nao no handler de `change`: mexer no input
// enquanto o `change` dele ainda esta na pilha e' justamente o que sujava o
// estado. Aqui o envio ja acabou (ou falhou) e ninguem mais olha pro
// elemento antigo.
function rearmInput() {
  const fresh = input.cloneNode(false);
  fresh.value = '';
  input.replaceWith(fresh);
  input = fresh;
  input.addEventListener('change', onPick);
}

['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); e.stopPropagation(); drop.classList.add('drag');
}));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); e.stopPropagation(); drop.classList.remove('drag');
}));
drop.addEventListener('drop', (e) => upload(e.dataTransfer.files));
input.addEventListener('change', onPick);

load();
