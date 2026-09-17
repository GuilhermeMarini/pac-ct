// Gerado por `npm run build` a partir de frontend/src/lib/file-picker.ts -- nao editar aqui.
(function () {
  'use strict';

  var cache = null;
  function data() {
    if (cache) return cache;
    var el = document.getElementById("page-data");
    if (!el) {
      cache = {};
      return cache;
    }
    try {
      cache = JSON.parse(String(el.textContent)) || {};
    } catch (e) {
      var msg = e instanceof Error ? e.message : String(e);
      throw new Error("page-data invalido em " + location.pathname + ": " + msg);
    }
    return cache;
  }
  const pageApi = { data };
  if (!window.PacPage) window.PacPage = pageApi;

  function fmtSize(n) {
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(0) + " kB";
    return n + " B";
  }
  function esc(s) {
    return (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function savedNote(pf) {
    if (!pf) return "";
    var verb = pf.duplicate ? "já estava em" : "guardado em";
    var usable = pf.kind === "rdb" || pf.kind === "scd" ? " — as outras ferramentas já podem escolhê-lo." : "";
    return '<div class="lib-note"><code>' + esc(pf.name) + "</code> " + verb + ' <a class="lnk" href="../files/">Arquivos do Projeto</a>' + usable + "</div>";
  }
  function list(kind) {
    var url = "/library" + (kind ? "?kind=" + encodeURIComponent(kind) : "");
    return fetch(url).then(function(r) {
      return r.json();
    }).then(function(d) {
      return d && d.files || [];
    });
  }
  function picker(el, optsIn) {
    const opts = optsIn || {};
    const found = typeof el === "string" ? document.getElementById(el) : el;
    if (!found) return { refresh: function() {
    }, select: function() {
    } };
    const node = found;
    var chosen = {};
    var cache = [];
    var wanted = null;
    function entryByRef(ref) {
      for (var i = 0; i < cache.length; i++) {
        if (cache[i].sha256 === ref || cache[i].short_sha === ref) return cache[i];
      }
      return null;
    }
    function emit() {
      if (!opts.onPick) return;
      var picked = cache.filter(function(f) {
        return chosen[f.sha256];
      });
      if (opts.multi) opts.onPick(picked);
      else if (picked.length) opts.onPick(picked[0]);
    }
    function paint() {
      Array.prototype.forEach.call(
        node.querySelectorAll(".filerow"),
        function(row) {
          var on = !!chosen[row.dataset.sha];
          row.classList.toggle("sel", on);
          row.setAttribute("aria-selected", on ? "true" : "false");
        }
      );
    }
    function render(files) {
      cache = files;
      node.innerHTML = "";
      var box = document.createElement("div");
      box.className = "filelist";
      box.setAttribute("role", "listbox");
      var cap = document.createElement("div");
      cap.className = "lbl";
      cap.textContent = opts.label || "Arquivo do projeto";
      box.appendChild(cap);
      if (!files.length) {
        var msg = document.createElement("div");
        msg.className = "noitems";
        msg.textContent = "Nenhum " + (opts.kind || "arquivo").toUpperCase() + " no projeto — envie em ";
        var a0 = document.createElement("a");
        a0.className = "lnk";
        a0.href = "../files/";
        a0.textContent = "Arquivos do Projeto";
        msg.appendChild(a0);
        box.appendChild(msg);
        node.appendChild(box);
        return;
      }
      files.forEach(function(f) {
        var row = document.createElement("div");
        row.className = "filerow";
        row.dataset.sha = f.sha256;
        row.setAttribute("role", "option");
        row.title = f.short_sha;
        var extra = opts.annotate ? opts.annotate(f) || "" : "";
        row.innerHTML = '<span class="name">' + esc(f.name) + '</span><span class="meta">' + esc(fmtSize(f.size)) + (f.detail ? " · " + esc(f.detail) : "") + (f.origin ? " · gerado no " + esc(f.origin) : "") + "</span>" + (extra ? '<span class="flag">' + esc(extra) + "</span>" : "") + '<span class="hash">' + esc(f.short_sha) + "</span>";
        row.addEventListener("click", function() {
          if (opts.multi) chosen[f.sha256] = !chosen[f.sha256];
          else chosen = (function(o) {
            o[f.sha256] = true;
            return o;
          })({});
          paint();
          emit();
        });
        box.appendChild(row);
      });
      var more = document.createElement("a");
      more.className = "more";
      more.href = "../files/";
      more.textContent = "Arquivos do Projeto →";
      box.appendChild(more);
      node.appendChild(box);
      if (wanted) select(wanted);
      paint();
    }
    function select(ref) {
      if (!ref) return;
      var f = entryByRef(ref);
      if (!f) {
        wanted = ref;
        return;
      }
      wanted = null;
      if (!opts.multi) chosen = {};
      chosen[f.sha256] = true;
      paint();
    }
    function refresh() {
      return list(opts.kind).then(function(files) {
        render(files);
        if (opts.selected) select(opts.selected);
      });
    }
    refresh();
    return { refresh, select };
  }
  const libraryApi = {
    list,
    picker,
    fmtSize,
    savedNote
  };
  if (!window.SelLibrary) window.SelLibrary = libraryApi;

})();
