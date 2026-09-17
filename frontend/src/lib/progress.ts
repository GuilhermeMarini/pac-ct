interface PacTrackOpts {
  from?: number;
  to?: number;
  doneLabel?: string;
}

interface PacUploadOpts {
  jobId?: string;
  uploadTo?: number;
  label?: string;
  serverLabel?: string;
  doneLabel?: string;
  headers?: Record<string, string>;
}

interface PacPostOpts {
  label?: string;
  doneLabel?: string;
  jobId?: string;
  headers?: Record<string, string>;
}

// O corpo que uma rota devolve quando falha. Nao e' invencao: este arquivo
// mesmo le `d.error` para montar a mensagem, e as rotas em Python escrevem a
// mesma forma -- `self._send_json(404, {"error": ...})`. `error` e' opcional
// porque este codigo ja aceita nao o encontrar.
export interface PacApiError {
  error?: string;
}

// O que `post` e `upload` resolvem, como uniao discriminada por `ok`.
//
// A discriminacao e' o que permite a quem chama ler `r.data.error` no ramo de
// falha e passar `r.data` adiante no de sucesso SEM asercao nenhuma nos dois
// -- e' o TypeScript estreitando exatamente onde o codigo ja estreitava. O
// preco esta' nos dois ternarios la' embaixo: `{ok: r.ok, ...}` com `ok`
// booleano nao pertence a nenhum dos dois membros, entao o objeto e'
// construido em cada ramo. Os valores sao os mesmos que sempre foram; o que
// mudou e' que agora o codigo DIZ o que ja fazia.
//
// `data` e' `T | null` nos dois: um corpo que nao e' JSON chega como null com
// qualquer status.
export type PacPostResult<T> =
  | {ok: true; status: number; data: T | null}
  | {ok: false; status: number; data: PacApiError | null};

var BAR_ID = 'selprog';
// As quatro pontas da barra, resolvidas UMA vez e guardadas juntas.
//
// Eram quatro variaveis soltas (`el`, `fill`, `label`, e um `querySelector`
// repetido para o `.pct`), todas comecando em null e preenchidas pelo
// `ensure()`. Sob `strictNullChecks` isso custava 16 erros -- o `ensure()`
// garante as quatro, mas essa garantia nao atravessa a fronteira de uma
// funcao, e fechar cada leitura com `!` seria afirmar dezesseis vezes o que
// se pode dizer uma. A regra ja' e' a convencao deste repositorio do lado
// Python: enunciar a invariante UMA vez (`require_session`, `require_rdb`)
// em vez de espalhar ramos que nunca se tomam.
//
// Entao `ensure()` DEVOLVE a barra, e quem tem o valor na mao nao precisa
// perguntar se ela existe. As unicas asercoes ficam no ponto de construcao,
// tres linhas abaixo do `innerHTML` que cria justamente esses elementos.
interface Bar {
  root: HTMLDivElement;
  fill: HTMLElement;
  label: HTMLElement;
  pct: HTMLElement;
}

var bar: Bar | null = null;
var hideTimer: ReturnType<typeof setTimeout> | null = null;
var poll: ReturnType<typeof setInterval> | null = null;

function ensure(): Bar {
  if (bar) return bar;
  var css = document.createElement('style');
  css.textContent = [
    '#selprog{position:fixed;top:0;left:0;right:0;z-index:99999;',
    'font:13px system-ui,-apple-system,Segoe UI,Roboto,sans-serif;',
    'background:#161b22;border-bottom:1px solid #30363d;color:#c9d1d9;',
    'transform:translateY(-100%);transition:transform .18s ease;}',
    '#selprog.on{transform:translateY(0);}',
    '#selprog .bar{height:3px;background:#21262d;overflow:hidden;}',
    '#selprog .fill{height:100%;width:0%;background:#2f81f7;',
    'transition:width .2s ease;}',
    '#selprog.err .fill{background:#f85149;}',
    '#selprog.ok .fill{background:#3fb950;}',
    '#selprog .txt{padding:6px 14px;display:flex;gap:10px;',
    'align-items:center;justify-content:space-between;}',
    '#selprog .pct{color:#8b949e;font-variant-numeric:tabular-nums;}',
    '#selprog.indet .fill{width:35%;animation:selprog-slide 1.1s infinite ease-in-out;}',
    '@keyframes selprog-slide{0%{margin-left:-35%}100%{margin-left:100%}}',
  ].join('');
  document.head.appendChild(css);
  var root = document.createElement('div');
  root.id = BAR_ID;
  root.innerHTML = '<div class="bar"><div class="fill"></div></div>' +
                   '<div class="txt"><span class="msg"></span>' +
                   '<span class="pct"></span></div>';
  document.body.appendChild(root);
  // Os tres `!` sao a invariante inteira, e o que os justifica esta no
  // `innerHTML` logo acima: os elementos acabaram de ser escritos por este
  // codigo, nesta ordem. O `.pct` entra aqui em vez de ser consultado a cada
  // `render()` -- mesmo elemento, resolvido uma vez, e nada nunca o troca.
  bar = {
    root: root,
    fill: root.querySelector<HTMLElement>('.fill')!,
    label: root.querySelector<HTMLElement>('.msg')!,
    pct: root.querySelector<HTMLElement>('.pct')!,
  };
  return bar;
}

function show(): Bar {
  var b = ensure();
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  b.root.classList.add('on');
  return b;
}

function render(pct: number | null, msg?: string | null) {
  var b = ensure();
  if (msg != null) b.label.textContent = msg;
  if (pct == null) {
    b.root.classList.add('indet');
    b.pct.textContent = '';
  } else {
    b.root.classList.remove('indet');
    var p = Math.max(0, Math.min(100, pct));
    b.fill.style.width = p + '%';
    b.pct.textContent = Math.round(p) + '%';
  }
}

function stopPoll() {
  if (poll) { clearInterval(poll); poll = null; }
}

var API = {
  begin: function (msg?: string) {
    var b = show();
    b.root.classList.remove('err', 'ok');
    render(null, msg || 'Processando...');
  },
  set: function (pct: number | null, msg?: string) { var b = show(); b.root.classList.remove('err','ok'); render(pct, msg); },
  done: function (msg?: string) {
    // Este `if` continua sendo um ramo de verdade, e nao some com a
    // invariante: ele pergunta se a barra chegou a ser CONSTRUIDA -- um
    // `done()` sem nenhum `begin()` antes nao deve criar barra nenhuma so'
    // para escondê-la em seguida. O `const` e' o que leva o estreitamento
    // para dentro do `setTimeout` abaixo.
    if (!bar) return;
    const b = bar;
    stopPoll();
    b.root.classList.remove('indet', 'err');
    b.root.classList.add('ok');
    render(100, msg || 'Concluido.');
    hideTimer = setTimeout(function () { b.root.classList.remove('on'); }, 1200);
  },
  fail: function (msg?: string) {
    var b = show();
    stopPoll();
    b.root.classList.remove('indet', 'ok');
    b.root.classList.add('err');
    render(100, msg || 'Falhou.');
    hideTimer = setTimeout(function () { b.root.classList.remove('on'); }, 5000);
  },
  hide: function () { stopPoll(); if (bar) bar.root.classList.remove('on'); },

  newJobId: function () {
    return 'j' + Math.random().toString(36).slice(2, 10) +
           Date.now().toString(36).slice(-4);
  },

  /* Acompanha o lado servidor de um job ja em andamento.
   *
   * Fecha a barra sozinho quando o job termina. Antes so parava de
   * consultar, e quem escondia a barra era o codigo que chamou -- quem
   * usasse `track` sem esse cuidado ficava com a barra congelada no topo da
   * viewport, por cima do cabecalho da ferramenta.
   */
  track: function (jobId: string, opts?: PacTrackOpts) {
    opts = opts || {};
    var from = opts.from == null ? 0 : opts.from;
    var span = (opts.to == null ? 100 : opts.to) - from;
    var sawStage = false, vazios = 0;
    stopPoll();
    poll = setInterval(function () {
      fetch('/progress?job=' + encodeURIComponent(jobId), {cache: 'no-store'})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j) return;
          if (j.error) { API.fail(j.error); return; }
          if (j.stage) {
            sawStage = true;
            vazios = 0;
            render(j.pct == null ? null : from + span * (j.pct / 100), j.stage);
          } else if (!j.done && (sawStage || ++vazios > 8)) {
            // Job desconhecido: ou saiu do registro (REGISTRY.drop) enquanto
            // olhavamos, ou nunca chegou a existir. Ninguem vai reportar
            // nada, entao a barra some em vez de ficar pendurada no topo.
            // A carencia (~3s) cobre so a janela entre o `begin()` do
            // cliente e o primeiro `stage()` do servidor.
            API.hide();
            return;
          }
          if (j.done) API.done(j.stage || opts.doneLabel);
        })
        .catch(function () { /* servidor ocupado; tenta de novo */ });
    }, 400);
  },

  /* Upload com progresso real de bytes.
   *
   * Usa XHR porque `fetch` nao reporta progresso de ENVIO -- e' justamente
   * o que interessa num RDB de 140 MB. O `open()` do XHR ja e' remendado
   * pelo shim de prefixo, entao a URL absoluta chega prefixada.
   *
   * Devolve Promise<{ok, status, data}>; nunca rejeita por status HTTP.
   */
  upload: function <T = unknown>(url: string, body: XMLHttpRequestBodyInit,
                                 opts?: PacUploadOpts): Promise<PacPostResult<T>> {
    opts = opts || {};
    var jobId = opts.jobId || API.newJobId();
    var uploadTo = opts.uploadTo == null ? 60 : opts.uploadTo;
    var b = show();
    b.root.classList.remove('err', 'ok');
    render(0, opts.label || 'Enviando...');

    return new Promise<PacPostResult<T>>(function (resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-Job-Id', jobId);
      var h = opts.headers || {};
      Object.keys(h).forEach(function (k) { xhr.setRequestHeader(k, h[k]); });

      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) { render(null, opts.label || 'Enviando...'); return; }
        var frac = e.loaded / e.total;
        render(frac * uploadTo,
               (opts.label || 'Enviando') + ' ' + fmtBytes(e.loaded) +
               ' / ' + fmtBytes(e.total));
      };
      // Corpo entregue: daqui em diante quem sabe do andamento e' o servidor.
      xhr.upload.onload = function () {
        render(uploadTo, opts.serverLabel || 'Processando no servidor...');
        API.track(jobId, {from: uploadTo, to: 100});
      };
      xhr.onerror = function () {
        API.fail('Erro de rede no upload.');
        resolve({ok: false, status: 0, data: {error: 'erro de rede'}});
      };
      xhr.onload = function () {
        stopPoll();
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        var ok = xhr.status >= 200 && xhr.status < 300;
        if (ok) API.done(opts.doneLabel || 'Concluido.');
        else API.fail((data && data.error) || ('Falhou: HTTP ' + xhr.status));
        // Um dos dois ternarios: mesmo objeto, construido no ramo que o
        // descreve. Ver `PacPostResult` acima.
        resolve(ok ? {ok: true, status: xhr.status, data: data}
                   : {ok: false, status: xhr.status, data: data});
      };
      xhr.send(body);
    });
  },

  /* POST sem corpo grande (export, diff, apply): sem bytes pra medir, mas o
   * servidor ainda reporta estagios. */
  post: function <T = unknown>(url: string, payload?: unknown,
                               opts?: PacPostOpts): Promise<PacPostResult<T>> {
    opts = opts || {};
    var jobId = opts.jobId || API.newJobId();
    API.begin(opts.label || 'Processando...');
    API.track(jobId, {from: 0, to: 100});
    var headers = Object.assign(
      {'Content-Type': 'application/json', 'X-Job-Id': jobId},
      opts.headers || {});
    var body = payload instanceof Blob || typeof payload === 'string'
      ? payload : JSON.stringify(payload);
    return fetch(url, {method: 'POST', headers: headers, body: body})
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (d): PacPostResult<T> {
          stopPoll();
          if (r.ok) API.done(opts.doneLabel || 'Concluido.');
          else API.fail((d && d.error) || ('Falhou: HTTP ' + r.status));
          // O outro ternario.
          return r.ok ? {ok: true, status: r.status, data: d}
                      : {ok: false, status: r.status, data: d};
        });
      })
      .catch(function (e) {
        API.fail('Erro de rede: ' + e);
        return {ok: false, status: 0, data: {error: String(e)}};
      });
  },
};

function fmtBytes(n: number) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

export const progressApi = API;
export type SelProgressApi = typeof progressApi;

if (!window.SelProgress) window.SelProgress = progressApi;
