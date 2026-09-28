// Panneau TrendGuard : graphiques (TradingView Lightweight Charts), marqueurs
// d'achats et de ventes, niveaux d'entrée et de stop.

import { cssVar, el, fpx, fR, LWC, up } from "./core.js";

// ---------- Graphiques (TradingView Lightweight Charts) ----------
export const charts = new Set();
export function chartOptions(extra = {}, logo = true) {
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
export function makeChart(box, extra, logo = true) {
  if (!LWC) { box.textContent = "Bibliothèque de graphiques introuvable."; return null; }
  const c = LWC.createChart(box, chartOptions(extra, logo));
  c.tgLogo = logo;
  charts.add(c);
  return c;
}
export function dropChart(c) {
  if (c) { charts.delete(c); c.remove(); }
}
export const COLORS = () => ({ up: cssVar("--up"), down: cssVar("--down"), accent: cssVar("--accent"), ink: cssVar("--ink"), muted: cssVar("--muted"), warn: cssVar("--warn"), sma: "#eb6834" });
export const uniq = (pts) => {                 // temps strictement croissants
  const out = [];
  pts.forEach((p) => { if (!out.length || p.time > out[out.length - 1].time) out.push(p); else out[out.length - 1] = p; });
  return out;
};
export const candleData = (rows) => uniq(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })));
// Achats et ventes du bot : flèches sur les bougies, au prix payé.
export const tradeMarkers = (list, c) => (list || []).map((m) => ({
  time: m.t, position: m.type === "buy" ? "belowBar" : "aboveBar", color: m.type === "buy" ? c.up : c.down,
  shape: m.type === "buy" ? "arrowUp" : "arrowDown",
  text: m.type === "buy" && m.price != null ? `${m.text} ${fpx(m.price)}` : m.text,
}));
// Achats sur la courbe du capital : chacun sur le point le plus proche.
export function buyMarkersOn(points, buys, c, withText = true, sells = []) {
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
export function gridInterval(p) {
  const age = p.entry_date ? Math.max(0, (Date.now() - Date.parse(p.entry_date)) / 3600e3) : 0;
  if (!isFinite(age) || age <= 60) return ["1h", 72, "1 h"];
  if (age <= 24 * 12) return ["4h", Math.min(500, Math.ceil(age / 4) + 30), "4 h"];
  return ["1d", Math.min(500, Math.ceil(age / 24) + 20), "1 jour"];
}

export function addPositionLines(series, pos) {
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
export function legend(box, items) {
  box.replaceChildren(...items.map(([color, label, dashed]) => {
    const s = el("span");
    const i = el("i", dashed ? "dash" : "");
    i.style.background = dashed ? "" : color;
    if (dashed) i.style.color = color;
    s.append(i, label);
    return s;
  }));
}
