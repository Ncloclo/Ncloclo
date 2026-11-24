// Panneau TrendGuard : zoom et période de toutes les courbes (Lightweight
// Charts). Une période choisie (24 h, 7 j, 1 mois…) glisse avec le temps :
// elle montre toujours la fin de la courbe, à chaque nouvelle donnée. − et +
// zooment autour de la fin de la courbe (autour du centre si l'on regarde le
// passé) ; ↺ revient à la période choisie. La période visible est écrite en
// clair et suit aussi le zoom à la molette, au doigt ou à la souris. Un
// rafraîchissement ne défait jamais un zoom.

import { el } from "./core.js";

const DAY = 86400;
// [clé, libellé, durée en secondes] ; « Tout » : toute la courbe chargée.
export const WINDOWS = [
  ["1d", "24 h", DAY], ["7d", "7 j", 7 * DAY], ["1m", "1 mois", 30 * DAY],
  ["3m", "3 mois", 91 * DAY], ["1y", "1 an", 365 * DAY], ["all", "Tout", 0],
];
export const spanOf = (key) => (WINDOWS.find((w) => w[0] === key) || [0, "", 0])[2];
export const labelOf = (key) => (WINDOWS.find((w) => w[0] === key) || [0, key])[1];
const MIN_BARS = 6;           // jamais moins de 6 points à l'écran
const FACTOR = 1.6;           // un clic sur − ou + : 1,6 fois plus ou moins de temps

// Jours de capital à lire pour une période (la courbe en garde au plus
// 1 500 points : une période courte reste fine).
export const daysFor = (key, auto) => (key === "auto" ? auto
  : { "1d": 2, "7d": 8, "1m": 31, "3m": 92, "1y": 366, all: 3650 }[key] || auto);

// Unité de temps d'une crypto pour une période : celle choisie si elle
// donne entre 12 et 800 bougies, sinon la plus parlante.
export const IV_SEC = { "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
export const IV_NAME = { "15m": "15 min", "1h": "1 h", "4h": "4 h", "1d": "1 jour" };
const IV_FOR = { "1d": "15m", "7d": "1h", "1m": "4h", "3m": "1d", "1y": "1d", all: "1d" };
// Unité la plus parlante pour une période (cartes, courbes des cryptos).
export const preferredInterval = (key) => IV_FOR[key] || "1d";
export function intervalFor(key, current) {
  const span = spanOf(key);
  if (!span) return current;
  const bars = span / (IV_SEC[current] || 3600);
  return bars >= 12 && bars <= 800 ? current : IV_FOR[key];
}
// Bougies à lire : la période et `warm` de plus (moyennes et indicateurs
// calculés dès le bord gauche), entre `min` et 1 000.
export const limitFor = (key, interval, warm, min) =>
  Math.max(min, Math.min(1000, Math.ceil(spanOf(key) / (IV_SEC[interval] || 3600)) + warm));

const hours = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const days = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
const years = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
// Période visible : « 12 nov. → 24 nov. · 12 j », avec les heures sous deux jours.
export function rangeText(from, to) {
  const s = Math.max(0, to - from);
  const a = new Date(from * 1000), b = new Date(to * 1000);
  const fmt = s < 2 * DAY ? hours : a.getFullYear() !== b.getFullYear() ? years : days;
  const n = s < 2 * DAY ? `${Math.max(1, Math.round(s / 3600))} h` : s < 91 * DAY ? `${Math.round(s / DAY)} j`
    : s < 730 * DAY ? `${Math.round(s / (30.44 * DAY))} mois` : `${Math.round(s / (365.25 * DAY))} ans`;
  return `${fmt.format(a)} → ${fmt.format(b)} · ${n}`;
}

// Premier indice de `t` (croissant) dont le temps est au moins `x`.
function lowerBound(t, x) {
  let lo = 0, hi = t.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (t[mid] < x) lo = mid + 1; else hi = mid;
  }
  return lo;
}

export class TimeZoom {
  // `chart` : graphique principal. `parts` : éléments de la barre, tous
  // facultatifs ({ windows, zoomIn, zoomOut, reset, label }). Options :
  //  keys : périodes proposées ; extend : toujours toutes proposées (le
  //  graphique recharge ses données pour la période, voir onWindow) ;
  //  win : période de départ ; span : durée fixe de la période « auto » ;
  //  interactive : molette et glisser actifs sur le graphique ;
  //  onWindow(clé) : vrai si le graphique recharge ses données (le cadrage
  //  se fera à leur arrivée) ; before() : avant chaque cadrage.
  constructor(chart, parts = {}, opts = {}) {
    this.chart = chart;
    this.parts = parts;
    this.opts = opts;
    this.keys = opts.keys || WINDOWS.map((w) => w[0]);
    this.win = opts.win || "all";
    this.fixed = opts.span || 0;
    this.free = false;                // zoom fait à la main
    this.times = [];
    this.ahead = 0;
    this.shown = null;
    if (!chart) return;
    if (parts.label) chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.writeRange());
    if (opts.interactive !== false) {
      const box = chart.chartElement(), manual = () => this.manual();
      box.addEventListener("wheel", manual, { passive: true });
      box.addEventListener("touchmove", manual, { passive: true });
      box.addEventListener("pointermove", (e) => { if (e.buttons) manual(); });
    }
    const on = (node, fn) => node && node.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    on(parts.zoomIn, () => this.zoomIn());
    on(parts.zoomOut, () => this.zoomOut());
    on(parts.reset, () => this.reset());
    this.renderWindows();
  }
  // Nouvelles données (temps croissants du graphique principal) : la
  // période choisie glisse jusqu'au dernier point ; un zoom manuel reste.
  data(times, ahead = 0) {
    this.times = times || [];
    this.ahead = ahead;
    this.renderWindows();
    if (!this.free) this.apply();
    this.writeRange();
  }
  span() { return this.win === "auto" ? this.fixed : spanOf(this.win); }
  step() {
    const t = this.times, n = t.length;
    return n > 1 ? (t[n - 1] - t[0]) / (n - 1) : DAY;
  }
  available(key) {
    if (key === "all" || key === "auto" || this.opts.extend) return true;
    const t = this.times, span = spanOf(key);
    if (t.length < 2) return false;
    return span < (t[t.length - 1] - t[0]) * 0.98 && span / this.step() >= MIN_BARS;
  }
  renderWindows() {
    const box = this.parts.windows;
    if (!box) return;
    const keys = this.keys.filter((k) => this.available(k)), sig = keys.join(",");
    if (sig !== this.shown) {
      this.shown = sig;
      box.replaceChildren(...keys.map((k) => {
        const b = el("button", "", labelOf(k));
        b.type = "button";
        b.dataset.win = k;
        b.title = spanOf(k) ? `Les ${labelOf(k)} les plus récents : la période glisse avec le temps` : "Toute la courbe";
        b.addEventListener("click", (e) => { e.stopPropagation(); this.choose(k); });
        return b;
      }));
    }
    this.press();
  }
  press() {
    if (this.parts.windows) this.parts.windows.querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(!this.free && b.dataset.win === this.win)));
  }
  choose(key) {
    this.win = key;
    this.free = false;
    this.press();
    if (this.opts.onWindow && this.opts.onWindow(key)) return;
    this.apply();
  }
  // Cadrage de la période : du premier point de la période au dernier.
  apply() {
    const t = this.times, n = t.length, span = this.span();
    if (!this.chart || !n) return;
    if (this.opts.before) this.opts.before();
    const ts = this.chart.timeScale();
    if (!span || t[n - 1] - t[0] <= span) { ts.fitContent(); return; }
    const from = Math.min(lowerBound(t, t[n - 1] - span), Math.max(0, n - MIN_BARS));
    ts.setVisibleLogicalRange({ from: from - 0.4, to: n - 0.5 + (this.ahead || 0) + (ts.options().rightOffset || 0) });
  }
  // − et + : la fin de la courbe reste à droite si elle est visible.
  zoom(factor) {
    if (!this.chart || !this.times.length) return;
    const ts = this.chart.timeScale(), r = ts.getVisibleLogicalRange(), n = this.times.length;
    if (!r) return;
    const width = Math.min(Math.max((r.to - r.from) * factor, MIN_BARS), n * 1.1 + 10);
    const to = r.to >= n - 1 ? r.to : (r.from + r.to) / 2 + width / 2;
    if (this.opts.before) this.opts.before();
    ts.setVisibleLogicalRange({ from: to - width, to });
    this.manual();
  }
  zoomIn() { this.zoom(1 / FACTOR); }
  zoomOut() { this.zoom(FACTOR); }
  // La période choisie reprend la main au prochain tracé (nouvelle unité
  // de temps d'une crypto, par exemple).
  resume() {
    this.free = false;
    this.press();
  }
  manual() {
    if (this.free) return;
    this.free = true;
    this.press();
  }
  // ↺ : retour à la période choisie, qui glisse de nouveau avec le temps.
  reset() {
    this.free = false;
    this.press();
    this.apply();
  }
  // Période écrite : de la première à la dernière bougie dont le centre est
  // à l'écran (une bougie à peine entamée au bord ne compte pas).
  writeRange() {
    const t = this.times, n = t.length;
    if (!this.parts.label || !this.chart || !n) return;
    const r = this.chart.timeScale().getVisibleLogicalRange();
    if (!r) return;
    const i = Math.min(n - 1, Math.max(0, Math.ceil(r.from))), j = Math.min(n - 1, Math.max(i, Math.floor(r.to)));
    this.parts.label.textContent = rangeText(t[i], t[j]);
  }
}

// Barre d'une carte ou du tableau de bord : périodes (si `keys`), période
// visible, −, + et ↺. Un clic dans la barre n'ouvre pas le détail de la carte.
export function zoomBar(keys) {
  const root = el("div", "zoom-bar");
  root.setAttribute("role", "toolbar");
  root.setAttribute("aria-label", "Zoom et période du graphique");
  let windows = null;
  if (keys) {
    windows = el("div", "seg zoom-windows");
    windows.setAttribute("role", "group");
    windows.setAttribute("aria-label", "Période affichée");
  }
  const label = el("span", "zoom-range", "—");
  const button = (icon, text) => {
    const b = el("button", "icon-btn small");
    b.type = "button";
    b.title = text;
    b.setAttribute("aria-label", text);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + icon);
    svg.append(use);
    b.append(svg);
    return b;
  };
  const zoomOut = button("i-zoom-out", "Dézoomer : voir plus de temps");
  const zoomIn = button("i-zoom-in", "Zoomer : voir moins de temps");
  const reset = button("i-fit", "Recadrer : revenir à la période choisie");
  const steps = el("div", "zoom-steps");
  steps.append(zoomOut, zoomIn, reset);
  root.append(...[windows, label, steps].filter(Boolean));
  root.addEventListener("click", (e) => e.stopPropagation());
  root.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") e.stopPropagation(); });
  return { root, parts: { windows, zoomIn, zoomOut, reset, label } };
}
