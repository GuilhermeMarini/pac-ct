// Gerado por `npm run build` a partir de frontend/src/lib/progress.ts -- nao editar aqui.
var PacProgressRuntime = (function (exports) {
  'use strict';

  var BAR_ID = "selprog";
  var bar = null;
  var hideTimer = null;
  var poll = null;
  function ensure() {
    if (bar) return bar;
    var css = document.createElement("style");
    css.textContent = [
      "#selprog{position:fixed;top:0;left:0;right:0;z-index:99999;",
      "font:13px system-ui,-apple-system,Segoe UI,Roboto,sans-serif;",
      "background:#161b22;border-bottom:1px solid #30363d;color:#c9d1d9;",
      "transform:translateY(-100%);transition:transform .18s ease;}",
      "#selprog.on{transform:translateY(0);}",
      "#selprog .bar{height:3px;background:#21262d;overflow:hidden;}",
      "#selprog .fill{height:100%;width:0%;background:#2f81f7;",
      "transition:width .2s ease;}",
      "#selprog.err .fill{background:#f85149;}",
      "#selprog.ok .fill{background:#3fb950;}",
      "#selprog .txt{padding:6px 14px;display:flex;gap:10px;",
      "align-items:center;justify-content:space-between;}",
      "#selprog .pct{color:#8b949e;font-variant-numeric:tabular-nums;}",
      "#selprog.indet .fill{width:35%;animation:selprog-slide 1.1s infinite ease-in-out;}",
      "@keyframes selprog-slide{0%{margin-left:-35%}100%{margin-left:100%}}"
    ].join("");
    document.head.appendChild(css);
    var root = document.createElement("div");
    root.id = BAR_ID;
    root.innerHTML = '<div class="bar"><div class="fill"></div></div><div class="txt"><span class="msg"></span><span class="pct"></span></div>';
    document.body.appendChild(root);
    bar = {
      root,
      fill: root.querySelector(".fill"),
      label: root.querySelector(".msg"),
      pct: root.querySelector(".pct")
    };
    return bar;
  }
  function show() {
    var b = ensure();
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    b.root.classList.add("on");
    return b;
  }
  function render(pct, msg) {
    var b = ensure();
    if (msg != null) b.label.textContent = msg;
    if (pct == null) {
      b.root.classList.add("indet");
      b.pct.textContent = "";
    } else {
      b.root.classList.remove("indet");
      var p = Math.max(0, Math.min(100, pct));
      b.fill.style.width = p + "%";
      b.pct.textContent = Math.round(p) + "%";
    }
  }
  function stopPoll() {
    if (poll) {
      clearInterval(poll);
      poll = null;
    }
  }
  var API = {
    begin: function(msg) {
      var b = show();
      b.root.classList.remove("err", "ok");
      render(null, msg || "Processando...");
    },
    set: function(pct, msg) {
      var b = show();
      b.root.classList.remove("err", "ok");
      render(pct, msg);
    },
    done: function(msg) {
      if (!bar) return;
      const b = bar;
      stopPoll();
      b.root.classList.remove("indet", "err");
      b.root.classList.add("ok");
      render(100, msg || "Concluido.");
      hideTimer = setTimeout(function() {
        b.root.classList.remove("on");
      }, 1200);
    },
    fail: function(msg) {
      var b = show();
      stopPoll();
      b.root.classList.remove("indet", "ok");
      b.root.classList.add("err");
      render(100, msg || "Falhou.");
      hideTimer = setTimeout(function() {
        b.root.classList.remove("on");
      }, 5e3);
    },
    hide: function() {
      stopPoll();
      if (bar) bar.root.classList.remove("on");
    },
    newJobId: function() {
      return "j" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
    },
    /* Acompanha o lado servidor de um job ja em andamento.
     *
     * Fecha a barra sozinho quando o job termina. Antes so parava de
     * consultar, e quem escondia a barra era o codigo que chamou -- quem
     * usasse `track` sem esse cuidado ficava com a barra congelada no topo da
     * viewport, por cima do cabecalho da ferramenta.
     */
    track: function(jobId, opts) {
      opts = opts || {};
      var from = opts.from == null ? 0 : opts.from;
      var span = (opts.to == null ? 100 : opts.to) - from;
      var sawStage = false, vazios = 0;
      stopPoll();
      poll = setInterval(function() {
        fetch("/progress?job=" + encodeURIComponent(jobId), { cache: "no-store" }).then(function(r) {
          return r.ok ? r.json() : null;
        }).then(function(j) {
          if (!j) return;
          if (j.error) {
            API.fail(j.error);
            return;
          }
          if (j.stage) {
            sawStage = true;
            vazios = 0;
            render(j.pct == null ? null : from + span * (j.pct / 100), j.stage);
          } else if (!j.done && (sawStage || ++vazios > 8)) {
            API.hide();
            return;
          }
          if (j.done) API.done(j.stage || opts.doneLabel);
        }).catch(function() {
        });
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
    upload: function(url, body, opts) {
      opts = opts || {};
      var jobId = opts.jobId || API.newJobId();
      var uploadTo = opts.uploadTo == null ? 60 : opts.uploadTo;
      var b = show();
      b.root.classList.remove("err", "ok");
      render(0, opts.label || "Enviando...");
      return new Promise(function(resolve) {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.setRequestHeader("X-Job-Id", jobId);
        var h = opts.headers || {};
        Object.keys(h).forEach(function(k) {
          xhr.setRequestHeader(k, h[k]);
        });
        xhr.upload.onprogress = function(e) {
          if (!e.lengthComputable) {
            render(null, opts.label || "Enviando...");
            return;
          }
          var frac = e.loaded / e.total;
          render(
            frac * uploadTo,
            (opts.label || "Enviando") + " " + fmtBytes(e.loaded) + " / " + fmtBytes(e.total)
          );
        };
        xhr.upload.onload = function() {
          render(uploadTo, opts.serverLabel || "Processando no servidor...");
          API.track(jobId, { from: uploadTo, to: 100 });
        };
        xhr.onerror = function() {
          API.fail("Erro de rede no upload.");
          resolve({ ok: false, status: 0, data: { error: "erro de rede" } });
        };
        xhr.onload = function() {
          stopPoll();
          var data = null;
          try {
            data = JSON.parse(xhr.responseText);
          } catch (e) {
            data = null;
          }
          var ok = xhr.status >= 200 && xhr.status < 300;
          if (ok) API.done(opts.doneLabel || "Concluido.");
          else API.fail(data && data.error || "Falhou: HTTP " + xhr.status);
          resolve(ok ? { ok: true, status: xhr.status, data } : { ok: false, status: xhr.status, data });
        };
        xhr.send(body);
      });
    },
    /* POST sem corpo grande (export, diff, apply): sem bytes pra medir, mas o
     * servidor ainda reporta estagios. */
    post: function(url, payload, opts) {
      opts = opts || {};
      var jobId = opts.jobId || API.newJobId();
      API.begin(opts.label || "Processando...");
      API.track(jobId, { from: 0, to: 100 });
      var headers = Object.assign(
        { "Content-Type": "application/json", "X-Job-Id": jobId },
        opts.headers || {}
      );
      var body = payload instanceof Blob || typeof payload === "string" ? payload : JSON.stringify(payload);
      return fetch(url, { method: "POST", headers, body }).then(function(r) {
        return r.json().catch(function() {
          return null;
        }).then(function(d) {
          stopPoll();
          if (r.ok) API.done(opts.doneLabel || "Concluido.");
          else API.fail(d && d.error || "Falhou: HTTP " + r.status);
          return r.ok ? { ok: true, status: r.status, data: d } : { ok: false, status: r.status, data: d };
        });
      }).catch(function(e) {
        API.fail("Erro de rede: " + e);
        return { ok: false, status: 0, data: { error: String(e) } };
      });
    }
  };
  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }
  const progressApi = API;
  if (!window.SelProgress) window.SelProgress = progressApi;

  exports.progressApi = progressApi;

  Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

  return exports;

})({});
