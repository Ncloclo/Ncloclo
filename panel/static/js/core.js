// Panneau TrendGuard : outils communs (formats, DOM, préférences, temps de
// chargement, API du serveur local, notifications).

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
export const LWC = window.LightweightCharts;
export const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

// ---------- Formats ----------
const nfCache = new Map();
export const nf = (d) => {
  if (!nfCache.has(d)) nfCache.set(d, new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  return nfCache.get(d);
};
export const fpx = (v) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return nf(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 3 : a >= 0.1 ? 4 : 5).format(v);
};
export const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
export const fusd = (v) => (v == null || !isFinite(v) ? "–" : nf(2).format(v) + " USDT");
export const fpct = (v, d = 2) => (v == null || !isFinite(v) ? "–" : sign(v) + nf(d).format(Math.abs(v)) + " %");
export const fR = (v) => (v == null || !isFinite(v) ? "–" : sign(v) + nf(2).format(Math.abs(v)) + " R");
export const fvol = (v) => (v == null ? "–" : v >= 1e9 ? nf(2).format(v / 1e9) + " Md$" : nf(1).format(v / 1e6) + " M$");
export const up = (s) => (s || "").toUpperCase();
const dShort = new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit" });
export const fdate = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? dShort.format(d) : "–";
};
const dTime = new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
// Instant en secondes depuis 1970 (horodatage Python) → « 29/09 07:17 ».
export const ftime = (sec) => (sec == null || !isFinite(sec) ? "–" : dTime.format(new Date(sec * 1000)));
export const fdur = (s) => {
  if (s == null) return "–";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min ${String(sec).padStart(2, "0")}`;
};
export const fage = (s) => (s == null ? "–" : s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);
export const REASON = { STOP: "stop de clôture", EXCHANGE_STOP: "stop catastrophe", DELISTED: "retrait de la cote", STOP_LATE: "stop (rattrapage)" };
// Raisonnement du bot, actif par actif : [classe de l'étiquette, libellé].
export const STATUS = {
  held: ["held", "Détenue"], bought: ["up", "Achetée"], sold: ["down", "Vendue"],
  watch: ["watch", "Sous surveillance"], full: ["watch", "Signal · plafond atteint"],
  bear: ["warn", "Signal · marché baissier"], deferred: ["warn", "Achat différé"],
  cancelled: ["muted", "Achat annulé"], veto: ["vetoed", "Achats bloqués"],
  wait: ["muted", "Pas de cassure"], weak: ["muted", "Tendance faible"],
  illiquid: ["muted", "Peu échangée"], young: ["muted", "Trop récente"],
  nodata: ["muted", "Données insuffisantes"], halted: ["down", "Arrêt d'urgence"],
};
export const CANDIDATE = new Set(["watch", "full", "bear", "deferred"]);

// ---------- Préférences locales ----------
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem("tg:" + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tg:" + k, v); } catch { /* stockage bloqué */ } },
};

// ---------- Temps de réflexion et de chargement ----------
// Chaque passage d'une rubrique ou d'une sélection à une autre affiche un
// chargement d'au moins 3 s (réglable : Réglages ▸ Affichage), pendant que
// les données se chargent réellement dessous.
const WAITS = { 3: 3000, 1: 1000, 0: 0 };
export const waitBase = () => { const v = WAITS[prefs.get("wait", "3")]; return v == null ? 3000 : v; };
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const loaderSeq = {};
export async function withLoader(text, work, target = "loader") {
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
// Session expirée : l'application affiche la connexion (voir onUnauthorized).
let unauthorized = () => {};
export function onUnauthorized(fn) { unauthorized = fn; }
export async function api(path, opts = {}) {
  const init = { headers: { "X-TrendGuard": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (res.status === 401 && path !== "/api/login") { unauthorized(); throw new Error("connexion requise"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export function toast(msg, kind = "") {
  const t = el("div", "toast " + kind, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 320); }, 4500);
}

export function ripple(ev, btn) {
  if (reduceMotion.matches) return;
  const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height);
  const s = el("span", "ripple");
  Object.assign(s.style, { width: size + "px", height: size + "px", left: (ev.clientX - r.left - size / 2) + "px", top: (ev.clientY - r.top - size / 2) + "px" });
  btn.append(s);
  setTimeout(() => s.remove(), 650);
}

export function countUp(node, value, fmt) {
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


export const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
