// Gerado por `npm run build` a partir de frontend/src/vlan_mapper/landing.ts -- nao editar aqui.
(function () {
  'use strict';

  const statusEl = document.getElementById("status");
  function setStatus(msg, kind) {
    statusEl.textContent = msg || "";
    statusEl.className = kind || "";
  }
  function escHtml(s) {
    return (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escAttr(s) {
    return (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }
  SelLibrary.picker("pick-scd", {
    kind: "scd",
    label: "SCD do projeto",
    onPick: (f) => selectScd(f)
  });
  async function selectScd(f) {
    setStatus("Lendo " + f.name + "...", "");
    const r = await SelProgress.post(
      "/select-scd",
      { sha256: f.sha256 },
      { label: "Lendo " + f.name }
    );
    if (!r.ok) {
      setStatus("Falha: " + (r.data && r.data.error || r.status), "err");
      return;
    }
    setStatus(f.name + " carregado.", "ok");
    render(r.data);
  }
  let _lastData = null;
  let _vlanFmt = "hex";
  try {
    _vlanFmt = localStorage.getItem("vlan-mapper-fmt") || "hex";
  } catch (e) {
  }
  let _displayMode = "chips";
  try {
    _displayMode = localStorage.getItem("vlan-mapper-mode") || "chips";
  } catch (e) {
  }
  function vlanFmt(v) {
    if (v == null) return "";
    const s = String(v).trim();
    if (_vlanFmt === "dec") {
      const n = parseInt(s, 16);
      if (!isNaN(n)) return String(n);
    }
    return s.toUpperCase();
  }
  function vlanSortKey(v) {
    const s = String(v).trim();
    const h = parseInt(s, 16);
    if (!isNaN(h)) return h;
    return Number.MAX_SAFE_INTEGER;
  }
  function render(data) {
    _lastData = data;
    const results = document.getElementById("results");
    if (!data || !data.has_scd) {
      results.style.display = "none";
      return;
    }
    results.style.display = "block";
    renderSummary();
    renderTable();
  }
  function renderSummary() {
    const data = _lastData;
    const summary = document.getElementById("summary");
    if (!data) {
      summary.innerHTML = "";
      return;
    }
    summary.innerHTML = '<div class="stat"><strong>' + data.ied_count + '</strong> IED(s)</div><div class="stat"><strong>' + data.vlan_count + "</strong> VLAN(s) distintos</div>" + (data.all_vlans && data.all_vlans.length ? '<div class="stat">Todos: ' + data.all_vlans.map((v) => "<code>" + escHtml(vlanFmt(v)) + "</code>").join(" ") + "</div>" : "");
  }
  function renderTable() {
    const data = _lastData;
    const tw = document.getElementById("table-wrap");
    if (!data || !data.rows || !data.rows.length) {
      tw.innerHTML = '<div class="empty-list">SCD não contem IEDs.</div>';
      return;
    }
    const rows = data.rows.map((r) => rowHtml(r)).join("");
    tw.innerHTML = '<table class="vlans"><thead><tr><th>IED (relé)</th><th>IP</th><th>VLAN(s) a permitir no switch</th><th>RX / TX</th><th>Nao resolvido</th></tr></thead><tbody>' + rows + "</tbody></table>";
    applyFilter();
  }
  function rowVlans(r) {
    const set = /* @__PURE__ */ new Set([...r.rx_vlans || [], ...r.tx_vlans || []]);
    return [...set].sort((a, b) => vlanSortKey(a) - vlanSortKey(b));
  }
  function publishersForVlan(r, vid) {
    const out = [];
    const rxPubs = r.publishers_by_vlan && r.publishers_by_vlan[vid] || [];
    out.push(...rxPubs);
    if ((r.tx_vlans || []).indexOf(vid) >= 0) out.push("(self)");
    return out;
  }
  function chipHtml(r, vid) {
    const inRx = (r.rx_vlans || []).indexOf(vid) >= 0;
    const inTx = (r.tx_vlans || []).indexOf(vid) >= 0;
    const cls = inRx && inTx ? "both" : inRx ? "rx" : "tx";
    const kindLabel = inRx && inTx ? "RX + TX" : inRx ? "RX (subscrito)" : "TX (publicado)";
    const pubs = publishersForVlan(r, vid);
    let srcText = "";
    let srcCls = "";
    if (pubs.length === 0) {
      srcText = "—";
    } else if (pubs.length === 1) {
      srcText = pubs[0];
      if (srcText === "(self)") srcCls = " self";
    } else {
      const first = pubs[0];
      const rest = pubs.length - 1;
      srcText = first + "  +" + rest;
      if (first === "(self)") srcCls = " self";
    }
    const tipPubs = pubs.length ? "\n" + pubs.map((p) => "  " + p).join("\n") : "";
    const title = "VLAN " + vlanFmt(vid) + " (" + kindLabel + ")" + tipPubs;
    return '<span class="chip ' + cls + '" title="' + escAttr(title) + '"><span class="vid">' + escHtml(vlanFmt(vid)) + '</span><span class="src' + srcCls + '">' + escHtml(srcText) + "</span></span>";
  }
  function csvText(r) {
    return rowVlans(r).map((v) => vlanFmt(v)).join(", ");
  }
  function rowHtml(r) {
    const vlans = rowVlans(r);
    const rxSize = (r.rx_vlans || []).length;
    const txSize = (r.tx_vlans || []).length;
    const chips = vlans.length ? vlans.map((v) => chipHtml(r, v)).join("") : '<span class="chip" title="Sem GOOSE">&mdash;</span>';
    const legend = rxSize && txSize ? '<div class="legend">azul = RX &middot; verde = TX &middot; roxo = ambos</div>' : "";
    const subline = r.relay_type || r.description ? '<div class="subline">' + (r.relay_type ? escHtml(r.relay_type) : "") + (r.relay_type && r.description ? " &middot; " : "") + (r.description ? escHtml(r.description) : "") + "</div>" : "";
    let unresolvedCell = "";
    if (r.unresolved && r.unresolved.length) {
      unresolvedCell = "<details><summary>" + r.unresolved.length + " GSE(s)</summary><ul>" + r.unresolved.map((u) => "<li>" + escHtml(u) + "</li>").join("") + "</ul></details>";
    }
    const cls = vlans.length ? "" : ' class="empty-row"';
    const searchValues = [
      r.ied_name,
      r.ip,
      r.relay_type,
      r.description,
      ...vlans,
      ...vlans.map(vlanFmt)
    ];
    const initialMode = _displayMode === "text" ? "text" : "chips";
    const cellInner = initialMode === "text" ? csvCellHtml(r) : '<div class="chips">' + chips + "</div>" + legend;
    return "<tr" + cls + ' data-ied="' + escAttr(r.ied_name) + '" data-search="' + escAttr(searchValues.join(" ").toLowerCase()) + '"><td class="ied">' + escHtml(r.ied_name) + subline + '</td><td class="ip">' + escHtml(r.ip || "-") + '</td><td class="vlans-cell" data-mode="' + initialMode + '">' + cellInner + '</td><td class="count">' + r.rx_count + " / " + r.tx_count + '</td><td class="unresolved">' + unresolvedCell + "</td></tr>";
  }
  function csvCellHtml(r) {
    const text = csvText(r);
    const isEmpty = !text;
    return '<div class="csv-text' + (isEmpty ? " empty" : "") + '">' + (isEmpty ? "(sem VLANs)" : escHtml(text)) + "</div>";
  }
  let _filterQ = "";
  function applyFilter() {
    const q = _filterQ;
    document.querySelectorAll("table.vlans tbody tr").forEach((tr) => {
      if (!q) {
        tr.style.removeProperty("display");
        return;
      }
      const hay = tr.dataset.search || "";
      tr.style.display = hay.indexOf(q) >= 0 ? "" : "none";
    });
  }
  (function setupFilterAndToggle() {
    const filter = document.getElementById("filter");
    filter.addEventListener("input", () => {
      _filterQ = (filter.value || "").trim().toLowerCase();
      applyFilter();
    });
    const toggle = document.getElementById("toggle-empty");
    const KEY = "vlan-mapper-hide-empty";
    function apply(on) {
      document.body.classList.toggle("hide-empty", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      toggle.classList.toggle("active", on);
      toggle.textContent = on ? "Mostrar sem VLAN" : "Ocultar sem VLAN";
    }
    try {
      apply(localStorage.getItem(KEY) === "1");
    } catch (e) {
      apply(false);
    }
    toggle.addEventListener("click", () => {
      const next = !document.body.classList.contains("hide-empty");
      apply(next);
      try {
        localStorage.setItem(KEY, next ? "1" : "0");
      } catch (e) {
      }
    });
  })();
  (function setupFormatToggle() {
    const buttons = document.querySelectorAll(".fmt-group button[data-fmt]");
    function apply(fmt) {
      _vlanFmt = fmt;
      buttons.forEach((b) => {
        const active = b.dataset.fmt === fmt;
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      try {
        localStorage.setItem("vlan-mapper-fmt", fmt);
      } catch (e) {
      }
      if (_lastData) {
        renderSummary();
        renderTable();
      }
    }
    apply(_vlanFmt);
    buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.fmt)));
  })();
  (function setupModeToggle() {
    const buttons = document.querySelectorAll(".fmt-group button[data-mode]");
    function apply(mode) {
      _displayMode = mode === "text" ? "text" : "chips";
      buttons.forEach((b) => {
        const active = b.dataset.mode === _displayMode;
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      try {
        localStorage.setItem("vlan-mapper-mode", _displayMode);
      } catch (e) {
      }
      if (_lastData) renderTable();
    }
    apply(_displayMode);
    buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.mode)));
  })();
  document.getElementById("copy-csv").addEventListener("click", async () => {
    if (!_lastData || !_lastData.rows) return;
    const header = ["IED", "IP", "Type", "Description", "RX_VLANs", "TX_VLANs", "All_VLANs (" + _vlanFmt + ")"];
    const lines = [header.join(",")];
    for (const r of _lastData.rows) {
      const allRaw = [.../* @__PURE__ */ new Set([...r.rx_vlans || [], ...r.tx_vlans || []])].sort((a, b) => vlanSortKey(a) - vlanSortKey(b));
      const row = [
        r.ied_name,
        r.ip || "",
        r.relay_type || "",
        r.description || "",
        (r.rx_vlans || []).join(";"),
        (r.tx_vlans || []).join(";"),
        allRaw.map(vlanFmt).join(";")
      ].map((v) => {
        const s = String(v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      });
      lines.push(row.join(","));
    }
    const csv = lines.join("\n");
    try {
      await navigator.clipboard.writeText(csv);
      setStatus("CSV copiado para a área de transferencia.", "ok");
    } catch (e) {
      setStatus("Falha ao copiar: " + e, "err");
    }
  });
  (function setupBackToMenu() {
    const btn = document.getElementById("back-to-menu");
    if (btn) btn.addEventListener("click", () => {
      window.location.href = "/";
    });
  })();
  (async function init() {
    try {
      const r = await fetch("/state", { cache: "no-store" });
      const data = await r.json();
      if (data.has_scd) {
        render(data);
      }
    } catch (e) {
    }
  })();

})();
