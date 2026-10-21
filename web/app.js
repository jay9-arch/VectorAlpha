// VectorAlpha UI: sidebar shell, job console and the seven views.
let STATE = {}, STRATS = [], PRESELECT = null;
const JOB = { active: false };

// ============================================================ shell
const NAV = [
  ["Workspace", [["run", "Run", "run"], ["history", "History", "history"]]],
  ["Library", [["strategies", "Strategies", "strategies"], ["tickers", "Tickers", "tickers"]]],
  ["System", [["compiler", "Compiler", "compiler"], ["checks", "Checks", "checks"], ["data", "Data", "data"]]],
];
const VIEWS = {
  run: { title: "Run", sub: "Backtest a strategy on its UNIVERSE with any combination of engines." },
  history: { title: "History", sub: "Every execution, plus the raw timing log." },
  strategies: { title: "Strategies", sub: "View, edit, create and delete .valpha strategies." },
  tickers: { title: "Tickers", sub: "Price files in the active data folder. Click a ticker to show its CSV in the file explorer." },
  compiler: { title: "Compiler", sub: "Compile one strategy or all of them into native binaries." },
  checks: { title: "Checks", sub: "Verify the engines against hand-worked results." },
  data: { title: "Data", sub: "Choose the data folder or generate synthetic demo data." },
};
const READY = {};   // views that have built their markup

$("#nav").innerHTML = NAV.map(([g, items]) =>
  `<div class="nav-group"><div>${g}</div>${items.map(([k, label, ic]) => `<a href="#${k}" data-view="${k}">${icon(ic)}${label}</a>`).join("")}</div>`).join("");
$("#console-chev").innerHTML = icon("chev");
$$(".theme button").forEach(b => b.addEventListener("click", () => setTheme(b.dataset.theme)));
setTheme(document.documentElement.getAttribute("data-theme"));
$("#side-data").addEventListener("click", () => { location.hash = "data"; });

const VIEW_IMPL = {};
async function route() {
  let name = location.hash.slice(1);
  if (!VIEWS[name]) name = location.pathname === "/history" ? "history" : "run";
  $$(".view").forEach(v => v.hidden = v.id !== "view-" + name);
  $$(".nav a").forEach(a => a.classList.toggle("active", a.dataset.view === name));
  $("#page-title").textContent = VIEWS[name].title;
  $("#page-sub").textContent = VIEWS[name].sub;
  const impl = VIEW_IMPL[name];
  try {
    if (!READY[name]) { impl.build($("#view-" + name)); READY[name] = true; }
    await impl.show();
  } catch (e) { toast(e.message, true); }
}
window.addEventListener("hashchange", route);

async function loadState() {
  STATE = await api("GET", "/api/state");
  $("#side-data").innerHTML = `<b>${STATE.n_tickers.toLocaleString()}</b> tickers<span class="path" title="${esc(STATE.data_dir)}">${esc(shortPath(STATE.data_dir, 30))}</span>`;
}
async function loadStrategies() { STRATS = await api("GET", "/api/strategies"); }

// ====================================================== job console
function setBusy(on, label) {
  JOB.active = on;
  document.body.classList.toggle("busy", on);
  $("#side-status").innerHTML = on ? `<span class="dot run"></span>${esc(label)}` : '<span class="dot"></span>Idle';
}
function consoleOpen(open) {
  $("#console").classList.toggle("open", open);
  document.body.classList.toggle("console-open", open);
}
$("#console-toggle").addEventListener("click", () => consoleOpen(!$("#console").classList.contains("open")));
$("#console-clear").addEventListener("click", () => { $("#console-log").textContent = ""; });

// starter() must resolve to {job: id}; returns the final job object (or null if it could not start).
async function runJob(starter, label) {
  if (JOB.active) { toast("Another job is still running.", true); return null; }
  setBusy(true, label);
  const log = $("#console-log");
  log.textContent = "";
  $("#console-title").textContent = label;
  $("#console-hint").textContent = "";
  $("#console-dot").className = "dot run";
  consoleOpen(true);
  let final = null;
  try {
    const { job } = await starter();
    let since = 0;
    for (;;) {
      const j = await api("GET", `/api/jobs/${job}?since=${since}`);
      if (j.log.length) { log.textContent += j.log.join("\n") + "\n"; log.scrollTop = log.scrollHeight; }
      since = j.next;
      if (j.status !== "running") { final = j; break; }
      await sleep(450);
    }
  } catch (e) { toast(e.message, true); log.textContent += "Error: " + e.message + "\n"; }
  setBusy(false);
  const ok = !!final && final.status === "done" && !(final.result && final.result.ok === false);
  $("#console-dot").className = "dot " + (ok ? "ok" : "bad");
  $("#console-title").textContent = label + (ok ? " - finished" : " - failed");
  if (final && final.status !== "done") toast(final.error || "Failed.", true);
  loadState().catch(() => {});
  return final;
}

// ============================================================== RUN
VIEW_IMPL.run = {
  build(root) {
    root.innerHTML = `
      <div class="panel">
        <div class="panel-body">
          <div class="field"><label for="run-strategy">Strategy</label><select id="run-strategy"></select></div>
          <div class="field"><label>Universe</label><div class="meta" id="run-meta"></div></div>
          <div class="field"><label>Engines</label><div class="checks" id="run-engines">${ENGINE_ORDER.map(e =>
            `<label><input type="checkbox" class="eng" value="${e}" checked>${ENGINE_LABELS[e]}</label>`).join("")}</div></div>
        </div>
        <div class="panel-foot">
          <button class="btn primary act" id="run-btn">${icon("run")}Run selected engines</button>
          <span class="hint" id="run-hint"></span>
        </div>
      </div>
      <div id="latest"></div>`;
    $("#run-strategy").addEventListener("change", () => this.meta());
    $("#run-btn").addEventListener("click", () => this.run());
  },
  async show() {
    await Promise.all([loadState(), loadStrategies()]);
    const sel = $("#run-strategy"), prev = PRESELECT || sel.value;
    PRESELECT = null;
    sel.innerHTML = STRATS.map(s => `<option value="${esc(s.key)}">${esc(s.key)}</option>`).join("");
    if (STRATS.some(s => s.key === prev)) sel.value = prev;
    this.meta();
    await this.latest();
  },
  meta() {
    const s = STRATS.find(x => x.key === $("#run-strategy").value), box = $("#run-meta");
    if (!s) { box.innerHTML = "No strategies yet. Create one in Strategies."; $("#run-btn").disabled = true; return; }
    $("#run-btn").disabled = false;
    const uni = s.universe.length
      ? `<code>UNIVERSE = { ${esc(s.universe.join(", "))} }</code> <span class="muted">${s.resolved} of ${s.universe.length} found</span>`
      : `<span>All <b>${s.resolved.toLocaleString()}</b> tickers in the data folder</span> <span class="muted">(UNIVERSE is empty)</span>`;
    const more = s.resolved > s.preview.length ? `<span class="muted">+${(s.resolved - s.preview.length).toLocaleString()} more</span>` : "";
    const compiled = s.compiled === "ok" ? '<span class="tag pos">compiled</span>'
      : s.compiled === "stale" ? '<span class="tag warn">compiled build outdated</span>' : '<span class="tag">not compiled</span>';
    box.innerHTML = `<div>${esc(s.description)}</div>
      <div class="row">${uni} ${compiled}</div>
      <div class="row">${s.preview.map(t => `<span class="tag mono">${esc(t)}</span>`).join("")} ${more}</div>
      ${s.missing.length ? `<div class="neg">No CSV for: ${esc(s.missing.join(", "))}</div>` : ""}`;
    $("#run-hint").textContent = s.compiled === "ok" ? "" : "The compiled engine will be built automatically before it runs.";
  },
  async run() {
    const strategy = $("#run-strategy").value, engines = $$(".eng:checked").map(c => c.value);
    if (!strategy) return toast("Pick a strategy first.", true);
    if (!engines.length) return toast("Select at least one engine.", true);
    const j = await runJob(() => api("POST", "/api/run", { strategy, engines }), `Run ${strategy}`);
    if (j && j.status === "done") { await loadStrategies(); this.meta(); await this.latest(); }
  },
  async latest() {
    const d = await api("GET", "/api/latest"), box = $("#latest");
    if (!d.entry) { box.innerHTML = '<div class="panel"><div class="empty">No runs yet. Choose a strategy and press <b>Run</b>.</div></div>'; return; }
    const e = d.entry, ran = ENGINE_ORDER.filter(k => e.engines[k]);
    const ref = e.engines[ran[0]].summary;
    const sigs = ran.map(k => [e.engines[k].summary["Closed trades"], e.engines[k].summary["Final portfolio value"]].join("|"));
    const agree = ran.length < 2 ? "" : sigs.every(s => s === sigs[0])
      ? '<span class="tag pos">engines agree</span>' : '<span class="tag neg">engines disagree</span>';
    const times = {};
    ENGINE_ORDER.forEach(k => { if (e.engines[k]) times[k] = e.engines[k].elapsed; else if (d.comparison[k] !== undefined) times[k] = d.comparison[k]; });
    const maxT = Math.max(...Object.values(times), 1e-9), naive = times.naive;
    const rows = ENGINE_ORDER.map(k => {
      const t = times[k], inRun = !!e.engines[k], s = inRun ? e.engines[k].summary : null;
      if (t === undefined) return `<tr><td>${ENGINE_LABELS[k]}</td><td colspan="4" class="muted">not run yet</td></tr>`;
      const sp = k === "naive" ? "baseline" : (naive ? (naive / t).toFixed(1) + "x" : "-");
      return `<tr><td>${ENGINE_LABELS[k]}${inRun ? "" : ' <span class="tag">earlier run</span>'}</td>
        <td class="num"><span class="meter"><i style="width:${Math.max(2, t / maxT * 100)}%"></i></span>${fmtTime(t)}</td>
        <td class="num">${sp}</td><td class="num">${s ? esc(s["Closed trades"] ?? "-") : "-"}</td><td class="num">${s ? esc(s["Final portfolio value"] ?? "-") : "-"}</td></tr>`;
    }).join("");
    const tiles = SUMMARY_KEYS.map(k => `<div class="tile"><div class="k">${k}</div><div class="v ${k === "Total return" || k === "Max drawdown" ? signClass(ref[k]) : ""}">${esc(ref[k] ?? "-")}</div></div>`).join("");
    const files = ran.map(k => `<div class="file-group"><div class="k">${ENGINE_LABELS[k]}</div><div class="files">${d.files[k].map(f =>
      `<button class="btn sm" ${f.exists ? `data-s="${esc(e.strategy)}" data-e="${k}" data-f="${esc(f.name)}"` : "disabled"}>${icon("file")}${esc(f.name)}</button>`).join("")}</div></div>`).join("");
    const uni = e.universe.length ? `UNIVERSE { ${e.universe.join(", ")} }` : "all tickers";
    box.innerHTML = `
      <div class="panel">
        <div class="panel-head"><h2>Latest run &middot; <span class="mono">${esc(e.strategy)}</span></h2>
          <span class="sub">${esc(fmtDate(e.timestamp))} &middot; ${e.n_tickers.toLocaleString()} ticker(s) &middot; ${esc(uni.length > 60 ? uni.slice(0, 58) + "..." : uni)}</span></div>
        <div class="tiles">${tiles}</div>
        <div class="panel-body" style="padding-bottom:6px"><span class="muted small">${esc(e.description)}</span> ${agree}</div>
        <div class="tbl-wrap"><table><thead><tr><th>Engine</th><th class="num">Time</th><th class="num">vs naive</th><th class="num">Trades</th><th class="num">Final value</th></tr></thead><tbody>${rows}</tbody></table></div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Output files</h2><span class="sub">Click to show in the file explorer</span>
          <button class="btn sm" id="open-results">${icon("folder")}Results folder</button></div>
        <div class="panel-body">${files}</div>
      </div>`;
    $$("button[data-f]", box).forEach(b => b.addEventListener("click", () => reveal({ kind: "output", strategy: b.dataset.s, engine: b.dataset.e, file: b.dataset.f })));
    $("#open-results").addEventListener("click", () => reveal({ kind: "results_dir", strategy: e.strategy }));
  },
};

// ========================================================== HISTORY
VIEW_IMPL.history = {
  entries: [], tab: "runs",
  build(root) {
    root.innerHTML = `
      <div class="panel">
        <div class="tabs"><button data-t="runs" class="on">Runs</button><button data-t="log">Timing log</button></div>
        <div id="h-runs">
          <div class="panel-head"><select id="h-filter"><option value="">All strategies</option></select><span class="sub" id="h-count"></span>
            <span class="spacer"></span><button class="btn sm danger act" id="h-clear">Clear history</button></div>
          <div class="tbl-wrap" id="h-table"></div>
        </div>
        <div id="h-log" hidden>
          <div class="panel-head"><span class="sub mono" id="h-logpath"></span><span class="spacer"></span>
            <button class="btn sm" id="h-logopen">${icon("file")}Show in file explorer</button><button class="btn sm" id="h-logrefresh">Refresh</button></div>
          <pre class="logbox" id="h-logtext"></pre>
        </div>
      </div>`;
    $$(".tabs button", root).forEach(b => b.addEventListener("click", () => this.setTab(b.dataset.t)));
    $("#h-filter").addEventListener("change", () => this.render());
    $("#h-clear").addEventListener("click", async () => {
      if (!confirm("Delete the whole run history? (Output files and the timing log are kept.)")) return;
      try { await api("DELETE", "/api/history"); toast("History cleared."); this.show(); } catch (e) { toast(e.message, true); }
    });
    $("#h-logopen").addEventListener("click", () => reveal({ kind: "timing_log" }));
    $("#h-logrefresh").addEventListener("click", () => this.loadLog());
  },
  setTab(t) {
    this.tab = t;
    $$("#view-history .tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
    $("#h-runs").hidden = t !== "runs"; $("#h-log").hidden = t !== "log";
    if (t === "log") this.loadLog();
  },
  async show() {
    this.entries = await api("GET", "/api/history");
    const keep = $("#h-filter").value, names = [...new Set(this.entries.map(e => e.strategy))].sort();
    $("#h-filter").innerHTML = '<option value="">All strategies</option>' + names.map(n => `<option>${esc(n)}</option>`).join("");
    $("#h-filter").value = names.includes(keep) ? keep : "";
    this.render();
    if (this.tab === "log") this.loadLog();
  },
  render() {
    const f = $("#h-filter").value, rows = this.entries.filter(e => !f || e.strategy === f);
    $("#h-count").textContent = `${rows.length} run(s)`;
    if (!rows.length) { $("#h-table").innerHTML = '<div class="empty">No runs recorded yet.</div>'; return; }
    $("#h-table").innerHTML = `<table><thead><tr><th>When</th><th>Strategy</th><th class="num">Tickers</th>${
      ENGINE_ORDER.map(k => `<th class="num">${ENGINE_LABELS[k]}</th>`).join("")}<th class="num">Trades</th><th class="num">Final value</th></tr></thead><tbody>${
      rows.map((e, i) => {
        const ran = ENGINE_ORDER.filter(k => e.engines[k]), first = e.engines[ran[0]].summary, naive = e.engines.naive?.elapsed;
        const cell = k => {
          const x = e.engines[k]; if (!x) return '<td class="num muted">-</td>';
          const sp = k !== "naive" && naive ? ` <span class="muted">${(naive / x.elapsed).toFixed(1)}x</span>` : "";
          return `<td class="num">${fmtTime(x.elapsed)}${sp}</td>`;
        };
        const uni = e.universe.length ? e.universe.join(", ") : "all";
        return `<tr class="hrow" data-i="${i}" style="cursor:pointer"><td>${esc(fmtDate(e.timestamp))}</td><td><b>${esc(e.strategy)}</b></td>
          <td class="num" title="${esc(uni)}">${e.n_tickers.toLocaleString()}</td>${ENGINE_ORDER.map(cell).join("")}
          <td class="num">${esc(first["Closed trades"] ?? "-")}</td><td class="num">${esc(first["Final portfolio value"] ?? "-")}</td></tr>
          <tr id="hd${i}" hidden><td colspan="${6 + ENGINE_ORDER.length - 1}" style="background:var(--surface-2)">${this.detail(e, ran)}</td></tr>`;
      }).join("")}</tbody></table>`;
    $$(".hrow", $("#h-table")).forEach(r => r.addEventListener("click", () => { const d = $("#hd" + r.dataset.i); d.hidden = !d.hidden; }));
    $$(".openres", $("#h-table")).forEach(b => b.addEventListener("click", ev => { ev.stopPropagation(); reveal({ kind: "results_dir", strategy: b.dataset.s }); }));
  },
  detail(e, ran) {
    const body = SUMMARY_KEYS.map(m => `<tr><td>${m}</td>${ran.map(k => {
      const v = e.engines[k].summary[m] ?? "-";
      return `<td class="num ${m === "Total return" || m === "Max drawdown" ? signClass(v) : ""}">${esc(v)}</td>`;
    }).join("")}</tr>`).join("");
    const tk = e.tickers && e.tickers.length ? `Tickers: ${esc(e.tickers.slice(0, 30).join(", "))}${e.n_tickers > 30 ? ", ..." : ""}` : "";
    return `<div class="small muted" style="margin-bottom:8px">${esc(e.description)} &middot; started from ${esc(e.source)} &middot; id ${esc(e.id)}<br>${tk}</div>
      <table style="max-width:680px;background:var(--surface);border:1px solid var(--line)"><thead><tr><th>Metric</th>${ran.map(k => `<th class="num">${ENGINE_LABELS[k]}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>
      <div style="margin-top:10px"><button class="btn sm openres" data-s="${esc(e.strategy)}">${icon("folder")}Results folder</button></div>`;
  },
  async loadLog() {
    const d = await api("GET", "/api/timing_log");
    $("#h-logpath").textContent = d.path;
    $("#h-logtext").textContent = d.exists ? d.text : "No timing log yet. Run a strategy first.";
    $("#h-logopen").disabled = !d.exists;
  },
};

// ====================================================== STRATEGIES
VIEW_IMPL.strategies = {
  editor: { mode: null, key: null, original: "" }, cache: {}, valTimer: null, valSeq: 0,
  build(root) {
    root.innerHTML = `
      <div class="split">
        <div class="panel">
          <div class="panel-head"><input type="search" id="st-filter" class="input-grow" placeholder="Filter strategies"><button class="btn sm primary" id="st-new">New</button></div>
          <div class="list" id="st-list"></div>
        </div>
        <div>
          <div class="panel">
            <div class="empty" id="st-empty">Select a strategy to view or edit it, or create a new one.</div>
            <div id="st-editor" hidden>
              <div class="panel-head">
                <input type="text" id="st-name" class="input-grow" placeholder="strategy_name  (letters, digits, underscores)" autocomplete="off">
                <span id="st-chips"></span>
              </div>
              <div class="panel-body">
                <textarea id="st-text" rows="19" spellcheck="false"></textarea>
                <div class="status" id="st-status"></div>
                <div class="row">
                  <button class="btn primary act" id="st-save">Save</button>
                  <button class="btn act" id="st-revert">Revert</button>
                  <button class="btn act" id="st-compile">${icon("compiler")}Compile</button>
                  <button class="btn" id="st-run">${icon("run")}Run</button>
                  <span class="spacer"></span>
                  <button class="btn danger act" id="st-delete">Delete</button>
                </div>
                <div class="sample" id="st-sample">
                  <h3>Sample from an existing strategy</h3>
                  <div class="row"><select id="st-sample-sel"></select><button class="btn sm" id="st-sample-copy">Copy into editor</button></div>
                  <pre class="code" id="st-sample-text"></pre>
                </div>
              </div>
            </div>
          </div>
          <div class="panel">
            <div class="panel-head"><h2>DSL quick reference</h2></div>
            <div class="panel-body"><div class="ref" id="st-ref"></div></div>
          </div>
        </div>
      </div>`;
    $("#st-filter").addEventListener("input", () => this.list());
    $("#st-new").addEventListener("click", () => this.create());
    $("#st-sample-sel").addEventListener("change", () => this.sample());
    $("#st-sample-copy").addEventListener("click", async () => {
      const k = $("#st-sample-sel").value; if (!k) return;
      $("#st-text").value = await this.text(k); this.validate();
    });
    $("#st-revert").addEventListener("click", () => { $("#st-text").value = this.editor.original; this.validate(); });
    const soon = () => { clearTimeout(this.valTimer); this.valTimer = setTimeout(() => this.validate(), 400); };
    $("#st-text").addEventListener("input", soon); $("#st-name").addEventListener("input", soon);
    $("#st-text").addEventListener("keydown", ev => {            // Tab inserts spaces instead of leaving the field
      if (ev.key !== "Tab") return;
      ev.preventDefault();
      const t = ev.target, s = t.selectionStart;
      t.value = t.value.slice(0, s) + "    " + t.value.slice(t.selectionEnd);
      t.selectionStart = t.selectionEnd = s + 4;
    });
    $("#st-save").addEventListener("click", () => this.save());
    $("#st-delete").addEventListener("click", () => this.remove());
    $("#st-run").addEventListener("click", () => { PRESELECT = this.editor.key; location.hash = "run"; });
    $("#st-compile").addEventListener("click", async () => {
      const k = this.editor.key;
      await runJob(() => api("POST", "/api/compile", { strategy: k }), `Compile ${k}`);
      await this.refresh(); if (this.editor.key === k) this.open(k);
    });
    api("GET", "/api/reference").then(d => {
      $("#st-text").placeholder = d.skeleton;
      const sec = (t, items) => `<h3>${t}</h3><dl>${items.map(i => `<dt><code>${esc(i.code)}</code></dt><dd>${esc(i.text)}</dd>`).join("")}</dl>`;
      $("#st-ref").innerHTML = `<div>${sec("Statements", d.reference.config)}</div><div>${sec("Functions", d.reference.functions)}${sec("Operators and rules", d.reference.operators)}</div>
        <p class="small muted" style="grid-column:1/-1;margin-top:12px">${esc(d.reference.notes)}</p>`;
    }).catch(() => {});
  },
  async show() { await this.refresh(); },
  async refresh() { await loadStrategies(); this.list(); },
  list() {
    const q = $("#st-filter").value.trim().toLowerCase(), box = $("#st-list");
    const rows = STRATS.filter(s => !q || s.key.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));
    if (!rows.length) { box.innerHTML = '<div class="empty">No strategies.</div>'; return; }
    box.innerHTML = rows.map(s => `<button class="list-item ${this.editor.mode === "edit" && this.editor.key === s.key ? "sel" : ""}" data-k="${esc(s.key)}">
      <b>${esc(s.key)}</b><span class="d">${esc(s.description)}</span>
      <span class="tag">${s.universe.length ? "UNIVERSE " + esc(s.universe.join(", ")) : "all tickers"}</span>
      ${s.compiled === "ok" ? '<span class="tag pos">compiled</span>' : s.compiled === "stale" ? '<span class="tag warn">outdated</span>' : ""}</button>`).join("");
    $$(".list-item", box).forEach(el => el.addEventListener("click", () => this.open(el.dataset.k)));
  },
  async text(key) {
    if (!this.cache[key]) this.cache[key] = (await api("GET", "/api/strategies/" + encodeURIComponent(key))).text;
    return this.cache[key];
  },
  reveal() { $("#st-empty").hidden = true; $("#st-editor").hidden = false; },
  async open(key) {
    try {
      delete this.cache[key];
      const d = await api("GET", "/api/strategies/" + encodeURIComponent(key));
      this.cache[key] = d.text;
      this.editor = { mode: "edit", key, original: d.text };
      this.reveal();
      $("#st-name").value = key; $("#st-name").disabled = true;
      $("#st-text").value = d.text;
      for (const id of ["st-revert", "st-compile", "st-run", "st-delete"]) $("#" + id).hidden = false;
      $("#st-sample").hidden = true;
      $("#st-chips").innerHTML = d.compiled === "ok" ? '<span class="tag pos">compiled</span>' : d.compiled === "stale" ? '<span class="tag warn">compiled build outdated</span>' : '<span class="tag">not compiled</span>';
      this.list(); this.validate();
    } catch (e) { toast(e.message, true); }
  },
  async create() {
    this.editor = { mode: "new", key: null, original: "" };
    this.reveal();
    $("#st-name").value = ""; $("#st-name").disabled = false; $("#st-text").value = "";
    for (const id of ["st-revert", "st-compile", "st-run", "st-delete"]) $("#" + id).hidden = true;
    $("#st-chips").innerHTML = '<span class="tag accent">new</span>';
    $("#st-sample").hidden = false;
    const sel = $("#st-sample-sel");
    sel.innerHTML = STRATS.map(s => `<option value="${esc(s.key)}">${esc(s.key)}</option>`).join("");
    if (STRATS.some(s => s.key === "crossover_rsi")) sel.value = "crossover_rsi";
    await this.sample(); this.list(); this.validate(); $("#st-name").focus();
  },
  async sample() {
    const k = $("#st-sample-sel").value;
    $("#st-sample-text").textContent = k ? await this.text(k) : "(no strategies to sample)";
  },
  async validate() {
    const text = $("#st-text").value, el = $("#st-status"), seq = ++this.valSeq;
    if (!text.trim()) { el.className = "status muted"; el.textContent = "Write a strategy, or copy the sample below. The grey placeholder shows the minimal shape."; return; }
    try {
      const r = await api("POST", "/api/validate", { text, name: $("#st-name").value || "new_strategy" });
      if (seq !== this.valSeq) return;
      if (r.ok) { el.className = "status ok"; el.textContent = `Valid. Columns: ${r.columns.join(", ") || "-"}. Universe: ${r.universe.length ? r.universe.join(", ") : "all tickers"}.`; }
      else { el.className = "status err"; el.innerHTML = "Errors:<ul>" + r.errors.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>"; }
    } catch (e) { el.className = "status err"; el.textContent = e.message; }
  },
  async save() {
    const name = $("#st-name").value.trim(), text = $("#st-text").value, ed = this.editor;
    try {
      if (ed.mode === "new") await api("POST", "/api/strategies", { name, text });
      else await api("PUT", "/api/strategies/" + encodeURIComponent(ed.key), { text });
      delete this.cache[name];
      toast(`Saved ${name}.`);
      await this.refresh(); await this.open(name);
    } catch (e) {
      const el = $("#st-status");
      el.className = "status err";
      el.innerHTML = esc(e.message) + (e.data?.errors ? "<ul>" + e.data.errors.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>" : "");
      toast(e.message, true);
    }
  },
  async remove() {
    const k = this.editor.key;
    if (!confirm(`Delete strategy '${k}'? Its .valpha file and compiled binary are removed (results and history are kept).`)) return;
    try {
      await api("DELETE", "/api/strategies/" + encodeURIComponent(k));
      delete this.cache[k]; toast(`Deleted ${k}.`);
      this.editor = { mode: null, key: null, original: "" };
      $("#st-editor").hidden = true; $("#st-empty").hidden = false;
      await this.refresh();
    } catch (e) { toast(e.message, true); }
  },
};

// ========================================================= TICKERS
VIEW_IMPL.tickers = {
  rows: [], matched: 0, total: 0, q: "", timer: null, seq: 0,
  build(root) {
    root.innerHTML = `
      <div class="panel">
        <div class="panel-head"><input type="search" id="tk-q" class="input-grow" placeholder="Search tickers">
          <span class="sub" id="tk-count"></span><button class="btn sm" id="tk-dir">${icon("folder")}Data folder</button></div>
        <div class="tbl-wrap" id="tk-table"></div>
        <div class="panel-foot" id="tk-foot">
          <span class="hint" id="tk-shown"></span><span class="spacer"></span>
          <button class="btn sm" id="tk-more">Load 100 more</button><button class="btn sm" id="tk-all">Load all</button>
        </div>
      </div>`;
    $("#tk-q").addEventListener("input", () => { clearTimeout(this.timer); this.timer = setTimeout(() => { this.q = $("#tk-q").value; this.load(true); }, 250); });
    $("#tk-more").addEventListener("click", () => this.load(false, 100));
    $("#tk-all").addEventListener("click", () => this.load(false, "all"));
    $("#tk-dir").addEventListener("click", () => reveal({ kind: "data_dir" }));
  },
  async show() { await loadState(); await this.load(true); },
  async load(reset, limit = 100) {
    const seq = ++this.seq, offset = reset ? 0 : this.rows.length;
    const d = await api("GET", `/api/tickers?offset=${offset}&limit=${limit}&q=${encodeURIComponent(this.q)}`);
    if (seq !== this.seq) return;
    this.rows = reset ? d.rows : this.rows.concat(d.rows);
    this.matched = d.matched; this.total = d.total;
    this.render();
  },
  render() {
    const box = $("#tk-table");
    $("#tk-count").textContent = this.q ? `${this.matched.toLocaleString()} of ${this.total.toLocaleString()} match` : `${this.total.toLocaleString()} ticker(s)`;
    if (!this.total) { box.innerHTML = '<div class="empty">No ticker CSVs in the data folder. Add files, choose another folder, or generate demo data in <a href="#data">Data</a>.</div>'; $("#tk-foot").hidden = true; return; }
    $("#tk-foot").hidden = false;
    if (!this.rows.length) box.innerHTML = '<div class="empty">No ticker matches your search.</div>';
    else box.innerHTML = `<table><thead><tr><th>File</th><th>First bar</th><th>Last bar</th><th class="num">Size</th></tr></thead><tbody>${
      this.rows.map(t => `<tr><td><button class="link" data-t="${esc(t.ticker)}" title="Show in file explorer">${esc(t.ticker)}.csv</button></td><td class="mono small">${esc(t.first || "-")}</td><td class="mono small">${esc(t.last || "-")}</td><td class="num">${fmtBytes(t.size)}</td></tr>`).join("")}</tbody></table>`;
    $$(".link[data-t]", box).forEach(b => b.addEventListener("click", () => reveal({ kind: "ticker", ticker: b.dataset.t })));
    const left = this.matched - this.rows.length;
    $("#tk-shown").textContent = `Showing ${this.rows.length.toLocaleString()} of ${this.matched.toLocaleString()}`;
    $("#tk-more").hidden = left <= 0; $("#tk-all").hidden = left <= 0;
    $("#tk-more").textContent = `Load ${Math.min(100, left)} more`;
    $("#tk-all").textContent = `Load all (${left.toLocaleString()} more)`;
  },
};

// ========================================================= COMPILER
VIEW_IMPL.compiler = {
  build(root) {
    root.innerHTML = `
      <div class="panel">
        <div class="panel-head"><h2>Toolchain</h2></div>
        <div class="panel-body"><div class="kv" id="cp-kv"></div></div>
        <div class="panel-foot">
          <button class="btn primary act" id="cp-pending">Compile outdated &amp; missing</button>
          <button class="btn act" id="cp-rebuild">Rebuild everything</button>
          <span class="hint">Rebuild recompiles valphac and every strategy from scratch.</span>
        </div>
      </div>
      <div class="panel"><div class="panel-head"><h2>Strategies</h2><span class="sub" id="cp-count"></span></div><div class="tbl-wrap" id="cp-table"></div></div>`;
    $("#cp-pending").addEventListener("click", () => this.job({}, "Compile outdated and missing"));
    $("#cp-rebuild").addEventListener("click", () => this.job({ rebuild: true }, "Rebuild everything"));
  },
  async job(body, label) {
    await runJob(() => api("POST", "/api/compile", body), label);
    await this.show();
  },
  async show() { await Promise.all([loadState(), loadStrategies()]); this.render(); },
  render() {
    const c = STATE.compiler;
    $("#cp-kv").innerHTML = `
      <div class="k">C++ compiler</div><div>${c.cxx_found ? `<span class="tag pos">found</span> <span class="mono small">${esc(c.cxx_version || c.cxx)}</span>` : `<span class="tag neg">'${esc(c.cxx)}' not on PATH</span> <span class="small muted">install g++ (Windows: MSYS2 mingw-w64) and restart</span>`}</div>
      <div class="k">valphac</div><div>${c.valphac ? '<span class="tag pos">built</span>' : '<span class="tag warn">not built</span> <span class="small muted">built automatically on first compile</span>'}</div>
      <div class="k">Strategies compiled</div><div><b>${c.built}</b> of ${STATE.n_strategies}</div>`;
    $("#cp-count").textContent = `${STRATS.length} strategies`;
    $("#cp-table").innerHTML = `<table><thead><tr><th>Strategy</th><th>Status</th><th>Compiled at</th><th class="num"></th></tr></thead><tbody>${STRATS.map(s => `<tr>
      <td><b>${esc(s.key)}</b><div class="small muted">${esc(s.description)}</div></td>
      <td>${s.compiled === "ok" ? '<span class="tag pos">up to date</span>' : s.compiled === "stale" ? '<span class="tag warn">outdated</span>' : '<span class="tag">not compiled</span>'}</td>
      <td class="small muted">${s.compiled_at ? esc(fmtDate(s.compiled_at)) : "-"}</td>
      <td class="num"><button class="btn sm act" data-k="${esc(s.key)}">${s.compiled === "ok" ? "Recompile" : "Compile"}</button></td></tr>`).join("")}</tbody></table>`;
    $$("button[data-k]", $("#cp-table")).forEach(b => b.addEventListener("click", () => this.job({ strategy: b.dataset.k }, `Compile ${b.dataset.k}`)));
  },
};

// ========================================================== CHECKS
VIEW_IMPL.checks = {
  results: {},
  build(root) {
    const card = (id, title, text) => `<div class="panel"><div class="panel-head"><h2>${title}</h2><span id="ck-${id}-res"></span></div>
      <div class="panel-body"><p class="muted">${text}</p></div>
      <div class="panel-foot"><button class="btn primary act" data-c="${id}">${icon("run")}Run check</button><span class="hint">Output streams into the console below.</span></div></div>`;
    root.innerHTML =
      card("sanity", "IO-spec sanity check", "Runs the hand-computed STOCKA / STOCKB worked example on every available engine (naive, pandas, and compiled if built) and compares each with the spec's answer key.") +
      card("smoke", "End-to-end smoke test", "Writes two tiny hardcoded tickers and a strategy to a temporary folder, runs all engines, and validates signals, trades, P&amp;L, equity curve, summary and indicator columns against values worked out by hand. Never touches your data or results.");
    $$("button[data-c]", root).forEach(b => b.addEventListener("click", () => this.run(b.dataset.c)));
  },
  async show() { this.paint(); },
  paint() {
    for (const id of ["sanity", "smoke"]) {
      const r = this.results[id];
      $(`#ck-${id}-res`).innerHTML = !r ? "" : r.ok ? '<span class="tag pos">passed</span>' : '<span class="tag neg">failed</span>';
    }
  },
  async run(id) {
    const j = await runJob(() => api("POST", "/api/check", { name: id }), id === "sanity" ? "IO-spec sanity check" : "Smoke test");
    if (j) { this.results[id] = { ok: j.status === "done" && j.result && j.result.ok }; this.paint(); }
  },
};

// ============================================================ DATA
VIEW_IMPL.data = {
  build(root) {
    root.innerHTML = `
      <div class="panel">
        <div class="panel-head"><h2>Data folder</h2></div>
        <div class="panel-body">
          <div class="kv" style="margin-bottom:16px"><div class="k">Active folder</div><div class="mono small" id="dt-dir"></div>
            <div class="k">Ticker CSVs</div><div id="dt-n"></div><div class="k">Default</div><div class="mono small muted" id="dt-def"></div></div>
          <div class="field"><label for="dt-path">Use folder</label>
            <div class="row"><input type="text" id="dt-path" class="input-grow" placeholder="C:\\path\\to\\data  (CSVs directly inside, or one subfolder deep)"><button class="btn primary act" id="dt-apply">Use this folder</button></div></div>
        </div>
        <div class="panel-foot"><button class="btn" id="dt-open">${icon("folder")}Open in file explorer</button><button class="btn act" id="dt-reset">Reset to default</button>
          <span class="hint">The choice is remembered across restarts.</span></div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Synthetic demo data</h2></div>
        <div class="panel-body"><p class="muted" style="margin-bottom:12px">Generates reproducible random-walk price files in <code>data/synthetic_demo</code> and switches every engine to them. Your own data is never modified.</p>
          <div class="row"><label for="dt-n-gen">Tickers</label><input type="number" id="dt-n-gen" value="40" min="1" max="5000" style="width:90px">
            <label for="dt-bars">Bars each</label><input type="number" id="dt-bars" value="1500" min="1" max="5000" style="width:90px">
            <button class="btn act" id="dt-gen">Generate</button></div></div>
      </div>`;
    $("#dt-apply").addEventListener("click", () => this.apply({ path: $("#dt-path").value }));
    $("#dt-reset").addEventListener("click", () => this.apply({ reset: true }));
    $("#dt-open").addEventListener("click", () => reveal({ kind: "data_dir" }));
    $("#dt-gen").addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/data/generate", { n: +$("#dt-n-gen").value, bars: +$("#dt-bars").value });
        toast(`Generated ${r.n_tickers} synthetic tickers.`); await this.show();
      } catch (e) { toast(e.message, true); }
    });
  },
  async apply(body) {
    try { await api("POST", "/api/data/folder", body); toast(body.reset ? "Reset to the default data folder." : "Data folder changed."); await this.show(); }
    catch (e) { toast(e.message, true); }
  },
  async show() {
    await loadState();
    $("#dt-dir").textContent = STATE.data_dir; $("#dt-def").textContent = STATE.default_data_dir;
    $("#dt-n").innerHTML = STATE.n_tickers ? `<b>${STATE.n_tickers.toLocaleString()}</b>` : '<span class="tag warn">none found</span>';
  },
};

// ============================================================= init
(async function init() {
  try { await loadState(); } catch (e) { toast(e.message, true); }
  route();
})();
