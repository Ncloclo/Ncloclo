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
// Raisonnement du bot, actif par actif : [classe de l'étiquette, libellé].
const STATUS = {
  held: ["held", "Détenue"], bought: ["up", "Achetée"], sold: ["down", "Vendue"],
  watch: ["watch", "Sous surveillance"], full: ["watch", "Signal · plafond atteint"],
  bear: ["warn", "Signal · marché baissier"], deferred: ["warn", "Achat différé"],
  cancelled: ["muted", "Achat annulé"], veto: ["vetoed", "Achats bloqués"],
  wait: ["muted", "Pas de cassure"], weak: ["muted", "Tendance faible"],
  illiquid: ["muted", "Peu échangée"], young: ["muted", "Trop récente"],
  nodata: ["muted", "Données insuffisantes"], halted: ["down", "Arrêt d'urgence"],
};
const CANDIDATE = new Set(["watch", "full", "bear", "deferred"]);

// ---------- Préférences locales ----------
const prefs = {
  get(k, d) { try { const v = localStorage.getItem("tg:" + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tg:" + k, v); } catch { /* stockage bloqué */ } },
};

// ---------- Temps de réflexion et de chargement ----------
// Chaque passage d'une rubrique ou d'une sélection à une autre affiche un
// chargement d'au moins 3 s (réglable : Réglages ▸ Affichage), pendant que
// les données se chargent réellement dessous.
const WAITS = { 3: 3000, 1: 1000, 0: 0 };
const waitBase = () => { const v = WAITS[prefs.get("wait", "3")]; return v == null ? 3000 : v; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const loaderSeq = {};
async function withLoader(text, work, target = "loader") {
  const ms = waitBase();
  const box = document.getElementById(target);
  const seq = (loaderSeq[target] = (loaderSeq[target] || 0) + 1);
  if (ms && box) {
    box.querySelector(".loader-text").textContent = text;
    box.style.setProperty("--ms", ms + "ms");
    const bar = box.querySelector(".loader-bar i");
    bar.style.animation = "none";
    void bar.offsetWidth;                          // barre de progression relancée
    bar.style.animation = "";
    box.hidden = false;
  }
  try {
    await Promise.all([Promise.resolve().then(work), sleep(ms)]);
  } finally {
    if (box && loaderSeq[target] === seq) box.hidden = true;
  }
}
$$("[data-wait]").forEach((b) => b.addEventListener("click", () => {
  prefs.set("wait", b.dataset.wait);
  $$("[data-wait]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  toast(`Temps de réflexion et de chargement : ${b.textContent}`, "ok");
}));
$$("[data-wait]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.wait === prefs.get("wait", "3"))));

// ---------- API ----------
async function api(path, opts = {}) {
  const init = { headers: { "X-TrendGuard": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("connexion requise"); }
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
// Achats et ventes du bot : flèches sur les bougies, au prix payé.
const tradeMarkers = (list, c) => (list || []).map((m) => ({
  time: m.t, position: m.type === "buy" ? "belowBar" : "aboveBar", color: m.type === "buy" ? c.up : c.down,
  shape: m.type === "buy" ? "arrowUp" : "arrowDown",
  text: m.type === "buy" && m.price != null ? `${m.text} ${fpx(m.price)}` : m.text,
}));
// Achats sur la courbe du capital : chacun sur le point le plus proche.
function buyMarkersOn(points, buys, c, withText = true, sells = []) {
  const out = [];
  if (!points.length) return out;
  const nearest = (t) => {
    let lo = 0, hi = points.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (points[mid].time < t) lo = mid + 1; else hi = mid; }
    return lo > 0 && t - points[lo - 1].time < points[lo].time - t ? points[lo - 1] : points[lo];
  };
  (buys || []).forEach((b) => out.push({ time: nearest(b.t).time, position: "belowBar", color: c.up, shape: "arrowUp", text: withText ? up(b.asset) : "" }));
  (sells || []).forEach((s) => out.push({ time: nearest(s.t).time, position: "aboveBar", color: c.down, shape: "arrowDown", text: withText ? `${up(s.asset)} ${fR(s.r)}` : "" }));
  return out.sort((a, b) => a.time - b.time);
}
// Graphique d'une position : intervalle choisi pour que l'achat soit visible.
function gridInterval(p) {
  const age = p.entry_date ? Math.max(0, (Date.now() - Date.parse(p.entry_date)) / 3600e3) : 0;
  if (!isFinite(age) || age <= 60) return ["1h", 72, "1 h"];
  if (age <= 24 * 12) return ["4h", Math.min(500, Math.ceil(age / 4) + 30), "4 h"];
  return ["1d", Math.min(500, Math.ceil(age / 24) + 20), "1 jour"];
}

function addPositionLines(series, pos) {
  if (!pos || !LWC) return [];
  const c = COLORS(), lines = [];
  const add = (price, color, style, title) => {
    if (price != null) lines.push(series.createPriceLine({ price, color, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
  };
  add(pos.entry, c.accent, LWC.LineStyle.Dashed, "Entrée");
  add(pos.stop, c.down, LWC.LineStyle.Solid, "Vente si <");
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
const TABS = ["dash", "news", "charts", "assets", "positions", "watch", "log", "settings"];
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
  const title = $("#page-" + t).dataset.title;
  $("#page-title").textContent = title;
  window.scrollTo({ top: 0 });
  withLoader(`Chargement : ${title}…`, () => refreshTab(true));
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
    checkNewBuy();
  } catch (e) {
    statusErr = true;
    if (e.message !== "connexion requise") $("#updated").textContent = "Panneau injoignable";
  }
}
// Achat en temps réel : annonce, puis graphiques redessinés avec sa flèche.
let lastBuyKey = null;
function checkNewBuy() {
  const b = S.last_buy, key = b ? `${b.asset}|${b.date}` : "";
  if (lastBuyKey !== null && key && key !== lastBuyKey) {
    toast(`🟢 Achat en temps réel : ${up(b.asset)} à ${fpx(b.price)} USDT${b.cost ? ` (${fusd(b.cost)})` : ""}`, "ok");
    if (["dash", "charts", "positions", "assets"].includes(current)) {
      refreshTab(true).then(() => {
        const g = gridCharts.get("asset:" + b.asset);
        if (!g) return;
        g.card.classList.remove("new-buy");
        void g.card.offsetWidth;
        g.card.classList.add("new-buy");
      });
    }
  }
  lastBuyKey = key;
}
function renderStatus() {
  const pill = $("#st-pill"), btn = $("#auto-btn");
  const label = { running: "En marche", stopped: "Arrêté", starting: "Démarrage…", stopping: "Arrêt en cours…", restarting: "Relance automatique…" }[S.state] || S.state;
  pill.className = "pill " + (S.state === "running" ? "running" : S.state === "stopped" ? "stopped" : "pending");
  pill.querySelector("span").textContent = S.halted ? "Arrêt d'urgence" : label;
  const mode = $("#mode-badge");
  mode.textContent = S.demo ? "DÉMO" : S.mode === "live" ? (S.testnet ? "RÉEL · TESTNET" : "RÉEL") : "PAPER";
  mode.className = "badge " + (S.mode === "live" ? "live" : "paper");
  $("#brand-mode").textContent = S.demo ? "Démonstration" : S.mode === "live" ? "Mode réel" : "Mode paper (argent fictif)";
  const running = S.state === "running" || S.state === "restarting", busy = S.state === "starting" || S.state === "stopping";
  btn.disabled = busy;
  btn.classList.toggle("is-running", running);
  btn.querySelector("use").setAttribute("href", running ? "#i-stop" : "#i-play");
  $("#auto-label").textContent = running ? "ARRÊTER" : "AUTO";
  $("#auto-sub").textContent = busy ? label : S.state === "restarting" ? "Relance après une erreur" : running ? "Automatisation en marche" : "Démarrer l'automatisation";
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
  const running = S.state === "running" || S.state === "restarting";
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
  const newsP = loadNews().then((n) => { renderTicker(n); newsAlerts(n); }).catch(() => { /* bandeau : réessai au prochain rafraîchissement */ });
  api("/api/anticipation").then(renderAnticipation).catch(() => { /* réessai au prochain rafraîchissement */ });
  const [pos, eq, mind] = await Promise.all([api("/api/positions"), api("/api/equity?days=30"), api("/api/reasoning")]);
  renderMind(mind);
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
    const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
    dashSeries.setData(pts);
    dashSeries.setMarkers(buyMarkersOn(pts, eq.buys, COLORS(), false, eq.sells));
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

  await Promise.race([newsP, new Promise((r) => setTimeout(r, 800))]);
  const alerts = [];
  if (S.halted) alerts.push(["crit", `Arrêt d'urgence : ${S.halt_reason || ""}`]);
  if (S.state === "stopped") alerts.push(["warn", "Le bot est arrêté : cliquez sur AUTO pour reprendre l'automatisation."]);
  const sup = (S.autonomy && S.autonomy.supervisor) || {};
  if (S.state === "restarting") alerts.push(["warn", "Le bot s'est arrêté sur une erreur : relance automatique en cours."]);
  else if (S.state === "running" && !sup.running && !S.demo) alerts.push(["warn", "Relance automatique inactive (bot lancé hors du panneau) : ARRÊTER puis AUTO pour l'activer."]);
  if (sup.running && sup.restarts > 0 && sup.last_exit) alerts.push(["warn", `Le bot s'est relancé seul ${sup.restarts} fois (dernière erreur le ${fdate(sup.last_exit.at)}).`]);
  S.vetoes.forEach((v) => alerts.push(["crit", `${v.reason} : achats bloqués jusqu'au ${fdate(v.until)}`]));
  ((S.watch && S.watch.alerts) || []).forEach((a) => alerts.push(["warn", a]));
  if (pos.stale) alerts.push(["warn", "Cours Binance momentanément indisponibles : dernières valeurs affichées."]);
  NEWS_ALERTS.forEach((i) => alerts.push(["warn", `Actualité sensible sur ${i.assets.map(up).join(", ")} (détenue) : ${i.title}`]));
  if (!alerts.length) alerts.push(["ok", "Aucune alerte. Tout est normal."]);
  $("#d-alerts").replaceChildren(...alerts.map(([k, t]) => el("li", k, t)));
}

// ---------- Anticipation : ventes et achats probables à la prochaine clôture ----------
let anticipKey = "";
const pctRound = (p) => `${Math.round((p || 0) * 100)} %`;
function probRow(cls, asset, text, prob, chips, blocked = false) {
  const li = el("li", "a-row");
  const b = el("button", "a-asset");
  b.type = "button";
  b.textContent = up(asset);
  b.addEventListener("click", () => openDetail({ kind: "asset", asset }));
  const bar = el("span", "a-bar " + (blocked ? "muted" : cls));
  const fill = el("i");
  requestAnimationFrame(() => { fill.style.width = Math.max(2, Math.round((prob || 0) * 100)) + "%"; });
  bar.append(fill);
  const info = el("span", "a-info");
  info.append(el("span", "", text));
  (chips || []).forEach((c) => info.append(el("span", "tag muted", c)));
  li.append(b, info, bar, el("strong", "a-prob " + (prob >= 0.5 && !blocked ? cls : ""), pctRound(prob)));
  return li;
}
function renderAnticipation(f) {
  if (!f || !f.ready) {
    $("#a-when").textContent = "· disponible après la prochaine décision";
    return;
  }
  $("#a-when").textContent = `dans ${fdur(f.hours_left * 3600)}`;
  const r = f.risk;
  $("#a-risk").textContent = `Risque engagé ${nf(1).format(r.open_risk_pct || 0)} % / ${nf(0).format(r.budget_pct)} % · ${r.slots} place${r.slots > 1 ? "s" : ""} libre${r.slots > 1 ? "s" : ""}`;
  const g = f.regime;
  $("#a-regime").textContent = !g ? "" : g.bull_now
    ? `Marché haussier : le bot n'achèterait plus si BTC clôturait sous ${fpx(g.threshold)} $ (${fpct(g.dist_pct, 1)}, probabilité ${pctRound(g.prob_bear)}).`
    : `Marché baissier : aucun achat tant que BTC reste sous ${fpx(g.threshold)} $ (${fpct(g.dist_pct, 1)}).`;
  // Rafraîchissement sans changement visible : pas de nouvelle animation.
  const key = JSON.stringify([f.sells.map((s) => [s.asset, Math.round(s.prob * 100), s.stop, Math.round(s.dist_pct * 10)]),
    f.buys.map((b) => [b.asset, Math.round(b.prob * 100), b.trigger, Math.round(b.dist_pct * 10), b.blocked]), f.advice]);
  if (key === anticipKey) return;
  // Première fois : entrée animée ; ensuite, mise à jour discrète.
  $("#d-anticip").classList.toggle("calm", anticipKey !== "");
  anticipKey = key;
  const sells = f.sells.slice(0, 6);
  $("#a-sells").replaceChildren(...(sells.length ? sells.map((s) => probRow("down", s.asset,
    `vente si < ${fpx(s.stop)} (${fpct(s.dist_pct, 1)})`, s.prob, s.locked_pct != null ? [`${s.locked_pct >= 0 ? "gain verrouillé" : "perte verrouillée"} ${fpct(s.locked_pct, 1)}`] : [])) : [el("li", "empty", "Aucune position : rien à vendre.")]));
  const buys = f.buys.slice(0, 6);
  $("#a-buys").replaceChildren(...(buys.length ? buys.map((b) => probRow("up", b.asset,
    `achat si > ${fpx(b.trigger)} (${fpct(b.dist_pct, 1)})`, b.prob, b.blocked, b.blocked.length > 0)) : [el("li", "empty", "Aucune crypto proche d'un signal d'achat.")]));
  $("#a-advice").replaceChildren(...f.advice.map((t, i) => {
    const li = el("li", "", t);
    li.style.animationDelay = i * 80 + "ms";
    return li;
  }));
}
// Actualité sensible (piratage, retrait, régulation…) sur une crypto détenue.
let NEWS_ALERTS = [];
function newsAlerts(n) {
  const held = new Set(n.held || []);
  NEWS_ALERTS = (n.items || []).filter((i) => i.alert && i.assets.some((a) => held.has(a))).slice(0, 3);
}

let mindKey = "";
function renderMind(m) {
  const cur = m.current;
  const lines = cur ? cur.lines : ["Pas encore de décision : le raisonnement s'affiche après la première clôture quotidienne (00:02 UTC)."];
  $("#d-mind-day").textContent = cur ? `bougie du ${fdate(cur.day)}` : "";
  const key = JSON.stringify([lines, m.pending, m.history.length]);
  if (key === mindKey) return;                // n'anime que ce qui change
  mindKey = key;
  $("#d-mind").replaceChildren(...lines.map((t, i) => {
    const li = el("li", "", t);
    li.style.animationDelay = i * 90 + "ms";
    return li;
  }));
  const chip = (cls, a, text, title) => {
    const b = el("button", "chip " + cls);
    b.type = "button";
    b.title = title || "";
    b.append(el("strong", "", up(a)), el("span", "", text));
    b.addEventListener("click", () => openDetail({ kind: "asset", asset: a }));
    return b;
  };
  const chips = [
    ...((cur && cur.radar) || []).map((a) => {
      const x = (cur.assets && cur.assets[a]) || {};
      return chip("watch", a, `${fpct(x.breakout_gap_pct, 1)} avant la cassure`, x.text);
    }),
    ...m.pending.map((p) => chip("warn", p.asset, `achat différé · essai ${p.tries}`, p.reason)),
  ];
  $("#d-radar").replaceChildren(...chips);
  $("#d-radar").hidden = !chips.length;
  const hist = m.history.length ? m.history : [{ day: "", text: "Aucun historique pour l'instant." }];
  $("#d-mind-history").replaceChildren(...hist.map((h) => {
    const li = el("li");
    if (h.day) li.append(el("strong", "", fdate(h.day) + " · "));
    li.append(document.createTextNode(h.text));
    return li;
  }));
}

// ---------- Actualités : bandeau défilant et page détaillée ----------
let NEWS = null, newsAt = 0, tickerKey = "", listKey = "", newsFilter = "all", pendingNews = null;
const CAT = { crypto: "Crypto", finance: "Finance" };
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const fago = (iso) => {
  const t = iso ? Date.parse(iso) : NaN;
  if (!isFinite(t)) return "";
  const sec = Math.max(0, (Date.now() - t) / 1000);
  return sec < 3600 ? `il y a ${Math.max(1, Math.round(sec / 60))} min` : sec < 86400 ? `il y a ${Math.round(sec / 3600)} h` : `il y a ${Math.round(sec / 86400)} j`;
};
const fbig = (v) => (v == null || !isFinite(v) ? "–" : nf(0).format(v / 1e9) + " Md$");
async function loadNews(maxAge = 60000) {
  if (!NEWS || NEWS.loading || Date.now() - newsAt > maxAge) {
    NEWS = await api("/api/news");
    newsAt = Date.now();
  }
  return NEWS;
}
function renderTicker(n) {
  const items = n.items.slice(0, 30);
  const key = items.map((i) => i.url).join("|") + (n.loading ? "~" : "");
  if (key === tickerKey) return;                 // pas de saut du défilement
  tickerKey = key;
  const group = () => {
    const g = el("span", "ticker-group");
    if (!items.length) g.append(el("span", "t-item", n.loading ? "Chargement des actualités…" : "Actualités momentanément indisponibles."));
    items.forEach((it) => {
      const t = el("span", "t-item" + (it.alert ? " alert" : ""));
      t.dataset.url = it.url;
      t.append(el("b", "t-cat " + it.category, CAT[it.category] || it.category),
        el("span", "t-src", `${it.source} · ${fago(it.published)}`),
        el("span", "t-title", (it.alert ? "⚠ " : "") + it.title));
      g.append(t);
    });
    return g;
  };
  const first = group(), copy = group();
  copy.setAttribute("aria-hidden", "true");      // copie pour une boucle sans à-coup
  const track = $("#d-ticker-track");
  track.replaceChildren(first, copy);
  requestAnimationFrame(() => track.style.setProperty("--dur", Math.max(40, first.scrollWidth / 40) + "s"));
}
$("#d-ticker").addEventListener("click", (e) => {
  const it = e.target.closest(".t-item");
  pendingNews = it && it.dataset.url ? it.dataset.url : null;
  if (pendingNews) {                             // l'article cliqué doit être visible
    newsFilter = "all";
    $$("[data-news]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.news === "all")));
    $("#news-lang").value = "all";
    $("#news-search").value = "";
    listKey = "";
  }
});
async function renderNews() {
  const n = await loadNews(30000);
  renderTicker(n);
  const m = n.markets || {}, c = m.crypto, f = m.fear_greed;
  countUp($("#n-cap"), c ? c.market_cap_usd : null, fbig);
  const capSub = $("#n-cap-sub");
  capSub.textContent = c ? `${fpct(c.market_cap_change_24h_pct)} en 24 h` : "CoinGecko indisponible";
  capSub.className = "sub " + (c && c.market_cap_change_24h_pct >= 0 ? "up" : c ? "down" : "");
  countUp($("#n-dom"), c ? c.btc_dominance_pct : null, (v) => fpct(v, 1).replace("+", ""));
  $("#n-dom-sub").textContent = c ? `ether ${nf(1).format(c.eth_dominance_pct)} % du marché` : "";
  const fng = $("#n-fng");
  fng.textContent = f ? `${f.value} · ${f.label}` : "—";
  fng.className = f ? (f.value >= 55 ? "up" : f.value <= 45 ? "down" : "") : "";
  $("#n-gauge").style.setProperty("--v", f ? f.value + "%" : "50%");
  const hist = (f && f.history) || [];
  $("#n-fng-sub").textContent = hist.length > 7 ? `il y a 7 jours : ${hist[hist.length - 8]} · 0 = peur, 100 = euphorie` : "0 = peur extrême, 100 = euphorie";
  countUp($("#n-vol"), c ? c.volume_24h_usd : null, fbig);
  $("#n-vol-sub").textContent = c ? `${nf(0).format(c.active_cryptos)} cryptos cotées` : "";

  const quotes = m.quotes || [];
  $("#n-q-sub").textContent = quotes.length ? `variation du jour · courbe sur 1 mois · ${quotes[0].source}` : "cours indisponibles";
  $("#n-quotes").replaceChildren(...quotes.map((q, k) => {
    const card = el("div", "quote");
    card.style.animationDelay = k * 40 + "ms";
    const a = Math.abs(q.price || 0), d = q.unit === "%" || (a >= 10 && a < 1000) ? 2 : a >= 1000 ? 0 : 4;
    const head = el("div", "row");
    head.append(el("span", "q-name", q.name), el("span", "chg " + ((q.change_pct || 0) >= 0 ? "up" : "down"), q.change_pct == null ? "–" : fpct(q.change_pct)));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 200 42");
    svg.setAttribute("preserveAspectRatio", "none");
    fillSpark(svg, q.closes || []);
    card.append(head, el("strong", "q-px", q.price == null ? "–" : nf(d).format(q.price) + (q.unit === "%" ? " %" : q.unit === "$" ? " $" : "")), svg);
    return card;
  }));

  const mover = (r) => {
    const li = el("li");
    const b = el("button", "mover");
    b.type = "button";
    b.append(el("strong", "", up(r.asset)), el("span", "px", fpx(r.price)), el("span", "chg " + (r.change_pct >= 0 ? "up" : "down"), fpct(r.change_pct)));
    b.addEventListener("click", () => openDetail({ kind: "asset", asset: r.asset }));
    li.append(b);
    return li;
  };
  const mv = n.movers || { up: [], down: [] };
  $("#n-up").replaceChildren(...mv.up.map(mover));
  $("#n-down").replaceChildren(...mv.down.map(mover));
  renderNewsList();
  const ok = n.sources.filter((x) => x.ok).length, bad = n.sources.filter((x) => !x.ok);
  $("#n-sources").textContent = n.loading ? "Première lecture des sources (jusqu'à 30 s sur une connexion lente)…"
    : `${ok} source${ok > 1 ? "s" : ""} lue${ok > 1 ? "s" : ""}${n.updated ? " · mise à jour " + fago(n.updated) : ""}`
      + (bad.length ? ` · indisponibles : ${bad.map((x) => x.name).join(", ")}` : "")
      + " · liens vers les articles d'origine, aucune donnée envoyée";
  if (n.loading) setTimeout(() => { if (current === "news") refreshTab(true); }, 4000);
}
function renderNewsList() {
  if (!NEWS) return;
  const q = $("#news-search").value.trim().toLowerCase(), lang = $("#news-lang").value;
  const key = [newsTag(), newsFilter, lang, q].join("|");
  if (key === listKey && !pendingNews) return;   // rien de nouveau : pas de réaffichage
  listKey = key;
  const held = new Set(NEWS.held || []);
  const rows = NEWS.items.filter((it) => (newsFilter === "all" || (newsFilter === "bot" ? it.assets.length : it.category === newsFilter))
    && (lang === "all" || it.lang === lang)
    && (!q || `${it.title} ${it.summary} ${it.source}`.toLowerCase().includes(q)));
  $("#n-sub").textContent = `${rows.length} article${rows.length > 1 ? "s" : ""} · 48 dernières heures`;
  const list = $("#n-list");
  if (!rows.length) {
    list.replaceChildren(el("li", "empty", NEWS.loading ? "Chargement des actualités…" : "Aucun article ne correspond."));
    return;
  }
  list.replaceChildren(...rows.slice(0, 150).map((it, k) => {
    const li = el("li", "news" + (it.alert ? " alert" : ""));
    li.dataset.url = it.url;
    li.style.animationDelay = Math.min(k, 20) * 30 + "ms";
    const meta = el("div", "news-meta");
    meta.append(el("b", "t-cat " + it.category, CAT[it.category] || it.category), el("span", "", it.source),
      el("span", "", fago(it.published)), el("span", "lang", up(it.lang)));
    if (it.alert) meta.append(el("span", "tag vetoed", "Concerne le bot"));
    const a = el("a", "news-title", it.title);
    a.href = safeUrl(it.url);
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.append(meta, a);
    if (it.summary) li.append(el("p", "news-sum", it.summary));
    const tags = el("div", "news-tags");
    it.assets.forEach((x) => {
      const b = el("button", "chip " + (held.has(x) ? "warn" : "watch"));
      b.type = "button";
      b.title = "Ouvrir le graphique";
      b.append(el("strong", "", up(x)), el("span", "", held.has(x) ? "détenue par le bot" : "suivie par le bot"));
      b.addEventListener("click", () => openDetail({ kind: "asset", asset: x }));
      tags.append(b);
    });
    it.topics.forEach((t) => tags.append(el("span", "tag muted", t)));
    if (tags.childNodes.length) li.append(tags);
    return li;
  }));
  if (pendingNews) {
    const hit = $$("#n-list li").find((li) => li.dataset.url === pendingNews);
    pendingNews = null;
    if (hit) {
      hit.classList.add("focus");
      hit.scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" });
    }
  }
}
const newsTag = () => (NEWS ? NEWS.items.map((i) => i.url).join("|") : "");
$$("[data-news]").forEach((b) => b.addEventListener("click", () => {
  newsFilter = b.dataset.news;
  $$("[data-news]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  withLoader(`Sélection : ${b.textContent}…`, renderNewsList);
}));
$("#news-lang").addEventListener("change", (e) => {
  withLoader(`Langue : ${e.currentTarget.selectedOptions[0].textContent}…`, renderNewsList);
});
$("#news-search").addEventListener("input", () => renderNewsList());

// ---------- Graphiques en temps réel ----------
const gridCharts = new Map();       // id → { chart, series, lines }
async function renderCharts() {
  const [eq, reg, pos, tr] = await Promise.all([api("/api/equity?days=90"), api("/api/regime"), api("/api/positions"), api("/api/trades")]);
  const c = COLORS();
  const held = new Set(pos.positions.map((p) => p.asset));
  const month = Date.now() - 30 * 86400e3, soldSeen = new Set();
  const sold = tr.trades.filter((t) => t.entry_date && Date.parse(t.date) >= month && !held.has(t.asset)
    && !soldSeen.has(t.asset) && soldSeen.add(t.asset));
  const specs = [
    { id: "equity", kind: "equity", title: "Capital du bot", legend: [[c.accent, "Capital (USDT)"], [c.muted, "Capital de départ", true], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes du bot"]] },
    { id: "regime", kind: "regime", title: `Régime BTC · moyenne ${reg.sma} jours`, legend: [[c.ink, "BTC (clôture)"], [c.sma, `Moyenne ${reg.sma} j`]] },
    ...pos.positions.map((p) => ({ id: "asset:" + p.asset, kind: "asset", asset: p.asset, title: `${up(p.asset)}/USDT · ${gridInterval(p)[2]}`, pos: p,
      legend: [[c.up, "Bougies"], [c.up, "▲ Achats du bot"], [c.accent, "Entrée", true], [c.down, "Stop de clôture (vente)"], [c.down, "Stop catastrophe", true]] })),
    ...sold.map((t) => ({ id: "sold:" + t.asset, kind: "asset", asset: t.asset, sold: t,
      title: `${up(t.asset)}/USDT · vendue · ${gridInterval({ entry_date: t.entry_date })[2]}`,
      legend: [[c.up, "▲ Achat du bot"], [c.down, "▼ Vente du bot"]] })),
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
    const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
    ge.series[0].setData(pts);
    ge.series[0].setMarkers(buyMarkersOn(pts, eq.buys, c, true, eq.sells));
    ge.chart.timeScale().fitContent();
  }
  const last = eq.points[eq.points.length - 1];
  setVal("equity", last ? fusd(last.v) : "—");
  const gr = gridCharts.get("regime");
  if (gr && gr.series.length) {
    gr.series[0].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.close }))));
    gr.series[1].setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.sma }))));
    gr.series[0].setMarkers(tradeMarkers(reg.markers, c));
    gr.chart.timeScale().fitContent();
  }
  setVal("regime", reg.bull ? "Haussier" : "Baissier", reg.bull ? "up" : "down");
  await Promise.all(sold.map(async (t) => {
    const g = gridCharts.get("sold:" + t.asset);
    if (!g || !g.series[0]) return;
    const [iv, lim] = gridInterval({ entry_date: t.entry_date });
    const d = await api(`/api/candles?asset=${t.asset}&interval=${iv}&limit=${lim}`);
    g.series[0].setData(candleData(d.candles));
    g.series[0].setMarkers(tradeMarkers(d.markers, c));
    g.chart.timeScale().fitContent();
    setVal("sold:" + t.asset, `${fR(t.r)} · ${fdate(t.date)}`, (t.r || 0) >= 0 ? "up" : "down");
  }));
  await Promise.all(pos.positions.map(async (p) => {
    const g = gridCharts.get("asset:" + p.asset);
    if (!g || !g.series[0]) return;
    const [iv, lim] = gridInterval(p);
    const d = await api(`/api/candles?asset=${p.asset}&interval=${iv}&limit=${lim}`);
    g.series[0].setData(candleData(d.candles));
    g.series[0].setMarkers(tradeMarkers(d.markers, c));
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
  withLoader(`Intervalle ${b.textContent}…`, loadDetail, "detail-loader");
}));

function openDetail(spec) {
  closeDetail();
  const c = COLORS();
  D = { spec, interval: prefs.get("interval", "1h"), fitted: false, lines: [], series: [] };
  $("#detail-title").textContent = spec.kind === "asset" ? `${up(spec.asset)}/USDT` : spec.title;
  $("#detail-intervals").hidden = spec.kind !== "asset";
  const box = $("#detail-chart");
  $$("#detail-chart > :not(.readout):not(.loader)").forEach((n) => n.remove());
  detail.showModal();
  D.chart = makeChart(box);
  if (!D.chart) return;
  if (spec.kind === "asset") {
    D.series.push(D.chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false }));
    const vol = D.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: c.muted + "66" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    D.series.push(vol);
    legend($("#detail-legend"), [[c.up, "Hausse"], [c.down, "Baisse"], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes"], [c.accent, "Entrée", true], [c.down, "Stop de clôture"], [c.down, "Stop catastrophe", true], [c.muted, "Volume"]]);
  } else if (spec.kind === "equity") {
    D.series.push(D.chart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, topLineColor: c.up, topFillColor1: c.up + "44", topFillColor2: c.up + "05", bottomLineColor: c.down, bottomFillColor1: c.down + "05", bottomFillColor2: c.down + "44", lineWidth: 2 }));
    legend($("#detail-legend"), [[c.up, "Au-dessus du départ"], [c.down, "En dessous du départ"], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes du bot"]]);
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
  withLoader("Chargement du graphique…", loadDetail, "detail-loader");
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
      D.series[0].setMarkers(tradeMarkers(d.markers, c));
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
      const buys = d.markers.filter((m) => m.type === "buy");
      rows.push(["Achats du bot visibles", String(buys.length)]);
      if (buys.length) rows.push(["Dernier achat", `${fdate(buys[buys.length - 1].date)} à ${fpx(buys[buys.length - 1].price)}`]);
      if (d.stale) rows.push(["Données", "en retard (réseau)", "warn"]);
      stats(rows);
    } else if (spec.kind === "equity") {
      const eq = await api("/api/equity?days=365");
      if (!D || D.spec !== spec) return;
      const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
      const start = pts.length ? pts[0].value : 0;
      D.series[0].applyOptions({ baseValue: { type: "price", price: start } });
      D.series[0].setData(pts);
      D.series[0].setMarkers(buyMarkersOn(pts, eq.buys, c, true, eq.sells));
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
      D.series[0].setMarkers(tradeMarkers(reg.markers, c));
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
      const times = d.candles.map((r) => r[0]);
      const marks = d.markers.map((m) => ({ i: times.indexOf(m.t), type: m.type })).filter((m) => m.i >= 0);
      sparkCache.set(a, { t: Date.now(), v: closes, m: marks });
    } catch { return; }
  }
  const svg = card.querySelector(".spark");
  if (svg) fillSpark(svg, closes, (sparkCache.get(a) || {}).m || []);
}
function fillSpark(svg, closes, marks = []) {
  if (closes.length < 2) return;
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
  // Achats (▲ vert) et ventes (▼ rouge) du bot sur la période affichée.
  marks.forEach((m) => {
    const [x, y] = pts[m.i];
    const tri = document.createElementNS(ns, "path");
    tri.setAttribute("d", m.type === "buy" ? `M${x} ${y + 3} l-5 8 h10 z` : `M${x} ${y - 3} l-5 -8 h10 z`);
    tri.setAttribute("fill", m.type === "buy" ? "var(--up)" : "var(--down)");
    tri.setAttribute("class", "spark-mark " + m.type);
    svg.append(tri);
  });
}
$$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  assetFilter = b.dataset.filter;
  $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  withLoader(`Sélection : ${b.textContent}…`, arrangeAssets);
}));
$("#asset-search").addEventListener("input", () => arrangeAssets());
$("#asset-sort").addEventListener("change", (e) => {
  withLoader(`Tri : ${e.currentTarget.selectedOptions[0].textContent}…`, arrangeAssets);
});
let ASSETS = [];
function arrangeAssets() {
  const q = $("#asset-search").value.trim().toLowerCase(), sort = $("#asset-sort").value;
  const grid = $("#asset-grid");
  const rows = ASSETS.slice().sort((a, b) => sort === "change" ? (b.change_pct || 0) - (a.change_pct || 0)
    : sort === "volume" ? (b.volume_quote || 0) - (a.volume_quote || 0) : a.asset.localeCompare(b.asset));
  rows.forEach((r) => {
    const card = grid.querySelector(`[data-asset="${r.asset}"]`);
    if (!card) return;
    const show = (assetFilter === "all" || (assetFilter === "held" && r.held) || (assetFilter === "vetoed" && r.vetoed)
      || (assetFilter === "selected" && r.selected)
      || (assetFilter === "watch" && CANDIDATE.has(r.status)))
      && (!q || r.asset.includes(q) || r.name.toLowerCase().includes(q));
    card.hidden = !show;
    grid.append(card);
  });
}
let SEL = { mode: "auto", active: [] };
function renderSelection(sel) {
  SEL = sel;
  $$("[data-sel]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sel === sel.mode)));
  $("#sel-count").textContent = `${sel.active.length} / ${sel.universe}`;
  $("#sel-actions").hidden = sel.mode === "auto";
  $("#sel-help").textContent = sel.mode === "auto"
    ? `Sélection auto (recommandée) : le bot peut acheter les 21 cryptos, toutes cochées. ${sel.note} Le classement de chaque carte est affiché pour information.`
    : `Sélection manuelle : cochez les cryptos que le bot peut acheter ; aucune n'est cochée au départ.${sel.active.length ? "" : " Tant qu'aucune n'est cochée, le bot n'achète rien."} Une crypto décochée déjà détenue reste gérée jusqu'à sa vente. Appliqué tout de suite aux achats en attente et à chaque décision (00:02 UTC).`;
}
async function saveSelection(mode, manual) {
  try {
    const r = await api("/api/selection", { body: manual ? { mode, manual } : { mode } });
    toast(r.mode === "auto" ? `Sélection auto : les ${r.active.length} cryptos sont achetables.` : r.active.length ? `Sélection manuelle : ${r.active.length} crypto${r.active.length > 1 ? "s" : ""} achetable${r.active.length > 1 ? "s" : ""}.` : "Sélection manuelle : aucune crypto cochée, cochez celles que le bot peut acheter.", "ok");
    ASSETS.forEach((a) => { a.selected = r.active.includes(a.asset); });
    renderSelection(r);
    return r;
  } catch (e) {
    toast(`Sélection non enregistrée : ${e.message}`, "err");
    return null;
  }
}
let pickTimer = 0;
function onPick() {
  clearTimeout(pickTimer);
  pickTimer = setTimeout(async () => {
    const manual = $$("#asset-grid .pick input").filter((b) => b.checked).map((b) => b.dataset.pick);
    await saveSelection("manual", manual);
    renderAssets().catch(() => {});
  }, 400);
}
$$("[data-sel]").forEach((b) => b.addEventListener("click", () => {
  if (SEL.mode === b.dataset.sel) return;
  const auto = b.dataset.sel === "auto";
  withLoader(auto ? "Sélection auto : les 21 cryptos…" : "Sélection manuelle : aucune crypto cochée…", async () => {
    await saveSelection(b.dataset.sel, auto ? undefined : []);          // manuel : on part de zéro
    await renderAssets();
  });
}));
$$("[data-sel-all]").forEach((b) => b.addEventListener("click", () => {
  const all = b.dataset.selAll === "1";
  withLoader(all ? "Sélection : les 21 cryptos…" : "Sélection : aucune crypto…", async () => {
    await saveSelection("manual", all ? ASSETS.map((a) => a.asset) : []);
    await renderAssets();
  });
}));
async function renderAssets() {
  const d = await api("/api/assets");
  ASSETS = d.assets;
  renderSelection(d.selection);
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
      const pick = el("label", "pick");
      const box = el("input");
      box.type = "checkbox";
      box.dataset.pick = r.asset;
      box.setAttribute("aria-label", `${up(r.asset)} : le bot peut l'acheter`);
      pick.append(box, el("span", "", "Achetable"));
      pick.addEventListener("click", (e) => e.stopPropagation());
      pick.addEventListener("keydown", (e) => e.stopPropagation());
      box.addEventListener("change", () => onPick());
      card.append(top, el("span", "name", r.name), pick, el("span", "px"), svg, el("p", "rank"), el("p", "why"), bottom);
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
    const st = STATUS[r.status];
    tags.replaceChildren(...[r.held && el("span", "tag held", "Détenue"), r.vetoed && el("span", "tag vetoed", "Achats bloqués"),
      st && r.status !== "held" && r.status !== "veto" && el("span", "tag " + st[0], st[1])].filter(Boolean));
    const why = card.querySelector(".why");
    why.textContent = r.why || "";
    why.hidden = !r.why;
    const box = card.querySelector(".pick input");
    box.checked = !!r.selected;
    box.disabled = SEL.mode === "auto";
    card.classList.toggle("unselected", !r.selected);
    const rk = card.querySelector(".rank");
    rk.textContent = r.rank ? `N° ${r.rank.rank} sur 2 ans : ${fR(r.rank.total_r)} en ${r.rank.trades} trade${r.rank.trades > 1 ? "s" : ""} (achats et ventes)` : "";
    rk.hidden = !r.rank;
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
  table($("#p-table"), ["Crypto", "Achat le", "Prix d'achat", "Cours", "Vente auto si clôture <", "Gain verrouillé", "Écart à la vente", "Résultat", "En R (≈)"],
    pos.positions.map((p) => {
      const locked = p.entry ? (p.stop / p.entry - 1) * 100 : null;
      return { key: p.asset, cells: [up(p.asset), fdate(p.entry_date), fpx(p.entry), fpx(p.price), fpx(p.stop), fpct(locked, 1), fpct(p.stop_dist_pct, 1), fpct(p.pnl_pct), fR(p.r)],
        cls: { 5: (locked || 0) >= 0 ? "up" : "down", 7: (p.pnl_pct || 0) >= 0 ? "up" : "down", 8: (p.r || 0) >= 0 ? "up" : "down" } };
    }), open);
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
  $("#w-report").textContent = w.report_text || "Aucun rapport : la veille tourne chaque jour avec le bot (python trendguard_bot.py watch pour un rapport immédiat).";
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
$("#log-level").addEventListener("change", (e) => {
  withLoader(`Journal : ${e.currentTarget.selectedOptions[0].textContent}…`, () => renderLog().catch(() => {}));
});

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
async function renderSecurity() {
  const s = await api("/api/security");
  $("#s-sec-score").textContent = `${s.ok} / ${s.total}`;
  $("#s-sec").replaceChildren(...s.checks.map((c) => {
    const li = el("li", c.ok === true ? "ok" : c.ok === false ? "warn" : "info");
    li.append(el("span", "sec-ico", c.ok === true ? "✓" : c.ok === false ? "!" : "i"));
    const t = el("span");
    t.append(el("strong", "", c.label), el("span", "sub", " · " + c.detail));
    li.append(t);
    return li;
  }));
}
async function renderSettings() {
  if (!S) await refreshStatus();
  renderSecurity().catch(() => { /* réessai au prochain rafraîchissement */ });
  const au = S.autonomy || {}, sup = au.supervisor || {}, le = sup.last_exit;
  const sw = $("#s-autostart");
  sw.checked = !!au.autostart;
  sw.disabled = au.autostart == null;
  $("#s-auto-os").textContent = { windows: "Windows", macos: "macOS", linux: "Linux" }[au.os] || "";
  const autoRows = [
    ["Relance après une erreur", sup.running ? "active" : "inactive : cliquez sur AUTO"],
    ["Relances automatiques", sup.running ? String(sup.restarts || 0) : "–"],
    ["Dernier arrêt imprévu", le && le.code !== 0 ? `${fdate(le.at)} · ${le.stalled ? "bot bloqué" : "code " + le.code}` : "aucun"],
    ["Mise en veille du PC", au.keep_awake ? "bloquée tant que le bot tourne" : "autorisée"],
    ["Bouton ARRÊTER", "aucune relance, même au démarrage du PC"],
  ];
  $("#s-auto").replaceChildren(...autoRows.flatMap(([k, v]) => [el("dt", "", k), el("dd", "", v)]));
  const dl = $("#s-bot");
  const rows = [["Mode", S.demo ? "Démonstration" : S.mode === "live" ? (S.testnet ? "Réel (testnet)" : "Réel") : "Paper (argent fictif)"],
    ["Risque par trade", `${nf(1).format(S.risk_pct)} %`], ["Positions au plus", String(S.max_positions)],
    ["Risque cumulé au plus", `${nf(0).format(S.max_total_risk_pct)} %`],
    ["Profil prudent", S.dd_throttle.length ? S.dd_throttle.map(([t, m]) => `risque × ${nf(1).format(m)} au-delà de ${nf(0).format(t * 100)} % de baisse`).join(" ; ") : "désactivé"],
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
$("#s-autostart").addEventListener("change", async (e) => {
  const sw = e.currentTarget;
  sw.disabled = true;
  try {
    const r = await api("/api/autostart", { body: { enabled: sw.checked } });
    toast(r.message, r.ok ? "ok" : "err");
    if (!r.ok) sw.checked = !sw.checked;
  } catch (err) {
    toast(err.message, "err");
    sw.checked = !sw.checked;
  }
  sw.disabled = false;
  await refreshStatus();
});
$("#logout-btn").addEventListener("click", async () => {
  try { await api("/api/logout", { body: {} }); } catch { /* déjà déconnecté */ }
  showLogin();
});

// ---------- Assistant (fenêtre de dialogue) ----------
// Garde-fou côté navigateur : une clé, un mot de passe ou un code collés ne
// quittent jamais cette page (le serveur applique le même contrôle).
const CHAT = { history: [], loaded: false, busy: false };
const MASK_TEXT = "🔒 Je suis désolée, j'ai masqué votre message : il semblait contenir une information secrète (clé, mot de passe ou code). Il n'a pas été envoyé. Si c'était une vraie clé ou un vrai mot de passe, je vous conseille de le révoquer : sur Binance, « Gestion des API » → supprimer la clé, puis créez-en une nouvelle avec la saisie masquée.";
// Réflexion de Rachelle : au moins le temps réglé (3 s), plus si la question
// et la réponse sont longues (jusqu'à 3 s de plus).
const thinkMs = (q, a) => {
  const base = waitBase();
  if (!base) return 0;
  const words = q.trim().split(/\s+/).length;
  return base + Math.min(3000, words * 100 + (a || "").length * 1.2);
};
const greeting = () => (new Date().getHours() >= 18 || new Date().getHours() < 5 ? "Bonsoir" : "Bonjour");
const SECRET_RX = [
  /\bsk-[A-Za-z0-9_-]{16,}/,
  /\b(mot de passe|password|passwd|mdp|pin)\s*(est|=|:|is|c'est|c’est)\s*(?=\S*[\d!@#$%^&*_+=?])\S{4,}/i,
  /\b(code|2fa|a2f|otp)\b\D{0,20}\b\d{6}\b/i,
];
const CHAT_STOP = new Set("le la les de du des un une et pour mon ma mes je tu il que qui comment est sur dans au avec pas ne ce on en the to my how is and of for what you it in with do can a i bot panneau".split(" "));
function looksSecret(t) {
  if (SECRET_RX.some((rx) => rx.test(t))) return true;
  const long = t.split(/\s+/).map((w) => w.replace(/^["'`.,;:()[\]{}<>]+|["'`.,;:()[\]{}<>]+$/g, ""))
    .some((w) => w.length >= 32 && !w.includes("/") && !w.includes("www.") && /^[A-Za-z0-9_\-+=]+$/.test(w) && /\d/.test(w) && /[A-Za-z]/.test(w));
  if (long) return true;
  const words = t.trim().split(/\s+/);
  return [12, 15, 18, 21, 24].includes(words.length) && words.every((w) => /^[a-z]{3,8}$/.test(w)) && !words.some((w) => CHAT_STOP.has(w));
}
function inlineRich(node, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el("strong", "", part.slice(2, -2)));
    else if (part) node.append(document.createTextNode(part));
  });
  return node;
}
function richText(node, text) {
  let ul = null;
  text.split("\n").forEach((line) => {
    if (/^- /.test(line)) {
      if (!ul) { ul = el("ul"); node.append(ul); }
      ul.append(inlineRich(el("li"), line.slice(2)));
    } else {
      ul = null;
      if (line.trim()) node.append(inlineRich(el("p"), line));
    }
  });
}
function chatScroll() {
  const log = $("#chat-log");
  log.scrollTo({ top: log.scrollHeight, behavior: reduceMotion.matches ? "auto" : "smooth" });
}
function chatMessage(role, text, extra = {}) {
  const li = el("li", `msg ${role}${extra.refused ? " refused" : ""}`);
  const bubble = el("div", "bubble");
  richText(bubble, text);
  li.append(bubble);
  if (extra.actions && extra.actions.length) {
    const acts = el("div", "msg-actions");
    extra.actions.forEach((a) => {
      const b = el("button", "chip watch");
      b.type = "button";
      b.append(el("span", "", a.label + " ›"));
      b.addEventListener("click", () => {
        location.hash = a.href;
        if (matchMedia("(max-width: 860px)").matches) closeChat();
      });
      acts.append(b);
    });
    li.append(acts);
  }
  $("#chat-log").append(li);
  chatScroll();
  return li;
}
function chatSuggestions(list) {
  $("#chat-sugg").replaceChildren(...(list || []).map((q) => {
    const b = el("button", "chip", q);
    b.type = "button";
    b.addEventListener("click", () => sendChat(q));
    return b;
  }));
}
async function openChat() {
  const box = $("#chat");
  box.hidden = false;
  $("#chat-fab").setAttribute("aria-expanded", "true");
  if (!CHAT.loaded) {
    CHAT.loaded = true;
    try {
      const info = await api("/api/assistant");
      CHAT.info = info;
      chatMessage("bot", `${greeting()} ! ${info.welcome}`);
      chatSuggestions(info.suggestions);
    } catch (e) {
      CHAT.loaded = false;
      chatMessage("bot", `Je suis désolée, je ne peux pas vous répondre pour le moment (${e.message}). Réessayez dans un instant.`);
    }
  }
  $("#chat-input").focus();
}
function closeChat() {
  $("#chat").hidden = true;
  $("#chat-fab").setAttribute("aria-expanded", "false");
  $("#chat-fab").focus();
}
async function sendChat(text) {
  text = (text || "").trim();
  if (!text || CHAT.busy) return;
  const input = $("#chat-input");
  input.value = "";
  input.style.height = "";
  const secret = looksSecret(text);                // rien n'est envoyé
  const userLi = chatMessage("user", secret ? "🔒 •••••• (message masqué)" : text, { refused: secret });
  const typing = el("li", "msg bot typing");
  typing.setAttribute("role", "status");
  const thinking = el("div", "bubble thinking");
  thinking.append(el("span", "spin"), el("span", "", "Rachelle réfléchit…"));
  typing.append(thinking);
  $("#chat-log").append(typing);
  chatScroll();
  CHAT.busy = true;
  $("#chat-send").disabled = true;
  const t0 = performance.now();
  const think = async (answer) => sleep(Math.max(0, thinkMs(secret ? "" : text, answer) - (performance.now() - t0)));
  if (secret) {
    await think("");
    typing.remove();
    chatMessage("bot", MASK_TEXT, { refused: true });
    CHAT.busy = false;
    $("#chat-send").disabled = false;
    return;
  }
  try {
    const r = await api("/api/assistant", { body: { message: text, history: CHAT.history.slice(-6) } });
    await think(r.answer);
    typing.remove();
    if (r.masked) {
      userLi.classList.add("refused");
      userLi.querySelector(".bubble").replaceChildren(el("p", "", "🔒 •••••• (message masqué)"));
    }
    chatMessage("bot", r.answer, { refused: r.refused, actions: r.actions, source: r.source });
    if (!r.refused) {
      CHAT.history.push({ role: "user", text }, { role: "assistant", text: r.answer });
      CHAT.history = CHAT.history.slice(-12);
    }
    if (r.suggestions && r.suggestions.length) chatSuggestions(r.suggestions);
  } catch (e) {
    await think("");
    typing.remove();
    chatMessage("bot", e.message === "connexion requise" ? "Votre session a expiré : reconnectez-vous au panneau, puis reposez-moi la question."
      : `Je suis désolée, je n'ai pas pu vous répondre (${e.message}). Réessayez dans un instant.`);
  } finally {
    CHAT.busy = false;
    $("#chat-send").disabled = false;
  }
}
$("#chat-fab").addEventListener("click", openChat);
$("#chat-close").addEventListener("click", closeChat);
$("#chat-clear").addEventListener("click", () => {
  CHAT.history = [];
  $("#chat-log").replaceChildren();
  if (CHAT.info) {
    chatMessage("bot", `${greeting()} ! ${CHAT.info.welcome}`);
    chatSuggestions(CHAT.info.suggestions);
  }
});
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("#chat-input").value); });
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(e.currentTarget.value); }
});
$("#chat-input").addEventListener("input", (e) => {
  const t = e.currentTarget;
  t.style.height = "";
  t.style.height = Math.min(t.scrollHeight, 120) + "px";
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
const RENDER = { dash: renderDash, news: renderNews, charts: renderCharts, assets: renderAssets, positions: renderPositions, watch: renderWatch, log: renderLog, settings: renderSettings };
const PERIOD = { dash: 15, news: 60, charts: 15, assets: 15, positions: 15, watch: 60, log: 5, settings: 30 };
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
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (D) closeDetail();
  else if (!$("#chat").hidden) closeChat();
});

// ---------- Démarrage ----------
applyTheme(prefs.get("theme", "auto"));
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* application installable : facultatif */ });
}
refreshStatus().then(() => { route(); setInterval(tick, 1000); });
})();
