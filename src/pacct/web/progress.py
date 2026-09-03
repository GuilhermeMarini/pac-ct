"""Barra de progresso: registro de jobs no servidor + runtime no cliente.

O toolkit lida com RDBs de 40-140 MB. Antes, subir um arquivo desses era uma
tela parada com "Enviando RDB..." por dezenas de segundos -- sem saber se
estava andando, travado, ou perto do fim. Duas fases precisam de feedback, e
so uma delas o browser conhece sozinho:

1. **Upload** (browser -> servidor). O `fetch()` nao expoe progresso de envio;
   `XMLHttpRequest.upload.onprogress` expoe. Por isso o runtime do cliente usa
   XHR nos uploads -- e' a unica forma de ter bytes enviados / bytes totais.

2. **Processamento** (dentro do servidor). Depois que o corpo chega, ainda ha
   segundos de extracao do OLE, leitura de reles, diff, render de SVG. O
   browser nao tem como saber disso, entao o servidor publica o estagio num
   registro em memoria e o cliente consulta `/progress?job=<id>`.

O `job_id` e' gerado pelo cliente e vai no header `X-Job-Id` do POST. Como o
servidor e' `ThreadingHTTPServer`, o GET de progresso e' atendido em paralelo
enquanto o POST ainda esta rodando -- e' isso que faz a fase 2 funcionar sem
precisar de SSE ou WebSocket.

A rota `/progress` e' servida pelo dispatcher (`pacct.web.mount`), entao vale
pra todas as ferramentas montadas sem nenhuma delas precisar declarar a rota.
"""

from __future__ import annotations

import json
import threading
import time

# Um job morre depois desse tempo sem updates. Cobre o caso do cliente sumir
# no meio (aba fechada) sem deixar entrada presa pra sempre.
_JOB_TTL_SECONDS = 600

# Aceita so o formato que o cliente gera; evita usar texto arbitrario como
# chave de dicionario que cresce sem limite.
_MAX_JOB_ID = 64


class ProgressRegistry:
    """Estagio atual de cada job, por id. Escrito pelo handler, lido pelo GET."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def set(self, job_id: str, stage: str, pct: float | None = None,
            done: bool = False, error: str | None = None) -> None:
        """Publica o estagio atual. `pct` e' 0..100, ou None se indeterminado."""
        if not job_id or len(job_id) > _MAX_JOB_ID:
            return
        now = time.time()
        with self._lock:
            self._jobs[job_id] = {
                "stage": stage,
                "pct": pct,
                "done": done,
                "error": error,
                "ts": now,
            }
            # Aproveita a escrita pra podar jobs velhos -- nao precisa de
            # thread propria so pra isso.
            if len(self._jobs) > 32:
                cutoff = now - _JOB_TTL_SECONDS
                for k in [k for k, v in self._jobs.items() if v["ts"] < cutoff]:
                    self._jobs.pop(k, None)

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else {"stage": "", "pct": None,
                                          "done": False, "error": None}

    def finish(self, job_id: str, stage: str = "Pronto") -> None:
        self.set(job_id, stage, pct=100.0, done=True)

    def fail(self, job_id: str, error: str) -> None:
        self.set(job_id, "Falhou", pct=None, done=True, error=error)

    def drop(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


# Registro unico do processo. Os job ids sao aleatorios do cliente, entao nao
# ha necessidade de particionar por sessao.
REGISTRY = ProgressRegistry()


class JobReporter:
    """Acucar pra reportar estagios de um job dentro de um handler.

    Vira no-op quando o cliente nao mandou `X-Job-Id` (ex.: chamada via curl),
    entao o codigo do handler nao precisa checar nada.
    """

    __slots__ = ("job_id", "registry")

    def __init__(self, job_id: str | None, registry: ProgressRegistry = REGISTRY):
        self.job_id = job_id or ""
        self.registry = registry

    def __bool__(self) -> bool:
        return bool(self.job_id)

    def stage(self, text: str, pct: float | None = None) -> None:
        if self.job_id:
            self.registry.set(self.job_id, text, pct)

    def fraction(self, text: str, done: int, total: int) -> None:
        """Estagio com percentual derivado de `done/total`."""
        if self.job_id and total > 0:
            self.registry.set(self.job_id, text, 100.0 * done / total)

    def finish(self, text: str = "Pronto") -> None:
        if self.job_id:
            self.registry.finish(self.job_id, text)

    def fail(self, error: str) -> None:
        if self.job_id:
            self.registry.fail(self.job_id, error)


def progress_response(job_id: str) -> bytes:
    return json.dumps(REGISTRY.get(job_id)).encode("utf-8")


# -----------------------------------------------------------------------------
# Runtime do cliente
# -----------------------------------------------------------------------------

# Barra fixa no topo da viewport. Fica fora do fluxo do documento de proposito:
# as cinco ferramentas tem layouts diferentes, e assim nenhuma precisou abrir
# espaco pra ela.
#
# As duas fases dividem a barra: upload ocupa 0-60%, processamento no servidor
# 60-100%. Num RDB grande as duas levam tempo parecido, entao mapear o upload
# em 0-100% daria a impressao falsa de "terminou" com metade do trabalho ainda
# por fazer.
PROGRESS_JS = r"""<script>
(function () {
  if (window.SelProgress) return;

  var BAR_ID = 'selprog';
  var el = null, fill = null, label = null, hideTimer = null, poll = null;

  function ensure() {
    if (el) return;
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
    el = document.createElement('div');
    el.id = BAR_ID;
    el.innerHTML = '<div class="bar"><div class="fill"></div></div>' +
                   '<div class="txt"><span class="msg"></span>' +
                   '<span class="pct"></span></div>';
    document.body.appendChild(el);
    fill = el.querySelector('.fill');
    label = el.querySelector('.msg');
  }

  function show() {
    ensure();
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    el.classList.add('on');
  }

  function render(pct, msg) {
    ensure();
    if (msg != null) label.textContent = msg;
    if (pct == null) {
      el.classList.add('indet');
      el.querySelector('.pct').textContent = '';
    } else {
      el.classList.remove('indet');
      var p = Math.max(0, Math.min(100, pct));
      fill.style.width = p + '%';
      el.querySelector('.pct').textContent = Math.round(p) + '%';
    }
  }

  function stopPoll() {
    if (poll) { clearInterval(poll); poll = null; }
  }

  var API = {
    begin: function (msg) {
      show();
      el.classList.remove('err', 'ok');
      render(null, msg || 'Processando...');
    },
    set: function (pct, msg) { show(); el.classList.remove('err','ok'); render(pct, msg); },
    done: function (msg) {
      if (!el) return;
      stopPoll();
      el.classList.remove('indet', 'err');
      el.classList.add('ok');
      render(100, msg || 'Concluido.');
      hideTimer = setTimeout(function () { el.classList.remove('on'); }, 1200);
    },
    fail: function (msg) {
      show();
      stopPoll();
      el.classList.remove('indet', 'ok');
      el.classList.add('err');
      render(100, msg || 'Falhou.');
      hideTimer = setTimeout(function () { el.classList.remove('on'); }, 5000);
    },
    hide: function () { stopPoll(); if (el) el.classList.remove('on'); },

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
    track: function (jobId, opts) {
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
    upload: function (url, body, opts) {
      opts = opts || {};
      var jobId = opts.jobId || API.newJobId();
      var uploadTo = opts.uploadTo == null ? 60 : opts.uploadTo;
      show();
      el.classList.remove('err', 'ok');
      render(0, opts.label || 'Enviando...');

      return new Promise(function (resolve) {
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
          resolve({ok: ok, status: xhr.status, data: data});
        };
        xhr.send(body);
      });
    },

    /* POST sem corpo grande (export, diff, apply): sem bytes pra medir, mas o
     * servidor ainda reporta estagios. */
    post: function (url, payload, opts) {
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
          return r.json().catch(function () { return null; }).then(function (d) {
            stopPoll();
            if (r.ok) API.done(opts.doneLabel || 'Concluido.');
            else API.fail((d && d.error) || ('Falhou: HTTP ' + r.status));
            return {ok: r.ok, status: r.status, data: d};
          });
        })
        .catch(function (e) {
          API.fail('Erro de rede: ' + e);
          return {ok: false, status: 0, data: {error: String(e)}};
        });
    },
  };

  function fmtBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }

  window.SelProgress = API;
})();
</script>
"""


def inject_progress_runtime(html: str) -> str:
    """Insere o runtime da barra logo antes de `</body>` (ou no fim)."""
    idx = html.rfind("</body>")
    if idx == -1:
        return html + PROGRESS_JS
    return html[:idx] + PROGRESS_JS + html[idx:]
