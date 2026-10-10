// SCALE map extras: view switcher, period + facet filters, detail panel, reading list, "where does my paper fit?".
// Runs inside datamapplot's module script, so `datamap` (and the global `deck`) are in scope.
// Config is injected by 08_build_map.py as window.NX.
(() => {
  const NX = window.NX;
  const $ = (id) => document.getElementById(id);
  const SHEETS = ["paper-panel", "agenda-panel", "trends-panel", "filter-panel"];
  const meta = (f, i) => (datamap.metaData && datamap.metaData[f] ? datamap.metaData[f][i] : "") ?? "";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const whenData = (fn) => {
    const t = setInterval(() => { if (datamap.metaData && datamap.metaData.pid) { clearInterval(t); fn(); } }, 150);
  };
  let pidIndex = null;
  const indexOfPid = (pid) => {
    if (!pidIndex) pidIndex = new Map(datamap.metaData.pid.map((p, i) => [p, i]));
    return pidIndex.get(pid);
  };

  // All programmatic camera moves go through goTo(). The current view is read from deck.gl itself
  // (datamapplot adjusts the initial view after loading, so a value captured at startup goes stale);
  // while a move is still animating, its target counts as current so rapid clicks stack properly.
  let pendingView = null;
  function currentView() {
    if (pendingView && performance.now() < pendingView.until) return pendingView.vs;
    const vp = datamap.deckgl.getViewports()[0];
    return { longitude: vp.longitude, latitude: vp.latitude, zoom: vp.zoom, pitch: 0, bearing: 0 };
  }
  // The opening view, captured once datamapplot has finished placing the initial camera after load.
  let initialView = null;
  whenData(() => {
    let last = null; const t0 = performance.now();
    const poll = setInterval(() => {
      const vp = datamap.deckgl.getViewports()[0];
      const key = `${vp.zoom.toFixed(4)},${vp.longitude.toFixed(5)},${vp.latitude.toFixed(5)}`;
      if (key === last || performance.now() - t0 > 5000) {
        initialView = initialView || { longitude: vp.longitude, latitude: vp.latitude, zoom: vp.zoom };
        clearInterval(poll);
      }
      last = key;
    }, 250);
  });

  function goTo(v, ms = 900) {
    const vs = { ...currentView(), ...v, transitionDuration: ms };
    pendingView = { vs, until: performance.now() + ms };
    datamap.deckgl.setProps({ initialViewState: vs });
    datamap.notifyViewStateChange(vs);
  }

  // ---------------- Navigation ----------------
  $("nx-views").innerHTML = NX.views.map((v) =>
    `<a href="${v.file}" class="${v.key === NX.view ? "active" : ""}" title="${esc(v.help)}">${esc(v.label)}</a>`).join("") +
    `<a href="evidence.html" title="Charts: what kind of evidence the repository holds, and where the gaps are">Evidence ↗</a>`;
  const yearChips = ["All", ...NX.periods];
  $("nx-years").innerHTML = yearChips.map((y) => `<button data-year="${y}" class="${y === "All" ? "active" : ""}">${y}</button>`).join("");
  // Keep the subtitle's paper count in step with the period and facet filters.
  const subtitle = [...document.querySelectorAll("#title-container *")].find((el) =>
    el.children.length === 0 && /papers in the/.test(el.textContent));
  const subtitleRest = subtitle ? subtitle.textContent.trim().replace(/^[\d,]+ (?:of [\d,]+ )?papers(?: first posted in \S+)?/, "") : "";
  const total = Object.values(NX.periodCounts).reduce((a, b) => a + b, 0);
  let currentYear = "All";
  function updateCount(y = currentYear) {
    if (!subtitle) return;
    const filtered = activeFilterCount() > 0;
    const n = filtered ? datamap.getSelectedIndices().size : y === "All" ? total : NX.periodCounts[y] || 0;
    const label = y === "All" ? "" : ` first posted in ${y}`;
    subtitle.textContent = `${n.toLocaleString("en-US")} ${filtered ? `of ${total.toLocaleString("en-US")} ` : ""}papers${label}${subtitleRest}`;
  }
  function setYear(y) {
    currentYear = y;
    updateCount(y);
    [...$("nx-years").children].forEach((b) => b.classList.toggle("active", b.dataset.year === y));
    whenData(() => {
      if (y === "All") datamap.removeSelection("year-filter");
      else {
        const idx = [];
        datamap.metaData.period.forEach((v, i) => { if (String(v) === y) idx.push(i); });
        datamap.addSelection(idx, "year-filter");
      }
      updateCount(y);
    });
  }
  $("nx-years").addEventListener("click", (e) => { if (e.target.dataset.year) setYear(e.target.dataset.year); });
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => $(b.dataset.close).classList.add("hidden")));
  document.addEventListener("keydown", (e) => {
    // Escape closes side panels but not the "where does my paper fit?" modal: it stays open until the
    // user places the paper (or clicks its close button), so a stray key press can't discard the text.
    if (e.key === "Escape") SHEETS.forEach((id) => $(id).classList.add("hidden"));
  });

  // ---------------- Reading list (localStorage, shared across the map pages) ----------------
  const KEY = "scale-map-reading-list";
  const loadAgenda = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const saveAgenda = (a) => { localStorage.setItem(KEY, JSON.stringify(a)); renderAgendaCount(); };
  const renderAgendaCount = () => { $("nx-agenda-count").textContent = Object.keys(loadAgenda()).length; };
  const agendaEntry = (i) => ({
    pid: meta("pid", i), title: meta("hover_text", i), authors: meta("authors", i), month: meta("month", i),
    publication: meta("publication", i), tier: meta("tier", i), key_finding: meta("key_finding", i),
    link: meta("link", i), url: meta("url", i), added: Date.now(),
  });
  const toggleAgenda = (i) => {
    const a = loadAgenda(); const pid = meta("pid", i);
    if (a[pid]) delete a[pid]; else a[pid] = agendaEntry(i);
    saveAgenda(a);
  };

  function renderAgenda() {
    // Grouped by evidence tier, in tier order, so the strongest evidence is read first.
    const order = NX.filters.find((f) => f.key === "tier").options;
    const items = Object.values(loadAgenda()).sort((a, b) => order.indexOf(a.tier) - order.indexOf(b.tier) || b.added - a.added);
    if (!items.length) { $("agenda-list").innerHTML = '<p class="nx-muted">No papers yet.</p>'; return; }
    let html = "", group = null;
    items.forEach((it) => {
      if (it.tier !== group) { html += `<h3>${esc(it.tier || "Other")}</h3>`; group = it.tier; }
      html += `<div class="agenda-item"><div class="what"><a href="#" data-pid="${esc(it.pid)}">${esc(it.title)}</a>
          <div class="where">${esc([it.authors.split(",").slice(0, 2).join(",") + (it.authors.split(",").length > 2 ? " et al." : ""), it.month, it.publication].filter(Boolean).join(" · "))}</div></div>
        <button data-remove="${esc(it.pid)}" title="Remove">&times;</button></div>`;
    });
    $("agenda-list").innerHTML = html;
  }
  $("agenda-list").addEventListener("click", (e) => {
    if (e.target.dataset.remove) { const a = loadAgenda(); delete a[e.target.dataset.remove]; saveAgenda(a); renderAgenda(); }
    if (e.target.dataset.pid) { e.preventDefault(); const i = indexOfPid(e.target.dataset.pid); if (i !== undefined) showPaper(i); }
  });
  const openSheet = (id) => { SHEETS.forEach((x) => $(x).classList.toggle("hidden", x !== id)); };
  $("nx-agenda-btn").onclick = () => { renderAgenda(); openSheet("agenda-panel"); };
  $("agenda-clear").onclick = () => { if (confirm("Remove all papers from your reading list?")) { saveAgenda({}); renderAgenda(); } };

  const download = (name, text, type) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  // RIS imports into Zotero, EndNote and Mendeley.
  $("agenda-ris").onclick = () => {
    const lines = [];
    Object.values(loadAgenda()).forEach((it) => {
      const isPreprint = /arxiv|ssrn|working|nber/i.test(it.publication || "");
      lines.push(`TY  - ${isPreprint ? "UNPB" : "JOUR"}`, `TI  - ${it.title}`);
      String(it.authors || "").split(",").map((x) => x.trim()).filter(Boolean).forEach((au) => lines.push(`AU  - ${au}`));
      if (it.month) lines.push(`PY  - ${it.month.slice(0, 4)}`, `DA  - ${it.month.replace("-", "/")}`);
      if (it.publication) lines.push(`T2  - ${it.publication}`);
      if (it.link) lines.push(`UR  - ${it.link}`);
      lines.push(`N1  - Evidence tier (AI-derived): ${it.tier}. Key finding: ${it.key_finding}`, `L2  - ${it.url}`, "ER  - ", "");
    });
    download("scale-reading-list.ris", lines.join("\r\n"), "application/x-research-info-systems");
  };
  $("agenda-csv").onclick = () => {
    const cols = ["title", "authors", "month", "publication", "tier", "key_finding", "link", "url"];
    const rows = Object.values(loadAgenda()).map((it) => cols.map((c) => `"${String(it[c] ?? "").replace(/"/g, '""')}"`).join(","));
    download("scale-reading-list.csv", [cols.join(","), ...rows].join("\n"), "text/csv");
  };

  // Lasso selection -> "add N to reading list"
  let lassoed = [];
  datamap.addSelectionHandler((sel) => {
    lassoed = Array.from(sel || []);
    $("nx-lasso-n").textContent = lassoed.length;
    $("nx-lasso-add").classList.toggle("hidden", lassoed.length === 0);
  }).catch(() => {});
  $("nx-lasso-add").onclick = () => {
    const a = loadAgenda(); lassoed.forEach((i) => { a[meta("pid", i)] = agendaEntry(i); }); saveAgenda(a);
    $("nx-lasso-add").classList.add("hidden");
  };

  // ---------------- Detail panel ----------------
  let current = null;
  const row = (k, v) => (v ? `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>` : "");
  function showPaper(i) {
    current = i;
    const cites = meta("citations", i), pct = meta("citation_pct", i), tier = meta("tier", i), n = meta("sample_size", i);
    $("paper-badges").innerHTML = `<span>${esc(meta("month", i) || meta("period", i))}</span>` +
      `<span class="tier" title="${esc((NX.filters.find((f) => f.key === "tier").help || {})[tier] || "")}">${esc(tier)}</span>` +
      (n ? `<span>n = ${Number(n).toLocaleString("en-US")}</span>` : "") +
      (meta("peer", i) ? `<span class="${meta("peer", i) === "Peer reviewed" ? "peer" : "pre"}">${esc(meta("peer", i))}</span>` : "") +
      (cites !== "" && cites !== null ? `<span class="cite" title="Semantic Scholar">${esc(cites)} citations${
        pct && 100 - pct <= 25 ? ` · top ${Math.max(1, Math.round(100 - pct))}% for its year` : ""}</span>` : "");
    $("paper-title").textContent = meta("hover_text", i);
    $("paper-authors").textContent = meta("authors", i);
    $("paper-institutions").textContent = meta("publication", i);
    $("paper-cluster").textContent = meta("path", i);
    ["eli5", "key_finding", "takeaway", "problem", "method", "abstract"].forEach((f) => { $("paper-" + f).textContent = meta(f, i); });
    $("paper-evidence").innerHTML = row("Study type", meta("study_type", i)) + row("Measures", meta("outcomes", i)) +
      row("Main result", meta("direction", i)) + row("Participants", meta("participants", i) === "Not stated" ? "" : meta("participants", i)) +
      row("Country", meta("countries", i)) + row("Subject", meta("subjects", i)) + row("AI tools", meta("ai_tools", i));
    $("paper-scale").innerHTML = NX.filters.filter((f) => f.key.startsWith("f_"))
      .map((f) => row(f.label.replace("?", ""), meta(f.key, i))).join("");
    document.querySelectorAll(".paper-section").forEach((el) => {
      const p = el.querySelector("p, table"); el.style.display = p && p.textContent ? "" : "none";
    });

    const t = NX.trends[meta("trend_key", i)];
    $("paper-trend").innerHTML = t ? `<b>Topic: ${esc(t.name)}</b> — <span class="nx-trend ${esc(t.label)}">${esc(t.label)}</span>` +
      ` (${NX.periods.map((y) => `${y}: ${t.counts[y] ?? 0}`).join(", ")} papers)` + (t.whats_new ? `<br><b>What's new in ${NX.latestPeriod}:</b> ${esc(t.whats_new)}` : "") : "";
    $("paper-flag").textContent = meta("audit_flag", i) ? "⚠ The audit found parts of these AI-written notes may not be supported by the abstract: " + meta("audit_flag", i) : "";

    $("paper-tags").innerHTML = String(meta("tags", i)).split("; ").filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    const links = [["SCALE entry", "url"], ["Paper", "link"], ["Semantic Scholar", "s2_url"]];
    $("paper-links").innerHTML = links.filter(([, f]) => meta(f, i)).map(([l, f]) =>
      `<a href="${esc(meta(f, i))}" target="_blank" rel="noopener">${esc(l)} ↗</a>`).join("");
    renderAgendaButton();
    openSheet("paper-panel");
  }
  function renderAgendaButton() {
    const saved = !!loadAgenda()[meta("pid", current)];
    $("paper-agenda").textContent = saved ? "★ In reading list (click to remove)" : "☆ Add to reading list";
    $("paper-agenda").classList.toggle("saved", saved);
  }
  $("paper-agenda").onclick = () => { if (current !== null) { toggleAgenda(current); renderAgendaButton(); } };
  window.showPaper = showPaper;

  // ---------------- Zoom to a category so its name is actually rendered ----------------
  // datamapplot labels have a fixed pixel size and are collision-filtered: a label is hidden while
  // its collision box (3x the text, 36-72 px font) overlaps the box of any higher-priority label,
  // and coarser layers always win. Fitting the category's bounds therefore often leaves its name
  // hidden under its parent's label. Instead: centre on the label's anchor in the unobstructed
  // part of the map and step the zoom in until no higher-priority label's box overlaps it.
  const labelData = () => datamap.labelLayer.props.data;
  let labelById = null;
  const findLabel = (id) => {
    if (!labelById) labelById = new Map(labelData().map((d) => [d.id, d]));
    return labelById.get(id);
  };
  const norm = (t) => String(t).replace(/\s+/g, " ").trim().toLowerCase();
  const findLabelByName = (name) => labelData().find((d) => norm(d.label) === norm(name));

  function visibleArea() {
    const W = window.innerWidth, H = window.innerHeight;
    let left = 0, right = W;
    if (W > 760) {
      const tree = document.querySelector(".stack.top-left");
      if (tree) left = Math.min(tree.getBoundingClientRect().right, W * 0.45);
      const sheet = [...document.querySelectorAll(".nx-sheet")].find((el) => !el.classList.contains("hidden"));
      if (sheet) right = sheet.getBoundingClientRect().left;
    }
    return { W, H, cx: (left + right) / 2, cy: H / 2 + 30, width: right - left };
  }

  function collisionBox(d, vp) {
    const [px, py] = vp.project([d.x, d.y]);
    const fs = Math.min(Math.max(d.size * 3, 36), 72);
    const lines = String(d.label).split("\n");
    const w = Math.max(...lines.map((l) => l.length)) * 0.55 * fs + 30;
    const h = lines.length * fs * 0.95 + 30;
    return [px - w / 2, py - h / 2, px + w / 2, py + h / 2];
  }
  const overlaps = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

  function viewFor(d, zoom, area) {
    // Put the label's anchor at the visible-area centre: offset the view centre by the pixel gap.
    const degPerPx = 360 / (512 * Math.pow(2, zoom));
    const probe = new deck.WebMercatorViewport({ width: area.W, height: area.H, longitude: d.x, latitude: d.y, zoom });
    const [lng, lat] = probe.unproject([area.W / 2 - (area.cx - area.W / 2), area.H / 2 - (area.cy - area.H / 2)]);
    return { longitude: lng ?? d.x + (area.W / 2 - area.cx) * degPerPx, latitude: lat ?? d.y, zoom };
  }

  function zoomToShowLabel(d) {
    const area = visibleArea();
    const [x0, x1, y0, y1] = d.bounds || [d.x, d.x, d.y, d.y];
    // Start from "fit the category in the visible area" (512 px tiles), then zoom in as needed.
    const fit = Math.min(Math.log2(360 / (Math.max(x1 - x0, 1e-3) / (area.width / 512))),
                         Math.log2(360 / (Math.max(y1 - y0, 1e-3) / (area.H / 512)))) - 0.2;
    const rivals = labelData().filter((o) => o !== d && (o.collision_priority ?? o.size) >= (d.collision_priority ?? d.size));
    let target = null;
    for (let z = fit; z <= fit + 9; z += 0.25) {
      const v = viewFor(d, z, area);
      const vp = new deck.WebMercatorViewport({ width: area.W, height: area.H, ...v });
      const box = collisionBox(d, vp);
      const blocked = rivals.some((o) => {
        const b = collisionBox(o, vp);
        return b[2] > 0 && b[0] < area.W && b[3] > 0 && b[1] < area.H && overlaps(box, b);
      });
      if (!blocked) { target = v; break; }
    }
    target = target || viewFor(d, fit + 9, area);
    goTo(target, 1000);
    return target;
  }
  window.zoomToShowLabel = zoomToShowLabel;

  // Take over clicks on topic-tree names (capture phase runs before datamapplot's own handler).
  document.addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest(".topic-tree-label");
    if (!el) return;
    const d = findLabel(el.dataset.labelId);
    if (!d) return;  // unknown id: let datamapplot handle it
    e.stopPropagation(); e.preventDefault();
    zoomToShowLabel(d);
  }, true);

  // ---------------- Zoom controls: + / − / zoom to selection / fit, plus keyboard + − 0 ----------------
  // Frame a set of points (trimmed so a few outliers don't zoom the map out) in the visible area.
  function frame(indices, trim) {
    const pos = datamap.pointLayer.props.data.attributes.getPosition.value;
    const q = (arr, f) => { const a = Float64Array.from(arr).sort(); return a[Math.floor(f * (a.length - 1))]; };
    const xs = indices.map((i) => pos[2 * i]), ys = indices.map((i) => pos[2 * i + 1]);
    const [x0, x1, y0, y1] = [q(xs, trim), q(xs, 1 - trim), q(ys, trim), q(ys, 1 - trim)];
    const area = visibleArea();
    const zoom = Math.min(Math.log2(360 / (Math.max(x1 - x0, 1e-3) / (area.width / 512))),
                          Math.log2(360 / (Math.max(y1 - y0, 1e-3) / (area.H / 512)))) - 0.3;
    return viewFor({ x: (x0 + x1) / 2, y: (y0 + y1) / 2 }, zoom, area);
  }
  let homeCache = null;
  const homeView = () => (homeCache ||= frame(Array.from({ length: datamap.metaData.pid.length }, (_, i) => i), 0.005));
  const clampZoom = (z) => Math.min(homeView().zoom + 14, Math.max(homeView().zoom - 1.5, z));
  const setView = (v, ms = 350) => goTo({ ...v, zoom: clampZoom(v.zoom ?? currentView().zoom) }, ms);
  const zoomBy = (dz) => setView({ zoom: currentView().zoom + dz });

  // Frame the highlighted papers (search, year filter, lasso, trend topic, "my paper" neighbours).
  function zoomToSelection() {
    const sel = Array.from(datamap.getSelectedIndices());
    if (!sel.length) return;
    setView(frame(sel, sel.length > 20 ? 0.05 : 0), 900);  // ignore far-flung outliers in big selections
  }
  $("nx-zoom-in").onclick = () => zoomBy(1);
  $("nx-zoom-out").onclick = () => zoomBy(-1);
  $("nx-zoom-fit").onclick = () => goTo(homeView(), 900);
  $("nx-zoom-sel").onclick = zoomToSelection;
  // The "zoom to selection" button is only active while something is highlighted, and the whole stack
  // moves clear of an open side panel (desktop: beside it; mobile bottom sheet: above it).
  setInterval(() => {
    $("nx-zoom-sel").disabled = datamap.getSelectedIndices().size === 0;
    const sheet = [...document.querySelectorAll(".nx-sheet")].find((el) => !el.classList.contains("hidden"));
    const z = $("nx-zoom"), mobile = window.innerWidth <= 760;
    document.body.classList.toggle("nx-sheet-open", !!sheet);  // the toolbar wraps clear of an open panel
    if (!sheet) { z.style.right = ""; z.style.bottom = ""; return; }
    const r = sheet.getBoundingClientRect();
    if (mobile) z.style.bottom = `${window.innerHeight - r.top + 12}px`;
    else z.style.right = `${window.innerWidth - r.left + 16}px`;
  }, 200);

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;  // leave browser zoom (Cmd/Ctrl +/-) alone
    const t = e.target, typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    if (typing || !$("fit-modal").classList.contains("hidden")) return;
    if (e.key === "+" || e.key === "=") { zoomBy(1); e.preventDefault(); }
    else if (e.key === "-" || e.key === "_") { zoomBy(-1); e.preventDefault(); }
    else if (e.key === "0") { goTo(homeView(), 900); e.preventDefault(); }
  });

  // ---------------- Facet filter panel ----------------
  // NX.filters: [{key, label, options, multi}] where key is a metadata column. Multi-valued columns hold
  // "; "-joined tags. Within a filter any ticked option matches; across filters all must match.
  const chosen = {};  // key -> Set of ticked options
  function activeFilterCount() { return Object.values(chosen).filter((s) => s.size).length; }
  $("filter-groups").innerHTML = NX.filters.map((f) => `<div class="filter-group"><h4>${esc(f.label)}</h4>` +
    f.options.map((o) => `<label title="${esc((f.help || {})[o] || "")}"><input type="checkbox" data-key="${esc(f.key)}" value="${esc(o)}"><span>${esc(o)}</span><span class="filter-n" data-key="${esc(f.key)}" data-opt="${esc(o)}"></span></label>`).join("") +
    "</div>").join("");
  whenData(() => {
    // Option counts over the whole repository, so users can see how thin some slices are.
    NX.filters.forEach((f) => {
      const col = datamap.metaData[f.key], counts = {};
      col.forEach((v) => String(v).split("; ").forEach((x) => { counts[x] = (counts[x] || 0) + 1; }));
      document.querySelectorAll(`.filter-n[data-key="${f.key}"]`).forEach((el) => { el.textContent = counts[el.dataset.opt] || 0; });
    });
  });
  function applyFilters() {
    const n = activeFilterCount();
    $("nx-filter-n").textContent = n ? ` (${n})` : "";
    $("nx-filter-btn").classList.toggle("active", n > 0);
    whenData(() => {
      if (!n) { datamap.removeSelection("facet-filter"); $("filter-count").textContent = ""; updateCount(); return; }
      const keys = Object.keys(chosen).filter((k) => chosen[k].size);
      const cols = keys.map((k) => datamap.metaData[k]);
      const idx = [];
      for (let i = 0; i < datamap.metaData.pid.length; i++) {
        if (keys.every((k, j) => String(cols[j][i]).split("; ").some((x) => chosen[k].has(x)))) idx.push(i);
      }
      datamap.addSelection(idx, "facet-filter");
      $("filter-count").textContent = `${idx.length.toLocaleString("en-US")} papers match` + (currentYear === "All" ? "" : ` (before the ${currentYear} chip)`);
      updateCount();
    });
  }
  $("filter-groups").addEventListener("change", (e) => {
    const k = e.target.dataset.key; if (!k) return;
    (chosen[k] ||= new Set())[e.target.checked ? "add" : "delete"](e.target.value);
    applyFilters();
  });
  function clearFilters() {
    Object.keys(chosen).forEach((k) => delete chosen[k]);
    document.querySelectorAll("#filter-groups input").forEach((el) => { el.checked = false; });
    applyFilters();
  }
  $("filter-clear").onclick = clearFilters;
  $("nx-filter-btn").onclick = () => openSheet("filter-panel");

  // ---------------- Trends panel ----------------
  // NX.trendLayers: [{layer, label}] coarse -> fine; NX.trends["layer:id"] = {name, counts, growth, label, whats_new}
  let trendLayer = NX.trendLayers.length > 1 ? NX.trendLayers[1].layer : NX.trendLayers[0]?.layer;
  let trendSort = "up", openTopic = null;
  $("trends-span").textContent = NX.periods.length > 1 ? `${NX.periods[NX.periods.length - 2]} → ${NX.latestPeriod}` : "";
  $("trends-layers").innerHTML = NX.trendLayers.map((l) =>
    `<button data-layer="${l.layer}" class="${l.layer === trendLayer ? "active" : ""}">${esc(l.label)}</button>`).join("");
  $("trends-layers").addEventListener("click", (e) => {
    if (e.target.dataset.layer === undefined) return;
    trendLayer = +e.target.dataset.layer; openTopic = null;
    [...$("trends-layers").children].forEach((b) => b.classList.toggle("active", b === e.target));
    renderTrends();
  });
  $("trends-sort").addEventListener("click", (e) => {
    if (!e.target.dataset.sort) return;
    trendSort = e.target.dataset.sort;
    [...$("trends-sort").children].forEach((b) => b.classList.toggle("active", b === e.target));
    renderTrends();
  });

  // Each row shows clearly labelled numbers, because a topic's raw paper count and its share of the
  // (fast-growing) repository move very differently, and the latest period is a partial year.
  const yrs = NX.periods, last = yrs[yrs.length - 1], prev = yrs[yrs.length - 2], first = yrs[0];
  const share = (c, y) => (c + 1) / ((NX.periodCounts[y] || 0) + 1);  // same +1 smoothing as 06_trends.py
  const pct = (r) => `${r >= 1 ? "+" : "−"}${Math.abs(Math.round((r - 1) * 100))}%`;
  const NEUTRAL = 0.025;  // |share change| below 2.5% is shown as "≈", not as up/down

  function trendSummary() {
    if (!prev) return "";
    return `Papers in the repository by when they first appeared: ${yrs.map((y) => `${y}: ${NX.periodCounts[y].toLocaleString("en-US")}`).join(", ")}` +
      ` (${last} is January to date). A topic gains share when it grows faster than the repository as a whole.`;
  }

  function renderTrends() {
    const topics = Object.entries(NX.trends).filter(([k]) => +k.split(":")[0] === trendLayer)
      .map(([k, v]) => ({ key: k, ...v, total: Object.values(v.counts).reduce((a, b) => a + b, 0) }))
      .filter((t) => t.total >= 6);  // tiny topics swing wildly
    const sorter = { up: (a, b) => b.growth - a.growth, down: (a, b) => a.growth - b.growth, size: (a, b) => b.total - a.total }[trendSort];
    topics.sort(sorter);
    const maxCount = Math.max(1, ...topics.flatMap((t) => Object.values(t.counts)));
    $("trends-list").innerHTML = topics.slice(0, 40).map((t) => {
      const c = (y) => t.counts[y] ?? 0;
      const shareX = Math.pow(2, t.growth);                       // share change, last two years
      const dir = Math.abs(shareX - 1) < NEUTRAL ? "flat" : shareX > 1 ? "up" : "down";
      const arrow = { up: "▲", down: "▼", flat: "≈" }[dir];
      const rawX = c(prev) ? c(last) / c(prev) : null;              // raw paper growth, last two years
      const longX = yrs.length > 2 ? share(c(last), last) / share(c(first), first) : null;  // share change since first year
      const bars = yrs.map((y) =>
        `<div class="trend-bar"><div style="height:${Math.max(2, Math.round(36 * c(y) / maxCount))}px"></div>${y.replace(/^20/, "")}: ${c(y)}</div>`).join("");
      const open = t.key === openTopic;
      return `<div class="trend-row${open ? " open" : ""}" data-key="${t.key}">
        <div class="trend-head"><span class="trend-name">${esc(t.name)}</span>
          <span class="trend-x ${dir}" title="Change in this topic's share of all papers, ${prev} → ${last}">${arrow} share ×${shareX.toFixed(2)}</span></div>
        <div class="trend-bars">${bars}
          <div class="trend-facts">
            ${rawX !== null ? `<span title="Raw change in paper count, ${prev} → ${last}">${pct(rawX)} papers</span>` : ""}
            ${longX !== null ? `<span title="Change in share of all papers, ${first} → ${last}">share ×${longX.toFixed(2)} since ${first}</span>` : ""}
            <span class="nx-trend ${esc(t.label)}" title="Label based on the ${prev} → ${last} share change">${esc(t.label)}</span>
          </div></div>
        ${open && t.whats_new ? `<div class="trend-note"><b>What's new in ${NX.latestPeriod}:</b> ${esc(t.whats_new)}</div>` : ""}
      </div>`;
    }).join("") || '<p class="nx-muted">No trend data for this level.</p>';
  }


  function focusTopic(key) {
    const [layer, id] = key.split(":");
    const col = datamap.metaData["tid" + layer];
    const idx = [];
    col.forEach((v, i) => { if (String(v) === id) idx.push(i); });
    if (!idx.length) return;
    datamap.addSelection(idx, "trend-topic");
    $("trends-clear").classList.remove("hidden");
    // Fly to the topic's core (10th-90th percentile box, so a few far-flung members don't zoom us out),
    // using the zoom formula datamapplot uses for its initial view, centred in the area left of the panel.
    const pos = datamap.pointLayer.props.data.attributes.getPosition.value;
    const q = (arr, f) => { const a = [...arr].sort((m, n) => m - n); return a[Math.floor(f * (a.length - 1))]; };
    const xs = idx.map((i) => pos[2 * i]), ys = idx.map((i) => pos[2 * i + 1]);
    const [x0, x1, y0, y1] = [q(xs, 0.1), q(xs, 0.9), q(ys, 0.1), q(ys, 0.9)];
    const panel = window.innerWidth > 760 ? 440 : 0, w = window.innerWidth - panel, h = window.innerHeight;
    // deck.gl web-mercator tiles are 512 px: at zoom z the world (360°) spans 512 * 2^z pixels, and near
    // the data's centre latitude scales the same as longitude (datamapplot's own helper uses 180° for y,
    // which zooms a full level too far out whenever height is the limiting side).
    const zoom = Math.min(Math.log2(360 / ((x1 - x0 || 1e-3) / (w / 512))), Math.log2(360 / ((y1 - y0 || 1e-3) / (h / 512)))) - 0.3;
    const degPerPx = 360 / (512 * Math.pow(2, zoom));
    const label = findLabelByName(NX.trends[key]?.name || "");
    if (label) { zoomToShowLabel(label); return; }  // centre on its map label and make sure it renders
    goTo({ longitude: (x0 + x1) / 2 + (panel / 2) * degPerPx, latitude: (y0 + y1) / 2, zoom });
  }
  $("trends-list").addEventListener("click", (e) => {
    const row = e.target.closest(".trend-row");
    if (!row) return;
    openTopic = row.dataset.key; renderTrends();
    whenData(() => focusTopic(openTopic));
  });
  $("trends-clear").onclick = () => {
    datamap.removeSelection("trend-topic"); openTopic = null; renderTrends();
    $("trends-clear").classList.add("hidden");
  };
  $("nx-trends-btn").onclick = () => { $("trends-summary").textContent = trendSummary(); renderTrends(); openSheet("trends-panel"); };

  // ---------------- Where does my paper fit? ----------------
  let extractor = null, index = null;
  const status = (s) => { $("fit-status").textContent = s; };
  $("nx-fit-btn").onclick = () => $("fit-modal").classList.remove("hidden");
  function loadIndex() {
    if (index) return index;
    const bin = atob(NX.searchIndexB64);
    const buf = new Int8Array(bin.length);
    for (let k = 0; k < bin.length; k++) buf[k] = (bin.charCodeAt(k) << 24) >> 24;
    return (index = buf);
  }
  async function embed(text) {
    if (!extractor) {
      status("Loading model (first time downloads ~570 MB)…");
      const { pipeline } = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3");
      const files = {};
      extractor = await pipeline("feature-extraction", NX.searchModel, {
        dtype: "q4f16",  // 567 MB; cosine 0.95 vs the Python index (q8: 0.88, fp16: 0.9999 at 1.2 GB)
        progress_callback: (p) => {
          if (p.status === "progress" && p.total) {
            files[p.file] = [p.loaded, p.total];
            const [l, t] = Object.values(files).reduce((s, v) => [s[0] + v[0], s[1] + v[1]], [0, 0]);
            status(`Downloading model… ${Math.round(100 * l / t)}%`);
          }
        },
      });
    }
    status("Embedding…");
    const out = await extractor([NX.searchInstruction + text], { pooling: "last_token", normalize: true });
    const v = Array.from(out.data).slice(0, NX.searchDim);
    const n = Math.hypot(...v);
    return v.map((x) => x / n);
  }
  // Step 1 ("Find closest papers"): embed and list the nearest papers in the modal; the map is untouched.
  // Step 2 ("Place it on the map"): pin + highlight those results and close the modal.
  let found = null;  // {text, top, sims} for the current text
  const placeBtn = $("fit-place");
  const setPlaceable = (ok) => { placeBtn.disabled = !ok; placeBtn.title = ok ? "Plot your paper on the map" : "Find the closest papers first"; };
  $("fit-text").addEventListener("input", () => {
    if (found && $("fit-text").value.trim() !== found.text) { found = null; setPlaceable(false); }
  });

  $("fit-go").onclick = async () => {
    const text = $("fit-text").value.trim();
    if (text.length < 40) return status("Paste a title and abstract first.");
    $("fit-go").disabled = true; setPlaceable(false);
    try {
      const q = await embed(text);
      const idx = loadIndex(), d = NX.searchDim, n = idx.length / d;
      const sims = new Float32Array(n);
      for (let i = 0; i < n; i++) { let s = 0; for (let k = 0; k < d; k++) s += q[k] * idx[i * d + k]; sims[i] = s / 127; }
      const top = Array.from(sims.keys()).sort((a, b) => sims[b] - sims[a]).slice(0, 12);
      // Search-index rows follow the same paper order as the map points.
      $("fit-results").innerHTML = top.map((i) =>
        `<li data-i="${i}">${esc(meta("hover_text", i))} <span class="sim">${esc(meta("period", i))} · ${esc(meta("tier", i))} · ${esc(meta("cluster", i))} · ${sims[i].toFixed(2)}</span></li>`).join("");
      found = { text, top, sims };
      status(`Closest topic: ${meta("path", top[0])}`);
      setPlaceable(true);
    } catch (err) {
      console.error(err);
      status("Couldn't load the in-browser model (" + err.message + "). It needs internet access to huggingface.co / jsdelivr.");
    } finally {
      $("fit-go").disabled = false;
    }
  };

  placeBtn.onclick = () => {
    if (!found) return;
    const top5 = found.top.slice(0, 5);
    datamap.addSelection(found.top, "my-paper");
    placePin(top5, top5.map((i) => Math.exp(20 * found.sims[i])));
    $("fit-modal").classList.add("hidden");
  };
  $("fit-results").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) showPaper(+li.dataset.i); });

  let pinView = null;

  const flyTo = (v) => goTo(v);

  // Release the pin: remove it and its neighbour highlight and reset the view. The text and the
  // results stay in the modal, so the user can place it again or edit and re-run.
  function removePin(fly = true) {
    datamap.layers = datamap.layers.filter((l) => l.id !== "my-paper-pin");
    datamap.deckgl.setProps({ layers: datamap.layers });
    datamap.removeSelection("my-paper");
    pinView = null;
    $("nx-pin").classList.add("hidden");
    if (fly) flyTo(homeView());
  }
  $("nx-pin-remove").onclick = () => removePin();
  $("nx-pin-show").onclick = () => {
    if (pinView) flyTo({ ...pinView, zoom: Math.max(currentView().zoom, homeView().zoom + 2.5) });
    $("fit-modal").classList.remove("hidden");  // shows the nearest-papers list again
  };

  function placePin(ids, weights) {
    const pos = datamap.pointLayer.props.data.attributes.getPosition.value;
    const W = weights.reduce((a, b) => a + b, 0);
    const x = ids.reduce((s, i, k) => s + pos[2 * i] * weights[k], 0) / W;
    const y = ids.reduce((s, i, k) => s + pos[2 * i + 1] * weights[k], 0) / W;
    const pin = new deck.ScatterplotLayer({
      id: "my-paper-pin", data: [{ p: [x, y] }], getPosition: (d) => d.p, getRadius: 11, radiusUnits: "pixels",
      getFillColor: [230, 40, 40, 230], getLineColor: [255, 255, 255, 255], lineWidthUnits: "pixels", getLineWidth: 3, stroked: true,
    });
    datamap.layers = [...datamap.layers.filter((l) => l.id !== "my-paper-pin"), pin];
    datamap.deckgl.setProps({ layers: datamap.layers });
    pinView = { longitude: x, latitude: y };
    $("nx-pin").classList.remove("hidden");
    goTo({ longitude: x, latitude: y, zoom: Math.max(currentView().zoom, homeView().zoom + 2.5) });
  }

  // ---------------- GitHub link with live star / fork counts ----------------
  // Counts come from GitHub's public API (60 unauthenticated requests/hour per visitor), cached for an
  // hour in sessionStorage. If the request fails (offline, rate-limited, private repo) the link still shows.
  if (NX.repo) {
    const gh = $("nx-github");
    gh.href = `https://github.com/${NX.repo}`; gh.classList.remove("hidden");
    const show = (d) => {
      [["nx-gh-stars", d.stargazers_count], ["nx-gh-forks", d.forks_count]].forEach(([id, n]) => {
        if (typeof n !== "number") return;
        $(id).querySelector("b").textContent = n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n;
        $(id).classList.remove("hidden");
      });
    };
    const key = `gh-counts:${NX.repo}`;
    let cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(key)); } catch {}
    if (cached && Date.now() - cached.t < 3600e3) show(cached.d);
    else fetch(`https://api.github.com/repos/${NX.repo}`, { headers: { Accept: "application/vnd.github+json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => { show(d); try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), d: { stargazers_count: d.stargazers_count, forks_count: d.forks_count } })); } catch {} })
      .catch(() => {});
  }

  // ---------------- Reset to the opening state ----------------
  // Restores the view and every filter/highlight to how the page opened. The reading list and any pasted
  // abstract are the user's own data, so they are kept.
  function resetAll() {
    setYear("All");
    clearFilters();
    const search = $("text-search");
    if (search && search.value) { search.value = ""; datamap.searchText(""); }
    datamap.removeSelection("trend-topic");
    openTopic = null; $("trends-clear").classList.add("hidden");
    const lasso = datamap.lassoSelector;
    if (lasso) { lasso.handleSelection([]); lasso.ctx?.clearRect(0, 0, lasso.canvas.width, lasso.canvas.height); }
    if (pinView) removePin(false);
    const firstColormap = document.querySelector(".color-map-option");
    if (firstColormap) firstColormap.click();  // "Clusters", the default colouring
    SHEETS.forEach((id) => $(id).classList.add("hidden"));
    goTo(initialView || homeView(), 900);
  }
  $("nx-reset").onclick = resetAll;
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || (e.key !== "r" && e.key !== "R")) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!$("fit-modal").classList.contains("hidden")) return;
    resetAll(); e.preventDefault();
  });

  renderAgendaCount();
})();
