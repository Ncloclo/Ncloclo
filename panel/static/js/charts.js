// Panneau TrendGuard : graphiques (TradingView Lightweight Charts), marqueurs
// d'achats et de ventes, niveaux d'entrée et de stop.

import { cssVar, el, fpx, fR, LWC, up } from "./core.js";

// ---------- Graphiques (TradingView Lightweight Charts) ----------
export const charts = new Set();
// Options communes ; celles de `extra` complètent chaque groupe (échelle,
// temps, curseur…) au lieu de le remplacer.
export function chartOptions(extra = {}, logo = true) {
  const base = {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: cssVar("--ink-2"), fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: logo },
    grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
    crosshair: { mode: LWC ? LWC.CrosshairMode.Normal : 0 },
    localization: { locale: "fr-FR", priceFormatter: fpx },
  };
  Object.entries(extra || {}).forEach(([k, v]) => {
    const group = v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object";
    base[k] = group ? Object.assign({}, base[k], v) : v;
  });
  return base;
}
// Après un changement de thème : couleurs de base relues, options propres à
// chaque graphique gardées.
export const restyleCharts = () => charts.forEach((c) => c.applyOptions(chartOptions(c.tgExtra, c.tgLogo)));
// Logo TradingView (crédit de la bibliothèque) sur les graphiques détaillés ;
// sur les petits graphiques, le crédit figure dans le menu. `fit` : tracé
// recadré quand la largeur change (pas pour les panneaux synchronisés, qui
// suivent le graphique principal).
export function makeChart(box, extra, logo = true, fit = true) {
  if (!LWC) { box.textContent = "Bibliothèque de graphiques introuvable."; return null; }
  const c = LWC.createChart(box, chartOptions(extra, logo));
  c.tgLogo = logo;
  c.tgExtra = extra || {};
  // En taille automatique, la largeur n'est connue qu'après le premier
  // affichage : ajusté avant, le tracé resterait tassé à droite. Il est donc
  // réajusté dès que la largeur change.
  let width = 0;
  if (fit) c.timeScale().subscribeSizeChange((w) => { if (w !== width) { width = w; c.timeScale().fitContent(); } });
  charts.add(c);
  return c;
}
export function dropChart(c) {
  if (c) { charts.delete(c); c.remove(); }
}
// Point qui bat au dernier prix (courbes en ligne, en zone, de base).
export const LIVE = { lastPriceAnimation: LWC ? LWC.LastPriceAnimationMode.Continuous : 0 };
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
  // Un seul repère par point : six achats le même jour donnent « 6 achats ».
  const groups = new Map();
  const add = (t, side, label) => {
    const key = `${nearest(t).time}|${side}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(label);
  };
  (buys || []).forEach((b) => add(b.t, "buy", up(b.asset)));
  (sells || []).forEach((s) => add(s.t, "sell", `${up(s.asset)} ${fR(s.r)}`));
  groups.forEach((labels, key) => {
    const [time, side] = key.split("|"), buy = side === "buy";
    const text = !withText ? "" : labels.length <= 3 ? labels.join(" · ") : `${labels.length} ${buy ? "achats" : "ventes"}`;
    out.push({ time: Number(time), position: buy ? "belowBar" : "aboveBar", color: buy ? c.up : c.down, shape: buy ? "arrowUp" : "arrowDown", text });
  });
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

// Courbe d'une carte (SVG) : clôtures, achats ▲ et ventes ▼ du bot.
export function fillSpark(svg, closes, marks = []) {
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
  // Dernier point : un rond (trait de longueur nulle, bouts ronds : il reste
  // rond même si la courbe est étirée) et son halo qui bat.
  const [lx, ly] = pts[pts.length - 1];
  const dot = (cls, width) => {
    const d = document.createElementNS(ns, "line");
    [["x1", lx], ["y1", ly], ["x2", lx], ["y2", ly], ["stroke", color], ["stroke-width", width],
      ["stroke-linecap", "round"], ["vector-effect", "non-scaling-stroke"], ["class", cls]].forEach(([k, v]) => d.setAttribute(k, v));
    return d;
  };
  svg.replaceChildren(area, line, dot("spark-halo", 6), dot("spark-dot", 6));
  // Première fois : la courbe se trace de gauche à droite.
  if (!svg.dataset.drawn) { svg.dataset.drawn = "1"; svg.classList.add("reveal"); }
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
