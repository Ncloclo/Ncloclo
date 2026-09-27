// Panneau TrendGuard : interface. Données : API JSON du serveur local
// (panel/server.py). Tout texte venant des données passe par textContent.
(() => {
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const LWC = window.LightweightCharts;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

// ---------- Formats ----------
const nfCache = new Map();
const nf = (d) => {
  if (!nfCache.has(d)) nfCache.set(d, new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  return nfCache.get(d);
};
const fpx = (v) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return nf(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 3 : a >= 0.1 ? 4 : 5).format(v);
};
const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
const fusd = (v) => (v == null || !isFinite(v) ? "–" : nf(2).format(v) + " USDT");
const fpct = (v, d = 2) => (v == null || !isFinite(v) ? "–" : sign(v) + nf(d).format(Math.abs(v)) + " %");
const fR = (v) => (v == null || !isFinite(v) ? "–" : sign(v) + nf(2).format(Math.abs(v)) + " R");
const fvol = (v) => (v == null ? "–" : v >= 1e9 ? nf(2).format(v / 1e9) + " Md$" : nf(1).format(v / 1e6) + " M$");
const up = (s) => (s || "").toUpperCase();
const dShort = new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit" });
const fdate = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? dShort.format(d) : "–";
};
const fdur = (s) => {
  if (s == null) return "–";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min ${String(sec).padStart(2, "0")}`;
};
const fage = (s) => (s == null ? "–" : s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);
const REASON = { STOP: "stop de clôture", EXCHANGE_STOP: "stop catastrophe", DELISTED: "retrait de la cote", STOP_LATE: "stop (rattrapage)" };

// ---------- Préférences locales ----------
const prefs = {
  get(k, d) { try { const v = localStorage.getItem("tg:" + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tg:" + k, v); } catch { /* stockage bloqué */ } },
};

// ---------- API ----------
async function api(path, opts = {}) {
  const init = { headers: { "X-TrendGuard": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (res.status === 401) { showLogin(); throw new Error("connexion requise"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, kind = "") {
  const t = el("div", "toast " + kind, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 320); }, 4500);
}

function ripple(ev, btn) {
  if (reduceMotion.matches) return;
  const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height);
  const s = el("span", "ripple");
  Object.assign(s.style, { width: size + "px", height: size + "px", left: (ev.clientX - r.left - size / 2) + "px", top: (ev.clientY - r.top - size / 2) + "px" });
  btn.append(s);
  setTimeout(() => s.remove(), 650);
}

function countUp(node, value, fmt) {
  const prev = Number(node.dataset.v);
  node.dataset.v = value;
  if (value == null || !isFinite(value) || reduceMotion.matches) { node.textContent = fmt(value); return; }
  const from = isFinite(prev) ? prev : 0;
  if (from === value) { node.textContent = fmt(value); return; }
  const t0 = performance.now(), dur = isFinite(prev) ? 600 : 1100;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(from + (value - from) * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- Thème ----------
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function applyTheme(mode) {
  if (mode === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  prefs.set("theme", mode);
  $$("[data-theme-set]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === mode)));
  $('meta[name="theme-color"]').setAttribute("content", cssVar("--bg"));
  charts.forEach((c) => c.applyOptions(chartOptions({}, c.tgLogo)));
}
$("#theme-btn").addEventListener("click", () => {
  const dark = cssVar("color-scheme") !== "light";
  applyTheme(dark ? "light" : "dark");
});
$$("[data-theme-set]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themeSet)));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => charts.forEach((c) => c.applyOptions(chartOptions({}, c.tgLogo))));

// ---------- Graphiques (TradingView Lightweight Charts) ----------
const charts = new Set();
function chartOptions(extra = {}, logo = true) {
  return Object.assign({
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: cssVar("--ink-2"), fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: logo },
    grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
    crosshair: { mode: LWC ? LWC.CrosshairMode.Normal : 0 },
    localization: { locale: "fr-FR", priceFormatter: fpx },
  }, extra);
}
// Logo TradingView (crédit de la bibliothèque) sur les graphiques détaillés ;
// sur les petits graphiques, le crédit figure dans le menu.
function makeChart(box, extra, logo = true) {
  if (!LWC) { box.textContent = "Bibliothèque de graphiques introuvable."; return null; }
  const c = LWC.createChart(box, chartOptions(extra, logo));
  c.tgLogo = logo;
  charts.add(c);
  return c;
}
function dropChart(c) {
  if (c) { charts.delete(c); c.remove(); }
}
const COLORS = () => ({ up: cssVar("--up"), down: cssVar("--down"), accent: cssVar("--accent"), ink: cssVar("--ink"), muted: cssVar("--muted"), warn: cssVar("--warn"), sma: "#eb6834" });
const uniq = (pts) => {                 // temps strictement croissants
  const out = [];
  pts.forEach((p) => { if (!out.length || p.time > out[out.length - 1].time) out.push(p); else out[out.length - 1] = p; });
  return out;
};
const candleData = (rows) => uniq(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })));
function addPositionLines(series, pos) {
  if (!pos || !LWC) return [];
  const c = COLORS(), lines = [];
  const add = (price, color, style, title) => {
    if (price != null) lines.push(series.createPriceLine({ price, color, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
  };
  add(pos.entry, c.accent, LWC.LineStyle.Dashed, "Entrée");
  add(pos.stop, c.down, LWC.LineStyle.Solid, "Stop");
  add(pos.disaster, c.down, LWC.LineStyle.Dotted, "Catastrophe");
  // L'échelle automatique suit les bougies seulement : l'élargir pour que
  // l'entrée et les stops (souvent 10 % plus bas) restent visibles.
  const levels = [pos.entry, pos.stop, pos.disaster].filter((v) => v != null && isFinite(v));
  series.applyOptions({ autoscaleInfoProvider: (base) => {
    const r = base();
    if (!r || !levels.length) return r;
    r.priceRange.minValue = Math.min(r.priceRange.minValue, ...levels);
    r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ...levels);
    return r;
  } });
  return lines;
}
function legend(box, items) {
  box.replaceChildren(...items.map(([color, label, dashed]) => {
    const s = el("span");
    const i = el("i", dashed ? "dash" : "");
    i.style.background = dashed ? "" : color;
    if (dashed) i.style.color = color;
    s.append(i, label);
    return s;
  }));
}

// ---------- Navigation ----------
const TABS = ["dash", "charts", "assets", "positions", "watch", "log", "settings"];
let current = null;
function route() {
  const t = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "dash";
  if (t === current) return;
  current = t;
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + t));
  $$(".tab").forEach((a) => {
    if (a.dataset.tab === t) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#page-title").textContent = $("#page-" + t).dataset.title;
  window.scrollTo({ top: 0 });
  refreshTab(true);
}
window.addEventListener("hashchange", route);
$$("[data-go]").forEach((b) => b.addEventListener("click", () => { location.hash = "#" + b.dataset.go; }));

// ---------- État du bot et bouton AUTO ----------
let S = null, lastOk = 0, nextDecisionAt = 0, statusErr = false;
async function refreshStatus() {
  try {
    S = await api("/api/status");
    lastOk = Date.now();
    statusErr = false;
    nextDecisionAt = Date.now() + S.next_decision_s * 1000;
    renderStatus();
  } catch (e) {
    statusErr = true;
    if (e.message !== "connexion requise") $("#updated").textContent = "Panneau injoignable";
  }
}
function renderStatus() {
  const pill = $("#st-pill"), btn = $("#auto-btn");
  const label = { running: "En marche", stopped: "Arrêté", starting: "Démarrage…", stopping: "Arrêt en cours…" }[S.state] || S.state;
  pill.className = "pill " + (S.state === "running" ? "running" : S.state === "stopped" ? "stopped" : "pending");
  pill.querySelector("span").textContent = S.halted ? "Arrêt d'urgence" : label;
  const mode = $("#mode-badge");
  mode.textContent = S.demo ? "DÉMO" : S.mode === "live" ? (S.testnet ? "RÉEL · TESTNET" : "RÉEL") : "PAPER";
  mode.className = "badge " + (S.mode === "live" ? "live" : "paper");
  $("#brand-mode").textContent = S.demo ? "Démonstration" : S.mode === "live" ? "Mode réel" : "Mode paper (argent fictif)";
  const running = S.state === "running", busy = S.state === "starting" || S.state === "stopping";
  btn.disabled = busy;
  btn.classList.toggle("is-running", running);
  btn.querySelector("use").setAttribute("href", running ? "#i-stop" : "#i-play");
  $("#auto-label").textContent = running ? "ARRÊTER" : "AUTO";
  $("#auto-sub").textContent = busy ? label : running ? "Automatisation en marche" : "Démarrer l'automatisation";
  btn.setAttribute("aria-label", running ? "Arrêter l'automatisation du bot" : "Démarrer l'automatisation du bot");
  $("#logout-btn").hidden = !S.password;
}
function confirmLive() {
  const dlg = $("#confirm");
  return new Promise((resolve) => {
    const done = (v) => { dlg.close(); resolve(v); };
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}
$("#auto-btn").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  ripple(ev, btn);
  if (!S) return;
  const running = S.state === "running";
  if (!running && S.mode === "live" && !S.demo && !(await confirmLive())) return;
  btn.disabled = true;
  try {
    const r = await api(running ? "/api/bot/stop" : "/api/bot/start", { body: {} });
    toast(r.message, r.ok ? "ok" : "err");
  } catch (e) {
    toast(e.message, "err");
  }
  await refreshStatus();
});

// ---------- Tableau de bord ----------
let dashChart = null, dashSeries = null;
async function renderDash() {
  if (!S) await refreshStatus();
  const [pos, eq] = await Promise.all([api("/api/positions"), api("/api/equity?days=30")]);
  countUp($("#d-equity"), S.equity, fusd);
  const sub = $("#d-equity-sub");
  if (S.equity && S.start_equity) {
    const ch = (S.equity / S.start_equity - 1) * 100;
    sub.textContent = `${fpct(ch)} depuis le départ (${fusd(S.start_equity)})`;
    sub.className = "sub " + (ch >= 0 ? "up" : "down");
  } else sub.textContent = "Aucune décision encore prise";
  if (!dashChart && LWC) {
    dashChart = makeChart($("#d-equity-chart"), { handleScroll: false, handleScale: false, rightPriceScale: { visible: false }, timeScale: { visible: false } }, false);
    const c = COLORS();
    dashSeries = dashChart.addAreaSeries({ lineColor: c.accent, topColor: c.accent + "55", bottomColor: c.accent + "05", lineWidth: 2, priceLineVisible: false });
  }
  if (dashSeries) {
    dashSeries.setData(uniq(eq.points.map((p) => ({ time: p.t, value: p.v }))));
    dashChart.timeScale().fitContent();
  }
  countUp($("#d-pos"), S.positions, (v) => `${Math.round(v)} / ${S.max_positions}`);
  $("#d-pos-sub").textContent = `${nf(1).format(S.risk_pct)} % du capital risqué par trade`;
  const reg = $("#d-regime");
  reg.textContent = S.regime_bull == null ? "—" : S.regime_bull ? "Haussier" : "Baissier";
  reg.className = S.regime_bull ? "up" : S.regime_bull === false ? "down" : "";
  $("#d-regime-sub").textContent = S.regime_bull ? "achats autorisés" : S.regime_bull === false ? "aucun achat, stops resserrés" : "";
  const dd = $("#d-dd");
  dd.textContent = S.drawdown_pct == null ? "—" : fpct(S.drawdown_pct);
  dd.className = "num " + (S.drawdown_pct < -10 ? "down" : "");
  $("#d-dd-sub").textContent = `arrêt d'urgence à −${nf(0).format(S.kill_drawdown_pct)} %`;
  $("#d-cycle").textContent = S.last_cycle_age_s == null ? "—" : "il y a " + fage(S.last_cycle_age_s);
  $("#d-cycle-sub").textContent = S.last_decision_day ? `décision : bougie du ${fdate(S.last_decision_day)}` : "";
  const w = S.watch, wd = $("#d-watch");
  wd.textContent = !w ? "—" : w.sentiment > 0.2 ? "Positive" : w.sentiment < -0.2 ? "Négative" : "Neutre";
  wd.className = !w ? "" : w.sentiment > 0.2 ? "up" : w.sentiment < -0.2 ? "down" : "";
  $("#d-watch-sub").textContent = w ? (w.providers_total ? `${w.providers}/${w.providers_total} IA · ${w.day}` : `mots-clés · ${w.day}`) : "pas encore de rapport";

  const list = $("#d-positions");
  const rows = pos.positions.slice().sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0));
  if (!rows.length) list.replaceChildren(el("p", "empty", "Aucune position : capital à 100 % en USDT."));
  else list.replaceChildren(...rows.map((p) => {
    const r = el("div", "pos-row");
    r.tabIndex = 0;
    r.setAttribute("role", "button");
    r.setAttribute("aria-label", `Détail de ${up(p.asset)}`);
    const bar = el("div", "bar"), b = el("b");
    const k = Math.max(-1, Math.min(1, (p.pnl_pct || 0) / 15));
    Object.assign(b.style, { left: k >= 0 ? "50%" : (50 + k * 50) + "%", width: Math.abs(k) * 50 + "%", background: k >= 0 ? "var(--up)" : "var(--down)" });
    bar.append(b);
    const v = el("span", "num " + ((p.pnl_pct || 0) >= 0 ? "up" : "down"), fpct(p.pnl_pct, 1));
    r.append(el("strong", "", up(p.asset)), bar, v);
    const open = () => openDetail({ kind: "asset", asset: p.asset });
    r.addEventListener("click", open);
    r.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return r;
  }));

  const alerts = [];
  if (S.halted) alerts.push(["crit", `Arrêt d'urgence : ${S.halt_reason || ""}`]);
  if (S.state === "stopped") alerts.push(["warn", "Le bot est arrêté : cliquez sur AUTO pour reprendre l'automatisation."]);
  S.vetoes.forEach((v) => alerts.push(["crit", `${v.reason} : achats bloqués jusqu'au ${fdate(v.until)}`]));
  ((S.watch && S.watch.alerts) || []).forEach((a) => alerts.push(["warn", a]));
  if (pos.stale) alerts.push(["warn", "Cours Binance momentanément indisponibles : dernières valeurs affichées."]);
  if (!alerts.length) alerts.push(["ok", "Aucune alerte. Tout est normal."]);
  $("#d-alerts").replaceChildren(...alerts.map(([k, t]) => el("li", k, t)));
}

// ---------- Graphiques en temps réel ----------
const gridCharts = new Map();       // id → { chart, series, lines }
async function renderCharts() {
  const [eq, reg, pos] = await Promise.all([api("/api/equity?days=90"), api("/api/regime"), api("/api/positions")]);
  const c = COLORS();
  const specs = [
    { id: "equity", kind: "equity", title: "Capital du bot", legend: [[c.accent, "Capital (USDT)"], [c.muted, "Capital de départ", true]] },
    { id: "regime", kind: "regime", title: `Régime BTC · moyenne ${reg.sma} jours`, legend: [[c.ink, "BTC (clôture)"], [c.sma, `Moyenne ${reg.sma} j`]] },
    ...pos.positions.map((p) => ({ id: "asset:" + p.asset, kind: "asset", asset: p.asset, title: `${up(p.asset)}/USDT · 1 h`, pos: p,
      legend: [[c.up, "Bougies"], [c.accent, "Entrée", true], [c.down, "Stop de clôture"], [c.down, "Stop catastrophe", true]] })),
  ];
  const grid = $("#chart-grid");
  const ids = specs.map((s) => s.id).join("|");
  if (grid.dataset.ids !== ids) {
    gridCharts.forEach((g) => dropChart(g.chart));
    gridCharts.clear();
    grid.dataset.ids = ids;
    grid.replaceChildren(...specs.map((s, k) => {
      const card = el("article", "card chart-card");
      card.style.animationDelay = (k * 60) + "ms";
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `${s.title} : ouvrir le détail`);
      const head = el("div", "head");
      head.append(el("h2", "", s.title), el("span", "val", "…"));
      const lg = el("div", "legend");
      legend(lg, s.legend);
      const box = el("div", "chart-box");
      card.append(head, lg, box, el("span", "open-hint", "Cliquer pour le détail"));
      const open = () => openDetail(s);
      card.addEventListener("click", open);
      card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      const chart = makeChart(box, { handleScroll: false, handleScale: false }, false);
      const g = { chart, series: [], lines: [], card };
      if (chart) {
        if (s.kind === "equity") {
          g.series.push(chart.addBaselineSeries({ baseValue: { type: "price", price: (eq.points[0] || {}).v || 0 },
            topLineColor: c.up, topFillColor1: c.up + "44", topFillColor2: c.up + "05",
            bottomLineColor: c.down, bottomFillColor1: c.down + "05", bottomFillColor2: c.down + "44", lineWidth: 2, priceLineVisible: false }));
        } else if (s.kind === "regime") {
          g.series.push(chart.addLineSeries({ color: c.ink, lineWidth: 2, priceLineVisible: false }));
          g.series.push(chart.addLineSeries({ color: c.sma, lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false }));
        } else {
          g.series.push(chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false }));
        }
      }
      gridCharts.set(s.id, g);
      return card;
    }));
  }
  const setVal = (id, text, cls = "") => {
    const g = gridCharts.get(id);
    if (!g) return;
    const v = g.card.querySelector(".val");
    v.textContent = text;
    v.className = "val " + cls;
  };
  const ge = gridCharts.get("equity");
  if (ge && ge.series[0]) {
    ge.series[0].setData(uniq(eq.points.map((p) => ({ time: p.t, value: p.v }))));
    ge.chart.timeScale().fitContent();
  }
  const last = eq.points[eq.points.length - 1];
  setVal("equity", last ? fusd(last.v) : "—");
  const gr = gridCharts.get("regime");
  if (gr && gr.series.length) {
    gr.series[0].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.close }))));
    gr.series[1].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.sma }))));
    gr.chart.timeScale().fitContent();
  }
  setVal("regime", reg.bull ? "Haussier" : "Baissier", reg.bull ? "up" : "down");
  await Promise.all(pos.positions.map(async (p) => {
    const g = gridCharts.get("asset:" + p.asset);
    if (!g || !g.series[0]) return;
    const d = await api(`/api/candles?asset=${p.asset}&interval=1h&limit=72`);
    g.series[0].setData(candleData(d.candles));
    g.lines.forEach((l) => g.series[0].removePriceLine(l));
    g.lines = addPositionLines(g.series[0], d.position);
    g.chart.timeScale().fitContent();
    setVal("asset:" + p.asset, `${fpx(p.price)} · ${fpct(p.pnl_pct, 1)}`, (p.pnl_pct || 0) >= 0 ? "up" : "down");
  }));
}

// ---------- Détail d'un graphique ----------
let D = null;
const detail = $("#detail");
function closeDetail() {
  if (!D) return;
  clearInterval(D.timer);
  dropChart(D.chart);
  D = null;
  if (detail.open) detail.close();
  detail.classList.remove("full");
}
$("#detail-close").addEventListener("click", closeDetail);
detail.addEventListener("close", closeDetail);
detail.addEventListener("click", (e) => { if (e.target === detail) closeDetail(); });
$("#detail-full").addEventListener("click", () => detail.classList.toggle("full"));
$$("#detail-intervals button").forEach((b) => b.addEventListener("click", () => {
  if (!D) return;
  D.interval = b.dataset.interval;
  D.fitted = false;
  prefs.set("interval", D.interval);
  loadDetail();
}));

function openDetail(spec) {
  closeDetail();
  const c = COLORS();
  D = { spec, interval: prefs.get("interval", "1h"), fitted: false, lines: [], series: [] };
  $("#detail-title").textContent = spec.kind === "asset" ? `${up(spec.asset)}/USDT` : spec.title;
  $("#detail-intervals").hidden = spec.kind !== "asset";
  const box = $("#detail-chart");
  $$("#detail-chart > :not(.readout)").forEach((n) => n.remove());
  detail.showModal();
  D.chart = makeChart(box);
  if (!D.chart) return;
  if (spec.kind === "asset") {
    D.series.push(D.chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false }));
    const vol = D.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: c.muted + "66" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    D.series.push(vol);
    legend($("#detail-legend"), [[c.up, "Hausse"], [c.down, "Baisse"], [c.accent, "Entrée", true], [c.down, "Stop de clôture"], [c.down, "Stop catastrophe", true], [c.muted, "Volume"]]);
  } else if (spec.kind === "equity") {
    D.series.push(D.chart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, topLineColor: c.up, topFillColor1: c.up + "44", topFillColor2: c.up + "05", bottomLineColor: c.down, bottomFillColor1: c.down + "05", bottomFillColor2: c.down + "44", lineWidth: 2 }));
    legend($("#detail-legend"), [[c.up, "Au-dessus du départ"], [c.down, "En dessous du départ"]]);
  } else {
    D.series.push(D.chart.addLineSeries({ color: c.ink, lineWidth: 2 }));
    D.series.push(D.chart.addLineSeries({ color: c.sma, lineWidth: 2, crosshairMarkerVisible: false }));
    legend($("#detail-legend"), [[c.ink, "BTC (clôture)"], [c.sma, "Moyenne 150 jours : au-dessus, achats autorisés"]]);
  }
  D.chart.subscribeCrosshairMove((param) => {
    const ro = $("#detail-readout");
    const d = param && param.time && D ? param.seriesData.get(D.series[0]) : null;
    if (!d) { ro.textContent = D && D.lastText ? D.lastText : ""; return; }
    ro.textContent = d.open != null
      ? `O ${fpx(d.open)}  H ${fpx(d.high)}  B ${fpx(d.low)}  C ${fpx(d.close)}`
      : `${fpx(d.value)}`;
  });
  loadDetail();
  D.timer = setInterval(loadDetail, 10000);
}

function stats(rows) {
  $("#detail-stats").replaceChildren(...rows.flatMap(([k, v, cls]) => [el("dt", "", k), el("dd", cls || "", v)]));
}
async function loadDetail() {
  if (!D || !D.chart) return;
  const spec = D.spec, c = COLORS();
  $$("#detail-intervals button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.interval === D.interval)));
  try {
    if (spec.kind === "asset") {
      const d = await api(`/api/candles?asset=${spec.asset}&interval=${D.interval}&limit=500`);
      if (!D || D.spec !== spec) return;
      const cd = candleData(d.candles);
      D.series[0].setData(cd);
      D.series[1].setData(uniq(d.candles.map((r) => ({ time: r[0], value: r[5], color: (r[4] >= r[1] ? c.up : c.down) + "55" }))));
      D.series[0].setMarkers(d.markers.map((m) => ({ time: m.t, position: m.type === "buy" ? "belowBar" : "aboveBar", color: m.type === "buy" ? c.up : c.down, shape: m.type === "buy" ? "arrowUp" : "arrowDown", text: m.text })));
      D.lines.forEach((l) => D.series[0].removePriceLine(l));
      D.lines = addPositionLines(D.series[0], d.position);
      const lastC = cd[cd.length - 1], first = cd[0];
      D.lastText = lastC ? `Dernier : ${fpx(lastC.close)}` : "";
      const hi = Math.max(...cd.map((x) => x.high)), lo = Math.min(...cd.map((x) => x.low));
      const rows = [["Cours", fpx(lastC && lastC.close)], ["Sur la période", fpct(lastC && first ? (lastC.close / first.open - 1) * 100 : null), lastC && first && lastC.close >= first.open ? "up" : "down"], ["Plus haut", fpx(hi)], ["Plus bas", fpx(lo)]];
      if (d.position) {
        const p = d.position, px = lastC ? lastC.close : null;
        rows.push(["Prix d'achat", fpx(p.entry)], ["Stop de clôture", fpx(p.stop)], ["Stop catastrophe", fpx(p.disaster)],
          ["Résultat", fpct(px ? (px / p.entry - 1) * 100 : null), px >= p.entry ? "up" : "down"],
          ["En R (≈)", fR(px && p.risk ? (px - p.entry) * p.qty / p.risk : null)], ["Achat le", fdate(p.entry_date)]);
      } else rows.push(["Position", "aucune"]);
      if (d.stale) rows.push(["Données", "en retard (réseau)", "warn"]);
      stats(rows);
    } else if (spec.kind === "equity") {
      const eq = await api("/api/equity?days=365");
      if (!D || D.spec !== spec) return;
      const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
      const start = pts.length ? pts[0].value : 0;
      D.series[0].applyOptions({ baseValue: { type: "price", price: start } });
      D.series[0].setData(pts);
      const last = pts[pts.length - 1], hi = Math.max(...pts.map((p) => p.value));
      D.lastText = last ? `Capital : ${fusd(last.value)}` : "";
      stats([["Capital", fusd(last && last.value)], ["Au départ de la courbe", fusd(start)],
        ["Variation", fpct(last && start ? (last.value / start - 1) * 100 : null), last && last.value >= start ? "up" : "down"],
        ["Plus haut", fusd(hi)], ["Baisse depuis le plus haut", fpct(last && hi ? (last.value / hi - 1) * 100 : null)], ["Points", String(pts.length)]]);
    } else {
      const reg = await api("/api/regime");
      if (!D || D.spec !== spec) return;
      D.series[0].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.close }))));
      D.series[1].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.sma }))));
      const l = reg.points[reg.points.length - 1];
      D.lastText = l ? `BTC ${fpx(l.close)}` : "";
      stats([["BTC", fpx(l && l.close)], [`Moyenne ${reg.sma} j`, fpx(l && l.sma)], ["Écart", fpct(l ? (l.close / l.sma - 1) * 100 : null), reg.bull ? "up" : "down"], ["Régime", reg.bull ? "Haussier : achats autorisés" : "Baissier : aucun achat", reg.bull ? "up" : "down"]]);
    }
    if (!D.fitted) { D.chart.timeScale().fitContent(); D.fitted = true; }
    $("#detail-readout").textContent = D.lastText || "";
  } catch (e) {
    toast(`Graphique : ${e.message}`, "err");
  }
}

// ---------- Cryptos ----------
let assetFilter = "all";
const sparkCache = new Map();
const sparkObs = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  entries.forEach((en) => { if (en.isIntersecting) { sparkObs.unobserve(en.target); drawSpark(en.target); } });
}) : null;
async function drawSpark(card) {
  const a = card.dataset.asset, hit = sparkCache.get(a);
  let closes = hit && Date.now() - hit.t < 300000 ? hit.v : null;
  if (!closes) {
    try {
      const d = await api(`/api/candles?asset=${a}&interval=1h&limit=48`);
      closes = d.candles.map((r) => r[4]);
      sparkCache.set(a, { t: Date.now(), v: closes });
    } catch { return; }
  }
  const svg = card.querySelector(".spark");
  if (!svg || closes.length < 2) return;
  const lo = Math.min(...closes), hi = Math.max(...closes), w = 200, h = 42;
  const pts = closes.map((v, i) => [i * w / (closes.length - 1), h - 3 - (hi > lo ? (v - lo) / (hi - lo) : 0.5) * (h - 6)]);
  const color = closes[closes.length - 1] >= closes[0] ? "var(--up)" : "var(--down)";
  const ns = "http://www.w3.org/2000/svg";
  const area = document.createElementNS(ns, "path");
  area.setAttribute("d", `M0 ${h} L${pts.map((p) => p.join(" ")).join(" L")} L${w} ${h} Z`);
  area.setAttribute("fill", color);
  area.setAttribute("fill-opacity", "0.12");
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "2");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  svg.replaceChildren(area, line);
}
$$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  assetFilter = b.dataset.filter;
  $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  arrangeAssets();
}));
$("#asset-search").addEventListener("input", () => arrangeAssets());
$("#asset-sort").addEventListener("change", () => arrangeAssets());
let ASSETS = [];
function arrangeAssets() {
  const q = $("#asset-search").value.trim().toLowerCase(), sort = $("#asset-sort").value;
  const grid = $("#asset-grid");
  const rows = ASSETS.slice().sort((a, b) => sort === "change" ? (b.change_pct || 0) - (a.change_pct || 0)
    : sort === "volume" ? (b.volume_quote || 0) - (a.volume_quote || 0) : a.asset.localeCompare(b.asset));
  rows.forEach((r) => {
    const card = grid.querySelector(`[data-asset="${r.asset}"]`);
    if (!card) return;
    const show = (assetFilter === "all" || (assetFilter === "held" && r.held) || (assetFilter === "vetoed" && r.vetoed))
      && (!q || r.asset.includes(q) || r.name.toLowerCase().includes(q));
    card.hidden = !show;
    grid.append(card);
  });
}
async function renderAssets() {
  const d = await api("/api/assets");
  ASSETS = d.assets;
  const grid = $("#asset-grid");
  d.assets.forEach((r, k) => {
    let card = grid.querySelector(`[data-asset="${r.asset}"]`);
    if (!card) {
      card = el("article", "card asset");
      card.dataset.asset = r.asset;
      card.style.animationDelay = (k * 25) + "ms";
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `${up(r.asset)} : ouvrir le graphique`);
      const top = el("div", "row");
      top.append(el("span", "tk", up(r.asset)), el("span", "chg"));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "spark");
      svg.setAttribute("viewBox", "0 0 200 42");
      svg.setAttribute("preserveAspectRatio", "none");
      const bottom = el("div", "row");
      bottom.append(el("span", "tags"), el("span", "sub vol"));
      card.append(top, el("span", "name", r.name), el("span", "px"), svg, bottom);
      const open = () => openDetail({ kind: "asset", asset: r.asset });
      card.addEventListener("click", open);
      card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      grid.append(card);
      if (sparkObs) sparkObs.observe(card); else drawSpark(card);
    }
    const px = card.querySelector(".px"), prev = Number(px.dataset.v);
    px.textContent = fpx(r.price);
    if (isFinite(prev) && r.price != null && r.price !== prev && !reduceMotion.matches) {
      px.classList.remove("flash-up", "flash-down");
      void px.offsetWidth;
      px.classList.add(r.price > prev ? "flash-up" : "flash-down");
      setTimeout(() => px.classList.remove("flash-up", "flash-down"), 60);
    }
    px.dataset.v = r.price;
    const chg = card.querySelector(".chg");
    chg.textContent = r.change_pct == null ? "–" : `${r.change_pct >= 0 ? "▲" : "▼"} ${fpct(r.change_pct)}`;
    chg.className = "chg " + ((r.change_pct || 0) >= 0 ? "up" : "down");
    const tags = card.querySelector(".tags");
    tags.replaceChildren(...[r.held && el("span", "tag held", "Détenue"), r.vetoed && el("span", "tag vetoed", "Achats bloqués")].filter(Boolean));
    if (r.veto_reason) card.title = r.veto_reason;
    card.querySelector(".vol").textContent = `Vol. 24 h ${fvol(r.volume_quote)}`;
  });
  arrangeAssets();
  if (d.stale) toast("Cours Binance momentanément indisponibles : dernières valeurs affichées.", "err");
}

// ---------- Positions ----------
function table(node, head, rows, onRow) {
  const thead = el("thead"), tr = el("tr");
  head.forEach((h) => { const th = el("th", "", h); th.scope = "col"; tr.append(th); });
  thead.append(tr);
  const tb = el("tbody");
  if (!rows.length) {
    const r = el("tr"), td = el("td", "empty", "Rien à afficher.");
    td.colSpan = head.length;
    r.append(td);
    tb.append(r);
  }
  rows.forEach(({ cells, cls, key }) => {
    const r = el("tr", onRow ? "click" : "");
    cells.forEach((c, j) => r.append(el("td", (j === 0 ? "tk " : "") + ((cls && cls[j]) || ""), c)));
    if (onRow) {
      r.tabIndex = 0;
      r.addEventListener("click", () => onRow(key));
      r.addEventListener("keydown", (e) => { if (e.key === "Enter") onRow(key); });
    }
    tb.append(r);
  });
  node.replaceChildren(thead, tb);
}
async function renderPositions() {
  const [pos, tr] = await Promise.all([api("/api/positions"), api("/api/trades")]);
  const open = (a) => openDetail({ kind: "asset", asset: a });
  table($("#p-table"), ["Crypto", "Achat le", "Prix d'achat", "Cours", "Stop", "Écart au stop", "Résultat", "En R (≈)"],
    pos.positions.map((p) => ({ key: p.asset, cells: [up(p.asset), fdate(p.entry_date), fpx(p.entry), fpx(p.price), fpx(p.stop), fpct(p.stop_dist_pct, 1), fpct(p.pnl_pct), fR(p.r)],
      cls: { 6: (p.pnl_pct || 0) >= 0 ? "up" : "down", 7: (p.r || 0) >= 0 ? "up" : "down" } })), open);
  $("#p-sub").textContent = `${pos.positions.length} / ${S ? S.max_positions : 8}`;
  const trades = tr.trades;
  const wins = trades.filter((t) => t.pnl > 0).length;
  $("#t-sub").textContent = trades.length ? `${trades.length} trades · ${nf(0).format(wins / trades.length * 100)} % gagnants` : "";
  table($("#t-table"), ["Crypto", "Achat", "Vente", "Prix d'achat", "Prix de vente", "Raison", "Résultat", "En R"],
    trades.map((t) => ({ key: t.asset, cells: [up(t.asset), fdate(t.entry_date), fdate(t.date), fpx(t.entry), fpx(t.exit), REASON[t.reason] || t.reason || "", `${sign(t.pnl)}${nf(2).format(Math.abs(t.pnl || 0))} USDT`, fR(t.r)],
      cls: { 6: t.pnl > 0 ? "up" : "down", 7: t.r > 0 ? "up" : "down" } })), open);
}

// ---------- Veille ----------
async function renderWatch() {
  const w = await api("/api/watch");
  const last = w.last, mood = $("#w-mood");
  mood.textContent = !last ? "—" : last.sentiment > 0.2 ? "Positif" : last.sentiment < -0.2 ? "Négatif" : "Neutre";
  mood.className = !last ? "" : last.sentiment > 0.2 ? "up" : last.sentiment < -0.2 ? "down" : "";
  $("#w-mood-sub").textContent = last ? `${nf(2).format(last.sentiment)} · ${last.providers_total ? `${last.providers}/${last.providers_total} IA` : "mots-clés"} · ${last.day}` : "pas encore de rapport";
  countUp($("#w-vetoes"), w.vetoes.length, (v) => String(Math.round(v)));
  const items = [...w.vetoes.map((v) => ["crit", `${v.reason} : achats bloqués jusqu'au ${fdate(v.until)}`]),
    ...((last && last.alerts) || []).map((a) => ["warn", a])];
  if (!items.length) items.push(["ok", "Aucune alerte."]);
  $("#w-list").replaceChildren(...items.map(([k, t]) => el("li", k, t)));
  $("#w-report").textContent = w.report_text || "Aucun rapport : la veille tourne chaque jour avec le bot (python market_watch.py pour un rapport immédiat).";
}

// ---------- Journal ----------
async function renderLog() {
  const d = await api("/api/log?lines=500");
  const lvl = $("#log-level").value;
  const lines = d.lines.filter((l) => lvl === "all" ? true : lvl === "warn" ? /\[(WARNING|ERROR|CRITICAL)\]/.test(l) : /\[(ERROR|CRITICAL)\]/.test(l));
  const log = $("#log");
  log.replaceChildren(...(lines.length ? lines : ["Journal vide."]).map((l) => {
    const cls = /\[(ERROR|CRITICAL)\]/.test(l) ? "error" : /\[WARNING\]/.test(l) ? "warn" : /\[(DAILY|TRADE|ENTRY|VEILLE)\]/.test(l) ? "info-tag" : "";
    return el("li", cls, l);
  }));
  if ($("#log-follow").checked) log.scrollTop = log.scrollHeight;
}
$("#log-level").addEventListener("change", () => renderLog().catch(() => {}));

// ---------- Réglages ----------
let installEvt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; $("#install-btn").hidden = false; });
$("#install-btn").addEventListener("click", async () => {
  if (!installEvt) return;
  installEvt.prompt();
  await installEvt.userChoice;
  installEvt = null;
  $("#install-btn").hidden = true;
});
async function renderSettings() {
  if (!S) await refreshStatus();
  const dl = $("#s-bot");
  const rows = [["Mode", S.demo ? "Démonstration" : S.mode === "live" ? (S.testnet ? "Réel (testnet)" : "Réel") : "Paper (argent fictif)"],
    ["Risque par trade", `${nf(1).format(S.risk_pct)} %`], ["Positions au plus", String(S.max_positions)],
    ["Risque cumulé au plus", `${nf(0).format(S.max_total_risk_pct)} %`],
    ["Profil prudent", S.dd_throttle.length ? S.dd_throttle.map(([t, m]) => `risque × ${m} au-delà de ${nf(0).format(t * 100)} % de baisse`).join(" ; ") : "désactivé"],
    ["Arrêt d'urgence", `baisse de ${nf(0).format(S.kill_drawdown_pct)} %`], ["Cryptos suivies", String(S.universe.length)]];
  dl.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", "", k), el("dd", "", v)]));
  const list = $("#s-alerts");
  const chans = S.alerts.length ? S.alerts : [{ name: "demo", label: "Aucun canal (démonstration)", enabled: false }];
  list.replaceChildren(...chans.map((c) => {
    const li = el("li");
    const b = el("button", "btn small ghost", "Tester");
    b.type = "button";
    b.disabled = !c.enabled;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        const r = await api("/api/alerts/test", { body: { channel: c.name } });
        toast(`${c.label} : ${r.message}`, r.ok ? "ok" : "err");
      } catch (e) { toast(e.message, "err"); }
      b.disabled = false;
    });
    const t = el("span");
    t.append(el("strong", "", c.label), el("span", "sub", c.enabled ? " · configuré" : " · non configuré"));
    li.append(t, b);
    return li;
  }));
  const phone = $("#s-phone");
  const parts = [];
  if (S.lan_urls.length) {
    parts.push(el("p", "", "Sur votre téléphone connecté au même Wi-Fi, ouvrez :"));
    S.lan_urls.forEach((u) => parts.push(el("p", "", u)));
  } else {
    parts.push(el("p", "sub", "Accès depuis un téléphone (même Wi-Fi) : ajoutez PANEL_PASSWORD=… dans .env, puis lancez « python trendguard_bot.py panel --host 0.0.0.0 ». L'adresse à ouvrir s'affichera ici."));
  }
  parts.push(el("p", "sub", "Android : menu ⋮ puis « Ajouter à l'écran d'accueil ». iPhone : Partager puis « Sur l'écran d'accueil ». Le panneau s'ouvre alors comme une application."));
  phone.replaceChildren(...parts);
}
$("#logout-btn").addEventListener("click", async () => {
  try { await api("/api/logout", { body: {} }); } catch { /* déjà déconnecté */ }
  showLogin();
});

// ---------- Connexion ----------
function showLogin() {
  $("#login").hidden = false;
  $("#login-pass").focus();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-err").textContent = "";
  try {
    const r = await api("/api/login", { body: { password: $("#login-pass").value } });
    if (r.ok) {
      $("#login").hidden = true;
      $("#login-pass").value = "";
      await refreshStatus();
      refreshTab(true);
    }
  } catch (err) {
    $("#login-err").textContent = err.message;
  }
});

// ---------- Rafraîchissement ----------
const RENDER = { dash: renderDash, charts: renderCharts, assets: renderAssets, positions: renderPositions, watch: renderWatch, log: renderLog, settings: renderSettings };
const PERIOD = { dash: 15, charts: 15, assets: 15, positions: 15, watch: 60, log: 5, settings: 30 };
let lastTab = 0, lastStatus = 0, busy = false;
async function refreshTab(force = false) {
  if (busy && !force) return;
  busy = true;
  lastTab = Date.now();
  try {
    if (!S) await refreshStatus();
    if (!S) return;                     // pas encore connecté
    await RENDER[current]();
  } catch (e) {
    if (e.message !== "connexion requise") toast(`Actualisation : ${e.message}`, "err");
  } finally {
    busy = false;
  }
}
function tick() {
  if (document.hidden || !$("#login").hidden) return;
  const now = Date.now();
  if (nextDecisionAt) $("#d-next").textContent = fdur((nextDecisionAt - now) / 1000);
  if (lastOk && !statusErr) $("#updated").textContent = `Mis à jour il y a ${Math.round((now - lastOk) / 1000)} s`;
  if (now - lastStatus > 5000) { lastStatus = now; refreshStatus(); }
  if (current && now - lastTab > PERIOD[current] * 1000) refreshTab();
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { lastStatus = 0; lastTab = 0; tick(); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && D) closeDetail(); });

// ---------- Démarrage ----------
applyTheme(prefs.get("theme", "auto"));
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* application installable : facultatif */ });
}
refreshStatus().then(() => { route(); setInterval(tick, 1000); });
})();
