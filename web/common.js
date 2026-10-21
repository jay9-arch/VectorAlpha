// Shared helpers for the VectorAlpha UI.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(method, url, body) {
  const opts = { method, headers: { "X-Valpha": "1" } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) { const err = new Error(data.error || res.statusText); err.data = data; err.status = res.status; throw err; }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer;
function toast(msg, isErr) {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.className = "toast show" + (isErr ? " err" : "");
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), isErr ? 6500 : 2800);
}

function fmtTime(s) {
  if (s === undefined || s === null || isNaN(s)) return "-";
  if (s < 1) return (s * 1000).toFixed(s < 0.01 ? 2 : 1) + " ms";
  return s.toFixed(3) + " s";
}
function fmtDate(iso) { const d = new Date(iso); return isNaN(d) ? (iso || "-") : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
function fmtBytes(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
function signClass(text) { const v = parseFloat(text); return isNaN(v) || v === 0 ? "" : v > 0 ? "pos" : "neg"; }
function shortPath(p, n = 34) { p = p || ""; return p.length <= n ? p : "..." + p.slice(-(n - 3)); }

// 16px line icons (stroke = currentColor)
const ICON = {
  run: '<path d="M5 3.2v9.6L12.8 8z"/>',
  history: '<circle cx="8" cy="8" r="6"/><path d="M8 4.6V8l2.4 1.6"/>',
  strategies: '<path d="M3.5 2h6l3 3v9h-9z"/><path d="M9.5 2v3h3M5.5 8.5h5M5.5 11h3.5"/>',
  tickers: '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
  compiler: '<rect x="4" y="4" width="8" height="8" rx="1"/><path d="M6.5 1.5V4M9.5 1.5V4M6.5 12v2.5M9.5 12v2.5M1.5 6.5H4M1.5 9.5H4M12 6.5h2.5M12 9.5h2.5"/>',
  checks: '<circle cx="8" cy="8" r="6"/><path d="M5.4 8.2l1.8 1.8 3.4-3.6"/>',
  data: '<ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
  file: '<path d="M4 2h5l3 3v9H4z"/><path d="M9 2v3h3"/>',
  folder: '<path d="M2 4.5h4l1.5 1.5H14v7H2z"/>',
  chev: '<path d="M4 6.5l4 4 4-4"/>',
};
function icon(name) {
  return `<svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name] || ""}</svg>`;
}

// Theme: light unless the user switches it (the OS setting is deliberately ignored).
function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("vp-theme", t); } catch (e) { /* storage blocked */ }
  $$(".theme button").forEach(b => b.classList.toggle("on", b.dataset.theme === t));
}
setTheme((() => { try { return localStorage.getItem("vp-theme") === "dark" ? "dark" : "light"; } catch (e) { return "light"; } })());

// Opens a file in the OS file explorer (the server resolves the path; the browser never sends one).
async function reveal(spec) {
  try { const r = await api("POST", "/api/reveal", spec); toast("Opened in file explorer: " + r.path); }
  catch (e) { toast(e.message, true); }
}

const ENGINE_LABELS = { naive: "Naive Python", vectorized: "pandas / NumPy", compiled: "VectorAlpha (compiled)" };
const ENGINE_ORDER = ["naive", "vectorized", "compiled"];
const SUMMARY_KEYS = ["Final portfolio value", "Total return", "Max drawdown", "Closed trades", "Win rate"];
