// Panneau TrendGuard : graphique détaillé, comme sur une plateforme de
// trading. Prix en bougies, barres, Heikin Ashi ou ligne, indicateurs choisis
// superposés, volume et oscillateurs dans des panneaux dessous, tous
// synchronisés (zoom, défilement, curseur) ; légende vivante des valeurs
// sous le curseur ; lecture technique de la dernière bougie.
// Information seulement : le bot ne suit que sa règle (cassure, momentum,
// marché BTC) et ses stops, que le serveur calcule avec la stratégie même.

import { cssVar, el, fpx, LWC, nf } from "./core.js";
import { addPositionLines, COLORS, dropChart, LIVE, makeChart, tradeMarkers } from "./charts.js";
import * as ti from "./indicateurs.js";

export const STEP = { "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
export const MAX_PANES = 4;        // panneaux sous le prix
export const MAX_COLORS = 6;       // couleurs d'indicateurs sur le prix (palette validée)
const AHEAD = 25;                  // barres du nuage d'Ichimoku projetées dans le futur
const AXIS_W = 90;                 // même largeur d'échelle partout : panneaux alignés
const LS = LWC ? LWC.LineStyle : { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 };
const slotColor = (k) => cssVar(`--s${k + 1}`);

// ---------- Formats ----------
const minus = (v) => (v < 0 ? "−" : "");
const fixed = (d) => (v) => (v == null || !isFinite(v) ? "–" : minus(v) + nf(d).format(Math.abs(v)));
const signed = (d) => (v) => (v == null || !isFinite(v) ? "–" : (v > 0 ? "+" : minus(v)) + nf(d).format(Math.abs(v)));
const price = (v) => (v == null || !isFinite(v) ? "–" : minus(v) + fpx(Math.abs(v)));
const f1 = fixed(1), f2 = fixed(2), s2 = signed(2);
// Décimales d'un écart de prix (MACD, ATR) selon le niveau du cours :
// 84 000 → 1, 155 → 2, 14 → 3, 0,25 → 5.
export const decimalsFor = (px) => (px > 0 && isFinite(px) ? Math.min(6, Math.max(1, 4 - Math.floor(Math.log10(px)))) : 2);
const fpctS = (v) => (v == null || !isFinite(v) ? "–" : signed(2)(v) + " %");
export const fcompact = (v) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v), s = minus(v);
  if (a >= 1e9) return s + nf(2).format(a / 1e9) + " Md";
  if (a >= 1e6) return s + nf(2).format(a / 1e6) + " M";
  if (a >= 1e3) return s + nf(1).format(a / 1e3) + " k";
  return s + nf(a >= 10 || a === 0 ? 0 : 2).format(a);
};

// Bougies de l'API ([t, o, h, b, c, volume]) : une par instant, dans l'ordre.
export function candleRows(candles) {
  const rows = [];
  (candles || []).forEach((r) => {
    const row = { time: r[0], open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5] };
    if (!rows.length || row.time > rows[rows.length - 1].time) rows.push(row); else rows[rows.length - 1] = row;
  });
  return rows;
}

// ---------- Calculs d'une série de bougies (mis en cache) ----------
class Ctx {
  constructor(rows, interval, strategy) {
    this.rows = rows;
    this.interval = interval;
    this.strategy = strategy || null;
    this.close = rows.map((r) => r.close);
    this.vol = rows.map((r) => r.volume);
    this.memo = new Map();
  }
  get(key, fn) { if (!this.memo.has(key)) this.memo.set(key, fn()); return this.memo.get(key); }
  sma(n) { return this.get("sma" + n, () => ti.sma(this.close, n)); }
  ema(n) { return this.get("ema" + n, () => ti.ema(this.close, n)); }
  rsi() { return this.get("rsi", () => ti.rsi(this.close, 14)); }
  macd() { return this.get("macd", () => ti.macd(this.close)); }
  stoch() { return this.get("stoch", () => ti.stochastic(this.rows)); }
  adx() { return this.get("adx", () => ti.adx(this.rows)); }
  atr() { return this.get("atr", () => ti.atr(this.rows, 14)); }
  bollinger() { return this.get("bb", () => ti.bollinger(this.close)); }
  keltner() { return this.get("kc", () => ti.keltner(this.rows)); }
  donchian() { return this.get("dc", () => ti.donchian(this.rows, 20)); }
  ichimoku() { return this.get("ichi", () => ti.ichimoku(this.rows, 9, 26, 52, AHEAD)); }
  supertrend() { return this.get("st", () => ti.supertrend(this.rows)); }
  psar() { return this.get("sar", () => ti.psar(this.rows)); }
  vwap() { return this.get("vwap", () => ti.vwap(this.rows)); }
  obv() { return this.get("obv", () => ti.obv(this.rows)); }
  mfi() { return this.get("mfi", () => ti.mfi(this.rows)); }
  cci() { return this.get("cci", () => ti.cci(this.rows)); }
  wr() { return this.get("wr", () => ti.williamsR(this.rows)); }
  roc() { return this.get("roc", () => ti.roc(this.close, 12)); }
  volSma() { return this.get("vsma", () => ti.sma(this.vol, 20)); }
  // Valeur journalière de la règle du bot sur chaque bougie (1 : cassure,
  // 2 : volatilité, 3 : momentum), du jour UTC de la bougie.
  daily(k) {
    return this.get("daily" + k, () => {
      const days = new Map(((this.strategy || {}).days || []).map((d) => [d[0], d[k]]));
      return this.rows.map((r) => { const v = days.get(Math.floor(r.time / 86400) * 86400); return v == null ? null : v; });
    });
  }
}

// ---------- Catalogue des indicateurs ----------
export const FAMILIES = [
  ["trend", "Tendance"], ["bands", "Canaux et volatilité"], ["levels", "Niveaux"],
  ["osc", "Oscillateurs (élan)"], ["volume", "Volume"], ["bot", "Règle du bot"],
];
const line = (label, values, color, width = 2, style = LS.Solid, more = {}) => ({ label, values, color, width, style, ...more });
const levelsOf = (...vs) => vs;

export const INDICATORS = [
  { id: "sma20", name: "MM 20", family: "trend", slot: 0,
    help: "Moyenne des 20 dernières clôtures : la tendance courte.",
    traces: (c, col) => [line("MM 20", c.sma(20), col, 2, LS.Solid, { title: "MM 20" })] },
  { id: "sma50", name: "MM 50", family: "trend", slot: 1,
    help: "Moyenne des 50 dernières clôtures : la tendance moyenne.",
    traces: (c, col) => [line("MM 50", c.sma(50), col, 2, LS.Solid, { title: "MM 50" })] },
  { id: "sma200", name: "MM 200", family: "trend", slot: 2,
    help: "Moyenne des 200 dernières clôtures : la tendance de fond. Prix au-dessus : tendance haussière.",
    traces: (c, col) => [line("MM 200", c.sma(200), col, 2, LS.Solid, { title: "MM 200" })] },
  { id: "ema", name: "MME 12 et 26", short: "MME 12 · 26", family: "trend", slot: 3,
    help: "Moyennes exponentielles, plus réactives aux derniers cours ; leur écart forme le MACD.",
    traces: (c, col) => [line("MME 12", c.ema(12), col, 2), line("MME 26", c.ema(26), col, 1, LS.Dashed)] },
  { id: "vwap", name: "VWAP de séance", short: "VWAP", family: "trend", slot: 4, intraday: true,
    help: "Prix moyen pondéré par les volumes depuis 00:00 UTC : la référence des grands acheteurs (bougies de moins d'un jour).",
    traces: (c, col) => [line("VWAP", c.vwap(), col, 2, LS.Solid, { title: "VWAP" })] },
  { id: "ichimoku", name: "Ichimoku (9, 26, 52)", short: "Ichimoku", family: "trend", slot: 5, ahead: true,
    help: "Tenkan, Kijun et nuage projeté 26 périodes en avant : au-dessus du nuage, tendance haussière ; dedans, indécision.",
    traces: (c, col) => {
      const k = c.ichimoku();
      return [line("Tenkan", k.tenkan, col, 1), line("Kijun", k.kijun, col, 2), line("Nuage A", k.spanA, col, 1, LS.Dashed),
        line("Nuage B", k.spanB, col, 2, LS.Dotted), line("Lagging", k.chikou, col, 1, LS.SparseDotted, { noRead: true })];
    } },
  { id: "supertrend", name: "Supertrend (10, 3)", short: "Supertrend", family: "trend",
    help: "Stop suiveur : sous les prix en tendance haussière (vert), au-dessus en tendance baissière (rouge).",
    traces: (c) => {
      const s = c.supertrend(), k = COLORS();
      return [line("haussier", s.line.map((v, i) => (s.dir[i] === 1 ? v : null)), k.up, 2),
        line("baissier", s.line.map((v, i) => (s.dir[i] === -1 ? v : null)), k.down, 2)];
    } },
  { id: "sar", name: "SAR parabolique", short: "SAR", family: "trend",
    help: "Points sous les prix en hausse, au-dessus en baisse ; un changement de côté signale un retournement.",
    traces: (c) => {
      const s = c.psar(), k = COLORS();
      return [{ label: "SAR", values: s.sar, dots: true, color: k.muted, colors: s.dir.map((d) => (d === 1 ? k.up : d === -1 ? k.down : null)) }];
    } },
  { id: "bollinger", name: "Bandes de Bollinger (20, 2)", short: "Bollinger", family: "bands", slot: 5,
    help: "Moyenne 20 ± 2 écarts types : bandes resserrées, calme avant un mouvement ; prix hors des bandes, excès.",
    traces: (c, col) => { const b = c.bollinger(); return [line("haute", b.upper, col, 1), line("moyenne", b.mid, col, 1, LS.Dashed), line("basse", b.lower, col, 1)]; } },
  { id: "keltner", name: "Canal de Keltner (20, 2 ATR)", short: "Keltner", family: "bands", slot: 4,
    help: "Moyenne exponentielle 20 ± 2 ATR : une clôture hors du canal marque une forte poussée.",
    traces: (c, col) => { const k = c.keltner(); return [line("haut", k.upper, col, 1), line("milieu", k.mid, col, 1, LS.Dashed), line("bas", k.lower, col, 1)]; } },
  { id: "donchian", name: "Canal de Donchian (20)", short: "Donchian", family: "bands", slot: 2,
    help: "Plus haut et plus bas des 20 dernières bougies : sortir par le haut est une cassure, le principe d'achat du bot.",
    traces: (c, col) => { const d = c.donchian(); return [line("haut", d.upper, col, 1), line("milieu", d.mid, col, 1, LS.Dotted), line("bas", d.lower, col, 1)]; } },
  { id: "atr", name: "ATR 14", family: "bands",
    pane: { fmt: price },
    help: "Amplitude moyenne d'une bougie : la volatilité. Le bot place ses stops en multiples de sa propre volatilité.",
    traces: (c) => [line("ATR", c.atr(), slotColor(0), 2)] },
  { id: "pivots", name: "Points pivots", family: "levels",
    help: "Pivot P, résistances R1 à R3 et supports S1 à S3 de la séance précédente (de la semaine précédente en bougies d'un jour).",
    lines: (c) => {
      const p = ti.pivots(c.rows, c.interval === "1d" ? 604800 : 86400);
      return p ? [["R3", p.R3], ["R2", p.R2], ["R1", p.R1], ["P", p.P], ["S1", p.S1], ["S2", p.S2], ["S3", p.S3]] : [];
    } },
  { id: "fibo", name: "Retracements de Fibonacci", short: "Fibonacci", family: "levels",
    help: "Paliers de 23,6 % à 78,6 % du grand mouvement affiché : zones de repli fréquentes avant la reprise.",
    lines: (c) => {
      const f = ti.fibonacci(c.rows);
      return f ? f.levels.map((l) => [`Fibo ${nf(1).format(l.ratio * 100)} %`, l.price]) : [];
    } },
  { id: "volume", name: "Volume", family: "volume",
    pane: { fmt: fcompact },
    help: "Quantité échangée par bougie (verte en hausse, rouge en baisse) et sa moyenne 20 : un mouvement sur fort volume est plus fiable.",
    traces: (c) => {
      const k = COLORS();
      return [{ label: "Volume", values: c.vol, hist: true, color: k.muted, colors: c.rows.map((r) => (r.close >= r.open ? k.up : k.down) + "88") },
        line("moyenne 20", c.volSma(), slotColor(0), 1)];
    } },
  { id: "obv", name: "OBV (volume cumulé)", short: "OBV", family: "volume",
    pane: { fmt: fcompact },
    help: "Volume ajouté les jours de hausse, retiré les jours de baisse : confirme, ou non, la tendance des prix.",
    traces: (c) => [line("OBV", c.obv(), slotColor(0), 2)] },
  { id: "mfi", name: "MFI 14 (flux monétaire)", short: "MFI 14", family: "volume",
    pane: { fmt: f1, range: [0, 100], levels: levelsOf(80, 20) },
    help: "RSI pondéré par le volume : au-dessus de 80, suracheté ; sous 20, survendu.",
    traces: (c) => [line("MFI", c.mfi(), slotColor(0), 2)] },
  { id: "rsi", name: "RSI 14", family: "osc",
    pane: { fmt: f1, range: [0, 100], levels: levelsOf(70, 50, 30) },
    help: "Force relative de 0 à 100 : au-dessus de 70, suracheté ; sous 30, survendu ; au-dessus de 50, élan haussier.",
    traces: (c) => [line("RSI", c.rsi(), slotColor(0), 2)] },
  { id: "macd", name: "MACD (12, 26, 9)", short: "MACD", family: "osc",
    pane: { fmt: price, levels: levelsOf(0) },
    help: "Écart des moyennes 12 et 26 (ligne) et sa moyenne 9 (signal) : ligne au-dessus du signal, élan haussier.",
    traces: (c) => {
      const m = c.macd(), k = COLORS();
      return [{ label: "histogramme", values: m.hist, hist: true, color: k.muted, colors: m.hist.map((v) => (v == null ? null : (v >= 0 ? k.up : k.down) + "99")) },
        line("MACD", m.macd, slotColor(0), 2), line("signal", m.signal, slotColor(1), 1)];
    } },
  { id: "stoch", name: "Stochastique (14, 3, 3)", short: "Stochastique", family: "osc",
    pane: { fmt: f1, range: [0, 100], levels: levelsOf(80, 20) },
    help: "Place de la clôture entre le plus haut et le plus bas de 14 bougies : au-dessus de 80, suracheté ; sous 20, survendu.",
    traces: (c) => { const s = c.stoch(); return [line("%K", s.k, slotColor(0), 2), line("%D", s.d, slotColor(1), 1)]; } },
  { id: "adx", name: "ADX et DMI 14", short: "ADX 14", family: "osc",
    pane: { fmt: f1, levels: levelsOf(25) },
    help: "Force de la tendance (ADX) : au-dessus de 25, tendance nette ; +DI au-dessus de −DI, les acheteurs dominent.",
    traces: (c) => { const a = c.adx(), k = COLORS(); return [line("ADX", a.adx, slotColor(0), 2), line("+DI", a.plusDI, k.up, 1), line("−DI", a.minusDI, k.down, 1)]; } },
  { id: "cci", name: "CCI 20", family: "osc",
    pane: { fmt: f1, levels: levelsOf(100, 0, -100) },
    help: "Écart du prix à sa moyenne : au-dessus de +100, forte poussée ; sous −100, forte baisse.",
    traces: (c) => [line("CCI", c.cci(), slotColor(0), 2)] },
  { id: "wr", name: "Williams %R 14", short: "Williams %R", family: "osc",
    pane: { fmt: f1, range: [-100, 0], levels: levelsOf(-20, -80) },
    help: "Comme le stochastique, de −100 à 0 : au-dessus de −20, suracheté ; sous −80, survendu.",
    traces: (c) => [line("%R", c.wr(), slotColor(0), 2)] },
  { id: "roc", name: "ROC 12 (variation)", short: "ROC 12", family: "osc",
    pane: { fmt: (v) => fpctS(v), levels: levelsOf(0) },
    help: "Variation du prix sur 12 bougies, en pour cent : au-dessus de 0, hausse.",
    traces: (c) => [line("ROC", c.roc(), slotColor(0), 2)] },
  { id: "botBreakout", name: "Cassure du bot (30 jours)", short: "Cassure du bot", family: "bot",
    help: "Plus haut des 30 clôtures journalières précédentes : le bot achète quand la clôture du jour le dépasse, si son momentum est positif et le marché BTC haussier. Son stop initial est donné dans la lecture technique.",
    traces: (c) => {
      const s = c.strategy;
      if (!s) return [];
      return [line(`cassure ${s.breakout_n} j`, c.daily(1), COLORS().ink, 2, LS.Dashed, { step: c.interval !== "1d", title: "Cassure" })];
    } },
  { id: "botMom", name: "Momentum du bot (90 jours)", short: "Momentum du bot", family: "bot",
    pane: { fmt: s2, levels: levelsOf(0) },
    help: "Rendement de 90 jours rapporté à sa volatilité, journalier : le bot n'achète que s'il est positif.",
    traces: (c) => [line(`momentum ${(c.strategy || {}).mom_n || 90} j`, c.daily(3), slotColor(0), 2, LS.Solid, { step: c.interval !== "1d" })] },
];
const BY_ID = new Map(INDICATORS.map((d) => [d.id, d]));
const PANE_ORDER = ["volume", "rsi", "macd", "stoch", "adx", "mfi", "obv", "cci", "wr", "roc", "atr", "botMom"];

export const PRESETS = [
  ["essentiel", "Essentiel", ["volume", "sma50", "sma200", "rsi", "botBreakout"]],
  ["tendance", "Tendance", ["sma20", "sma50", "sma200", "macd", "adx"]],
  ["elan", "Élan", ["volume", "rsi", "macd", "stoch"]],
  ["volatilite", "Volatilité", ["bollinger", "keltner", "atr", "volume"]],
  ["volume", "Volume", ["volume", "vwap", "obv", "mfi"]],
  ["ichimoku", "Ichimoku", ["ichimoku", "volume"]],
  ["bot", "Règle du bot", ["botBreakout", "sma200", "volume", "botMom"]],
  ["aucun", "Aucun", []],
];
export const DEFAULT_IDS = PRESETS[0][2];
// Indicateurs connus, dans les limites de lisibilité (choix enregistrés compris).
export function known(ids) {
  let panes = 0, colors = 0;
  return INDICATORS.filter((d) => ids.includes(d.id)).filter((d) => {
    if (d.pane && ++panes > MAX_PANES) return false;
    return !(d.slot != null && ++colors > MAX_COLORS);
  }).map((d) => d.id);
}

// Données d'une série : un point par instant du graphique (vide s'il manque),
// pour que tous les panneaux partagent exactement le même axe du temps.
function seriesData(times, tr) {
  return times.map((t, i) => {
    const v = tr.values[i];
    if (v == null || !isFinite(v)) return { time: t };
    const d = { time: t, value: v };
    if (tr.colors && tr.colors[i]) d.color = tr.colors[i];
    return d;
  });
}
function addTrace(chart, tr, range, precision) {
  const auto = range ? { autoscaleInfoProvider: () => ({ priceRange: { minValue: range[0], maxValue: range[1] } }) } : {};
  if (precision != null) auto.priceFormat = { type: "price", precision, minMove: 10 ** -precision };
  if (tr.hist) return chart.addHistogramSeries({ color: tr.color, priceLineVisible: false, lastValueVisible: false, base: 0, ...auto });
  return chart.addLineSeries({
    color: tr.color, lineWidth: tr.width || 2, lineStyle: tr.style || LS.Solid, lineType: tr.step && LWC ? LWC.LineType.WithSteps : 0,
    lineVisible: !tr.dots, pointMarkersVisible: !!tr.dots, pointMarkersRadius: 2,
    priceLineVisible: false, lastValueVisible: !!tr.title, title: tr.title || "", crosshairMarkerVisible: false, ...auto,
  });
}
const swatch = (color, dashed) => {
  const i = el("i", dashed ? "dash" : "");
  if (dashed) i.style.color = color; else i.style.background = color;
  return i;
};

// ---------- Pile de graphiques synchronisés ----------
const mainOptions = () => ({ rightPriceScale: { minimumWidth: AXIS_W, scaleMargins: { top: 0.08, bottom: 0.08 } }, timeScale: { rightOffset: 4 } });
const paneOptions = (fmt, fixedRange) => ({
  rightPriceScale: { minimumWidth: AXIS_W, scaleMargins: fixedRange ? { top: 0.12, bottom: 0.06 } : { top: 0.18, bottom: 0.08 } },
  timeScale: { visible: false, rightOffset: 4 }, localization: { priceFormatter: fmt },
});
export class ChartStack {
  constructor(host, onHover) {
    this.host = host;
    this.onHover = onHover || (() => {});
    this.root = el("div", "stack");
    this.mainBox = el("div", "pane pane-main");
    this.root.append(this.mainBox);
    host.append(this.root);
    this.panes = [];
    this.leader = null;
    this.main = makeChart(this.mainBox, mainOptions());
    if (this.main) this.link(this.main, this.mainBox);
    this.root.addEventListener("pointerleave", () => {
      this.leader = null;
      this.all().forEach((c) => c.clearCrosshairPosition());
      this.onHover(null);
    });
    this.layout();
  }
  all() { return [this.main, ...this.panes.map((p) => p.chart)].filter(Boolean); }
  addPane(id, order, fmt, fixedRange = false) {
    const box = el("div", "pane"), legendBox = el("div", "pane-legend");
    box.dataset.pane = id;
    box.append(legendBox);
    const p = { id, order, box, legend: legendBox, series: [], traces: [] };
    const at = this.panes.filter((x) => x.order < order).length;
    this.panes.splice(at, 0, p);
    const next = this.panes[at + 1];
    this.root.insertBefore(box, next ? next.box : null);
    p.fmt = fmt;
    p.chart = makeChart(box, paneOptions(fmt, fixedRange), false, false);
    if (p.chart) this.link(p.chart, box);
    this.layout();
    return p;
  }
  removePane(id) {
    const i = this.panes.findIndex((p) => p.id === id);
    if (i < 0) return;
    const [p] = this.panes.splice(i, 1);
    dropChart(p.chart);
    p.box.remove();
    this.layout();
  }
  // Hauteur selon le nombre de panneaux ; l'axe du temps sous le dernier.
  layout() {
    this.host.style.setProperty("--panes", String(this.panes.length));
    const last = this.panes[this.panes.length - 1];
    if (this.main) this.main.applyOptions({ timeScale: { visible: !last } });
    this.panes.forEach((p) => p.chart && p.chart.applyOptions({ timeScale: { visible: p === last } }));
    this.follow();
  }
  // Les panneaux reprennent la fenêtre de temps du graphique principal.
  follow() {
    const r = this.main && this.main.timeScale().getVisibleLogicalRange();
    if (r) this.panes.forEach((p) => p.chart && p.chart.timeScale().setVisibleLogicalRange(r));
  }
  // Zoom et défilement suivent le graphique manipulé ; le curseur aussi, avec
  // sa ligne horizontale sur ce seul graphique.
  link(chart, box) {
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (!r || (this.leader || this.main) !== chart) return;
      this.all().forEach((o) => { if (o !== chart) o.timeScale().setVisibleLogicalRange(r); });
    });
    box.addEventListener("pointerenter", () => {
      this.leader = chart;
      this.all().forEach((o) => o.applyOptions({ crosshair: { horzLine: { visible: o === chart, labelVisible: o === chart } } }));
    });
    chart.subscribeCrosshairMove((param) => {
      if (this.leader !== chart) return;
      const t = param && param.time != null ? param.time : null;
      this.all().forEach((o) => {
        if (o === chart) return;
        const v = t != null && o.tgValueAt ? o.tgValueAt(t) : null;
        if (v == null || !o.tgPrimary) o.clearCrosshairPosition();
        else o.setCrosshairPosition(v, t, o.tgPrimary);
      });
      this.onHover(t);
    });
  }
  fit() { if (this.main) { this.leader = null; this.main.timeScale().fitContent(); } }
  // Échelle des prix du graphique principal : prix, logarithmique ou %.
  scale(kind) {
    if (!this.main || !LWC) return;
    const mode = { log: LWC.PriceScaleMode.Logarithmic, pct: LWC.PriceScaleMode.Percentage }[kind] || LWC.PriceScaleMode.Normal;
    this.main.priceScale("right").applyOptions({ mode });
  }
  destroy() {
    this.all().forEach(dropChart);
    this.root.remove();
    this.host.style.removeProperty("--panes");
  }
}

// ---------- Graphique d'une crypto ----------
export class AssetChart {
  constructor(host, readout, { ids = DEFAULT_IDS, ctype = "candles", scale = "normal" } = {}) {
    this.host = host;
    this.readout = readout;
    this.active = new Set(known(ids));
    this.ctype = ctype;
    this.scale = scale;
    this.colors = new Map();          // indicateur → case de la palette (gardée tant qu'il est affiché)
    this.overlays = new Map();        // indicateur → séries sur le prix
    this.levelLines = [];
    this.posLines = [];
    this.data = null;
    this.ctx = null;
    this.times = [];
    this.index = new Map();
    this.stack = new ChartStack(host, (t) => this.show(t));
    this.makeMain();
    this.applyScale();
  }
  get chart() { return this.stack.main; }
  makeMain() {
    const m = this.stack.main, c = COLORS();
    if (!m) return;
    if (this.series) { this.overlays.forEach((o) => o.series.forEach((s) => m.removeSeries(s))); this.overlays.clear(); m.removeSeries(this.series); }
    this.levelLines = [];
    this.posLines = [];
    this.series = this.ctype === "line" ? m.addLineSeries({ color: c.ink, lineWidth: 2 })
      : this.ctype === "bars" ? m.addBarSeries({ upColor: c.up, downColor: c.down, thinBars: false })
        : m.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false });
    this.precision = null;
    m.tgPrimary = this.series;
    m.tgValueAt = (t) => { const i = this.index.get(t); return i != null && this.ctx && i < this.ctx.rows.length ? this.ctx.close[i] : null; };
  }
  setType(ctype) { if (ctype === this.ctype) return; this.ctype = ctype; this.makeMain(); this.draw(); }
  applyScale() { this.stack.scale(this.scale); }
  // Pour le zoom (zoom.js) : temps des bougies et points vides d'avance
  // (projection du nuage d'Ichimoku).
  zoomData() {
    const n = this.ctx ? this.ctx.rows.length : 0;
    return [this.ctx ? this.ctx.rows.map((r) => r.time) : [], this.times ? Math.max(0, this.times.length - n) : 0];
  }
  setScale(scale) { this.scale = scale; this.applyScale(); }
  // Un indicateur de plus ou de moins ; refusé (avec la raison) au-delà des
  // limites qui gardent le graphique lisible.
  toggle(id, on) {
    const d = BY_ID.get(id);
    if (!d) return null;
    if (on && !this.active.has(id)) {
      if (d.pane && this.paneCount() >= MAX_PANES) return `${MAX_PANES} panneaux au plus sous le prix : retirez-en un d'abord.`;
      if (d.slot != null && this.colorCount() >= MAX_COLORS) return `${MAX_COLORS} indicateurs en couleur au plus sur le prix : retirez-en un d'abord.`;
      this.active.add(id);
    } else if (!on) this.active.delete(id);
    this.draw();
    return null;
  }
  setIds(ids) { this.active = new Set(known(ids)); this.draw(); }
  ids() { return INDICATORS.filter((d) => this.active.has(d.id)).map((d) => d.id); }
  paneCount() { return INDICATORS.filter((d) => d.pane && this.active.has(d.id)).length; }
  colorCount() { return INDICATORS.filter((d) => d.slot != null && this.active.has(d.id)).length; }
  usable(d) { return !(d.intraday && this.interval === "1d") && !(d.family === "bot" && !(this.ctx && this.ctx.strategy)); }
  colorOf(id) { const k = this.colors.get(id); return k == null ? null : slotColor(k); }
  update(d) {
    this.data = d;
    this.interval = d.interval;
    const rows = candleRows(d.candles);
    this.ctx = new Ctx(rows, d.interval, d.strategy);
    this.dec = decimalsFor(rows.length ? rows[rows.length - 1].close : null);
    this.draw();
  }
  draw() {
    const m = this.stack.main, c = this.ctx;
    if (!m || !c) return;
    const step = STEP[this.interval] || 3600, rows = c.rows;
    const ahead = this.active.has("ichimoku") ? AHEAD : 0;
    this.times = rows.map((r) => r.time);
    for (let k = 1; k <= ahead && rows.length; k++) this.times.push(rows[rows.length - 1].time + k * step);
    this.index = new Map(this.times.map((t, i) => [t, i]));
    // Couleurs : chaque indicateur garde la sienne tant qu'il est affiché.
    [...this.colors.keys()].forEach((id) => { if (!this.active.has(id)) this.colors.delete(id); });
    INDICATORS.forEach((d) => {
      if (d.slot == null || !this.active.has(d.id) || this.colors.has(d.id)) return;
      const used = new Set(this.colors.values());
      this.colors.set(d.id, used.has(d.slot) ? [0, 1, 2, 3, 4, 5].find((k) => !used.has(k)) : d.slot);
    });
    // Prix.
    const shown = this.ctype === "heikin" ? ti.heikinAshi(rows) : rows;
    const main = this.ctype === "line" ? shown.map((r) => ({ time: r.time, value: r.close }))
      : shown.map((r) => ({ time: r.time, open: r.open, high: r.high, low: r.low, close: r.close }));
    for (let k = rows.length; k < this.times.length; k++) main.push({ time: this.times[k] });
    this.shown = shown;
    const precision = Math.max(2, (this.dec || 2) + 1);       // cours : un chiffre de plus que les écarts
    if (precision !== this.precision) {
      this.precision = precision;
      this.series.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    }
    this.series.setData(main);
    this.series.setMarkers(tradeMarkers(this.data.markers, COLORS()));
    this.posLines.forEach((l) => this.series.removePriceLine(l));
    this.posLines = addPositionLines(this.series, this.data.position);
    // Indicateurs sur le prix.
    const want = INDICATORS.filter((d) => !d.pane && d.traces && this.active.has(d.id) && this.usable(d));
    this.overlays.forEach((o, id) => {
      if (!want.some((d) => d.id === id)) { o.series.forEach((s) => m.removeSeries(s)); this.overlays.delete(id); }
    });
    want.forEach((d) => {
      const color = this.colorOf(d.id), traces = d.traces(c, color);
      let o = this.overlays.get(d.id);
      if (!o || o.color !== color || o.series.length !== traces.length) {
        if (o) o.series.forEach((s) => m.removeSeries(s));
        o = { color, series: traces.map((tr) => addTrace(m, tr)) };
        this.overlays.set(d.id, o);
      }
      o.traces = traces;
      traces.forEach((tr, k) => o.series[k].setData(seriesData(this.times, tr)));
    });
    // Niveaux (pivots, Fibonacci) : lignes horizontales.
    this.levelLines.forEach((l) => this.series.removePriceLine(l));
    this.levelLines = [];
    INDICATORS.filter((d) => d.lines && this.active.has(d.id)).forEach((d) => d.lines(c).forEach(([title, value]) => {
      if (value != null && isFinite(value)) {
        this.levelLines.push(this.series.createPriceLine({ price: value, color: COLORS().muted, lineWidth: 1, lineStyle: LS.Dashed, axisLabelVisible: true, title }));
      }
    }));
    // Panneaux.
    const panes = PANE_ORDER.map((id) => BY_ID.get(id)).filter((d) => this.active.has(d.id) && this.usable(d));
    this.stack.panes.slice().forEach((p) => { if (!panes.some((d) => d.id === p.id)) this.stack.removePane(p.id); });
    panes.forEach((d) => {
      let p = this.stack.panes.find((x) => x.id === d.id);
      const traces = d.traces(c);
      if (!p) {
        const priceLike = d.pane.fmt === price;
        const fmt = priceLike ? (v) => fixed(this.dec)(v) : d.pane.fmt;
        p = this.stack.addPane(d.id, PANE_ORDER.indexOf(d.id), fmt, !!d.pane.range);
        if (!p.chart) return;
        p.series = traces.map((tr) => addTrace(p.chart, tr, d.pane.range, priceLike ? this.dec : null));
        const primary = p.series[traces.findIndex((tr) => !tr.hist)] || p.series[0];
        (d.pane.levels || []).forEach((v) => primary.createPriceLine({ price: v, color: COLORS().muted, lineWidth: 1, lineStyle: LS.Dashed, axisLabelVisible: false, title: "" }));
        p.chart.tgPrimary = primary;
        p.chart.tgValueAt = (t) => { const i = this.index.get(t), tr = (p.traces || [])[p.series.indexOf(p.chart.tgPrimary)]; return i != null && tr ? tr.values[i] : null; };
      }
      p.traces = traces;
      traces.forEach((tr, k) => p.series[k] && p.series[k].setData(seriesData(this.times, tr)));
    });
    this.stack.follow();
    this.show(null);
  }
  fit() { this.stack.fit(); }
  // Légende vivante : valeurs de la bougie sous le curseur (la dernière sinon).
  show(t) {
    const c = this.ctx;
    if (!c || !c.rows.length) return;
    let i = t == null ? c.rows.length - 1 : this.index.get(t);
    if (i == null) return;
    i = Math.min(i, this.times.length - 1);
    const r = i < c.rows.length ? (this.shown || c.rows)[i] : null;
    const first = el("div", "ro-line");
    if (r) {
      const prev = i > 0 ? (this.shown || c.rows)[i - 1].close : r.open, ch = (r.close / prev - 1) * 100;
      const kind = { heikin: "Heikin Ashi · ", line: "", bars: "", candles: "" }[this.ctype];
      first.append(`${kind}O ${fpx(r.open)}  H ${fpx(r.high)}  B ${fpx(r.low)}  C ${fpx(r.close)}  `, el("b", ch >= 0 ? "up" : "down", fpctS(ch)),
        `  Vol ${fcompact(c.rows[i].volume)}`);
    } else first.append("Projection du nuage d'Ichimoku");
    const items = el("div", "ro-line ro-ind");
    INDICATORS.forEach((d) => {
      const o = this.overlays.get(d.id);
      if (!o || !o.traces) return;
      const vals = o.traces.filter((tr) => !tr.noRead).map((tr) => tr.values[i]).filter((v) => v != null && isFinite(v));
      if (!vals.length) return;
      const tr = o.traces.find((x) => x.values[i] != null) || o.traces[0];
      const color = tr.colors && tr.colors[i] ? tr.colors[i] : tr.color;
      const name = d.id === "supertrend" ? `Supertrend ${tr.label}` : d.short || d.name;
      const s = el("span", "ro-item");
      s.append(swatch(color, tr.style === LS.Dashed), `${name} `, el("b", "", vals.map(price).join(" · ")));
      items.append(s);
    });
    this.readout.replaceChildren(first, ...(items.childNodes.length ? [items] : []));
    this.stack.panes.forEach((p) => {
      const d = BY_ID.get(p.id);
      const head = el("strong", "", d.short || d.name);
      const parts = (p.traces || []).map((tr) => {
        const v = tr.values[i];
        const s = el("span", "ro-item");
        const color = tr.hist ? (tr.colors && tr.colors[i]) || tr.color : tr.color;
        s.append(swatch(color, tr.style === LS.Dashed), `${tr.label} `, el("b", "", (p.fmt || d.pane.fmt)(v)));
        return s;
      });
      p.legend.replaceChildren(head, ...parts);
    });
  }
  destroy() { this.stack.destroy(); }
}

// ---------- Courbe du capital et régime : courbe + un panneau ----------
const baseline = (up, down, width = 2) => ({
  baseValue: { type: "price", price: 0 }, lineWidth: width, topLineColor: up, bottomLineColor: down,
  topFillColor1: up === "transparent" ? up : up + "44", topFillColor2: up === "transparent" ? up : up + "05",
  bottomFillColor1: down + "05", bottomFillColor2: down + "44",
});
const legendItem = (color, label, value) => {
  const s = el("span", "ro-item");
  s.append(swatch(color), `${label} `, el("b", "", value));
  return s;
};
export class EquityChart {
  constructor(host, readout) {
    const c = COLORS();
    this.readout = readout;
    this.points = [];
    this.stack = new ChartStack(host, (t) => this.show(t));
    const m = this.stack.main;
    if (!m) return;
    this.series = m.addBaselineSeries({ ...baseline(c.up, c.down), ...LIVE });
    m.tgPrimary = this.series;
    this.pane = this.stack.addPane("dd", 0, (v) => fpctS(v));
    if (!this.pane.chart) return;
    this.dd = this.pane.chart.addBaselineSeries({ ...baseline(c.down, c.down, 1), topFillColor1: "transparent", topFillColor2: "transparent", priceLineVisible: false });
    this.pane.chart.tgPrimary = this.dd;
  }
  // Capital (points {time, value}) et sa baisse depuis le plus haut.
  update(points, fmt) {
    if (!this.series) return [];
    // Valeurs prêtes avant le tracé : le curseur peut les lire pendant.
    let peak = -Infinity;
    const dd = points.map((p) => { peak = Math.max(peak, p.value); return { time: p.time, value: peak > 0 ? (p.value / peak - 1) * 100 : 0 }; });
    this.points = points;
    this.fmt = fmt;
    this.value = new Map(points.map((p) => [p.time, p.value]));
    this.drop = new Map(dd.map((p) => [p.time, p.value]));
    this.stack.main.tgValueAt = (t) => this.value.get(t);
    const start = points.length ? points[0].value : 0;
    this.series.applyOptions({ baseValue: { type: "price", price: start } });
    this.series.setData(points);
    if (this.dd) { this.dd.setData(dd); this.pane.chart.tgValueAt = (t) => this.drop.get(t); }
    this.stack.follow();
    this.show(null);
    return dd;
  }
  zoomData() { return [this.points.map((p) => p.time), 0]; }
  show(t) {
    if (!this.points.length || !this.value) return;
    const time = t == null ? this.points[this.points.length - 1].time : t;
    const v = this.value.get(time), d = this.drop.get(time), c = COLORS();
    if (v == null) return;
    this.readout.replaceChildren(el("div", "ro-line", `Capital ${this.fmt(v)}`));
    if (this.pane) this.pane.legend.replaceChildren(el("strong", "", "Baisse depuis le plus haut"), legendItem(c.down, "", fpctS(d)));
  }
  fit() { this.stack.fit(); }
  setScale(kind) { this.stack.scale(kind); }
  destroy() { this.stack.destroy(); }
}

// Une courbe et sa moyenne longue, l'écart dessous : le régime du bot (BTC
// et sa moyenne de 150 jours) ou un marché hors cryptos (sa propre tendance,
// son niveau de cassure et sa position fictive). Options : label, unit
// (« j » ou « séances »), words (au-dessus, en dessous), breakout.
export class RegimeChart {
  constructor(host, readout, opts = {}) {
    const c = COLORS();
    this.opts = { label: "BTC", unit: "j", long: "jours", words: ["achats permis", "aucun achat"], breakout: false, ...opts };
    this.readout = readout;
    this.lines = [];
    this.points = [];
    this.stack = new ChartStack(host, (t) => this.show(t));
    const m = this.stack.main;
    if (!m) return;
    this.btc = m.addLineSeries({ color: c.ink, lineWidth: 2, ...LIVE });
    this.sma = m.addLineSeries({ color: c.sma, lineWidth: 2, crosshairMarkerVisible: false });
    if (this.opts.breakout) {
      this.high = m.addLineSeries({ color: c.accent, lineWidth: 1, lineStyle: LS.Dashed, crosshairMarkerVisible: false,
        priceLineVisible: false, lastValueVisible: false });
    }
    m.tgPrimary = this.btc;
    this.pane = this.stack.addPane("gap", 0, (v) => fpctS(v));
    if (!this.pane.chart) return;
    this.gap = this.pane.chart.addBaselineSeries({ ...baseline(c.up, c.down, 1), priceLineVisible: false });
    this.pane.chart.tgPrimary = this.gap;
  }
  update(reg) {
    if (!this.btc) return;
    // Valeurs prêtes avant le tracé : le curseur peut les lire pendant.
    this.reg = reg;
    this.points = reg.points;
    this.byTime = new Map(reg.points.map((p) => [p.t, p]));
    this.stack.main.tgValueAt = (t) => (this.byTime.get(t) || {}).close;
    this.btc.setData(reg.points.map((p) => ({ time: p.t, value: p.close })));
    const avg = reg.points.filter((p) => p.sma != null);      // marché coté depuis peu : pas encore de moyenne
    this.sma.setData(avg.map((p) => ({ time: p.t, value: p.sma })));
    this.btc.setMarkers(tradeMarkers(reg.markers, COLORS()));
    if (this.high) this.high.setData(reg.points.filter((p) => p.high != null).map((p) => ({ time: p.t, value: p.high })));
    this.lines.forEach((l) => this.btc.removePriceLine(l));
    this.lines = addPositionLines(this.btc, reg.position);       // position fictive : entrée et stop
    const gap = avg.map((p) => ({ time: p.t, value: (p.close / p.sma - 1) * 100 }));
    if (this.gap) {
      this.gap.setData(gap);
      this.pane.chart.tgValueAt = (t) => { const p = this.byTime.get(t); return p ? (p.close / p.sma - 1) * 100 : null; };
    }
    this.stack.follow();
    this.show(null);
  }
  zoomData() { return [this.points.map((p) => p.t), 0]; }
  show(t) {
    if (!this.points.length || !this.byTime) return;
    const p = t == null ? this.points[this.points.length - 1] : this.byTime.get(t), c = COLORS();
    if (!p) return;
    const gap = p.sma == null ? null : (p.close / p.sma - 1) * 100;
    const line1 = el("div", "ro-line ro-ind");
    const o = this.opts;
    line1.append(legendItem(c.ink, o.label, fpx(p.close)), legendItem(c.sma, `Moyenne ${this.reg.sma} ${o.unit}`, fpx(p.sma)));
    if (this.high && p.high != null) line1.append(legendItem(c.accent, "Cassure si au-dessus de", fpx(p.high)));
    this.readout.replaceChildren(line1);
    if (this.pane) {
      this.pane.legend.replaceChildren(el("strong", "", `Écart à la moyenne ${this.reg.sma} ${o.long}`),
        gap == null ? el("span", "", "historique trop court") : legendItem(gap >= 0 ? c.up : c.down, gap >= 0 ? o.words[0] : o.words[1], fpctS(gap)));
    }
  }
  fit() { this.stack.fit(); }
  setScale(kind) { this.stack.scale(kind); }
  destroy() { this.stack.destroy(); }
}

// ---------- Lecture rapide d'une carte ----------
// Quatre repères de la dernière bougie : [texte, sens (up, down, warn ou
// ""), explication]. Information seulement, comme la lecture complète.
export function quickReading(candles) {
  const rows = candleRows(candles);
  if (rows.length < 35) return [];
  const c = new Ctx(rows, null, null), i = rows.length - 1, px = c.close[i], out = [];
  const m = c.sma(50)[i] ?? c.sma(20)[i], n = c.sma(50)[i] != null ? 50 : 20;
  if (m != null) out.push([`Tendance ${px >= m ? "▲" : "▼"}`, px >= m ? "up" : "down", `Prix ${px >= m ? "au-dessus" : "en dessous"} de sa moyenne ${n} (${fpctS((px / m - 1) * 100)})`]);
  const r = c.rsi()[i];
  if (r != null) out.push([`RSI ${f1(r)}`, r >= 70 || r <= 30 ? "warn" : r >= 50 ? "up" : "down", r >= 70 ? "Suracheté : repli possible" : r <= 30 ? "Survendu : rebond possible" : r >= 50 ? "Élan haussier" : "Élan baissier"]);
  const h = c.macd().hist[i];
  if (h != null) out.push([`MACD ${h >= 0 ? "▲" : "▼"}`, h >= 0 ? "up" : "down", h >= 0 ? "MACD au-dessus de son signal" : "MACD sous son signal"]);
  const vs = c.volSma()[i];
  if (vs) out.push([`Volume × ${f1(c.vol[i] / vs)}`, "", "Volume de la dernière bougie rapporté à sa moyenne 20"]);
  return out;
}

// ---------- Lecture technique de la dernière bougie ----------
// Signaux classiques, expliqués : haussier (up), baissier (down), neutre ("")
// ou à surveiller (warn, compté neutre). Le bot ne s'en sert pas.
export function technicalReading(chart, bull) {
  const c = chart && chart.ctx;
  if (!c || c.rows.length < 2) return null;
  const i = c.rows.length - 1, px = c.close[i], rows = [], vote = { up: 0, down: 0, flat: 0 };
  const add = (label, text, cls = "", counts = true) => {
    rows.push([label, text, cls]);
    if (counts) vote[cls === "up" ? "up" : cls === "down" ? "down" : "flat"]++;
  };
  const m200 = c.sma(200)[i], m50 = c.sma(50)[i];
  if (m200 != null) add("Prix et MM 200", `${fpctS((px / m200 - 1) * 100)} : ${px >= m200 ? "au-dessus, tendance de fond haussière" : "en dessous, tendance de fond baissière"}`, px >= m200 ? "up" : "down");
  if (m50 != null && m200 != null) add("MM 50 et MM 200", m50 >= m200 ? "MM 50 au-dessus (croisement haussier)" : "MM 50 en dessous (croisement baissier)", m50 >= m200 ? "up" : "down");
  const md = c.macd().hist[i];
  if (md != null) add("MACD", md >= 0 ? "au-dessus de son signal : élan haussier" : "sous son signal : élan baissier", md >= 0 ? "up" : "down");
  const r = c.rsi()[i];
  if (r != null) {
    const z = r >= 70 ? ["suracheté, repli possible", "warn"] : r <= 30 ? ["survendu, rebond possible", "warn"] : r >= 50 ? ["élan haussier", "up"] : ["élan baissier", "down"];
    add("RSI 14", `${f1(r)} : ${z[0]}`, z[1]);
  }
  const st = c.stoch(), k = st.k[i], dd = st.d[i];
  if (k != null && dd != null) {
    const z = k >= 80 ? ["suracheté", "warn"] : k <= 20 ? ["survendu", "warn"] : k >= dd ? ["%K au-dessus de %D", "up"] : ["%K sous %D", "down"];
    add("Stochastique", `${f1(k)} : ${z[0]}`, z[1]);
  }
  const ax = c.adx();
  if (ax.adx[i] != null) {
    const a = ax.adx[i], buyers = ax.plusDI[i] >= ax.minusDI[i];
    if (a >= 25) add("ADX 14", `${f1(a)} : tendance nette, ${buyers ? "acheteurs" : "vendeurs"} dominants`, buyers ? "up" : "down");
    else add("ADX 14", `${f1(a)} : ${a < 20 ? "pas de tendance" : "tendance faible"}`);
  }
  const sd = c.supertrend().dir[i];
  if (sd) add("Supertrend", sd === 1 ? "haussier" : "baissier", sd === 1 ? "up" : "down");
  const pd = c.psar().dir[i];
  if (pd) add("SAR parabolique", pd === 1 ? "sous les prix : haussier" : "au-dessus des prix : baissier", pd === 1 ? "up" : "down");
  const ich = c.ichimoku(), a = ich.spanA[i], b = ich.spanB[i];
  if (a != null && b != null) {
    const top = Math.max(a, b), bot = Math.min(a, b);
    add("Ichimoku", px > top ? "au-dessus du nuage : haussier" : px < bot ? "sous le nuage : baissier" : "dans le nuage : indécis", px > top ? "up" : px < bot ? "down" : "");
  }
  const obv = c.obv(), obvM = ti.sma(obv, 20)[i];
  if (obvM != null) add("OBV", obv[i] >= obvM ? "au-dessus de sa moyenne : le volume accompagne la hausse" : "sous sa moyenne : le volume accompagne la baisse", obv[i] >= obvM ? "up" : "down");
  const bb = c.bollinger(), pb = bb.pctB[i];
  if (pb != null) add("Bollinger", `%B ${f2(pb)}, largeur ${f1(bb.width[i] * 100)} %${pb > 1 ? " : au-dessus de la bande haute" : pb < 0 ? " : sous la bande basse" : ""}`, pb > 1 || pb < 0 ? "warn" : "", false);
  const at = c.atr()[i];
  if (at != null) add("ATR 14", `${price(at)}, soit ${f2(at / px * 100)} % du prix par bougie`, "", false);
  const vs = c.volSma()[i];
  if (vs) add("Volume", `${f1(c.vol[i] / vs)} × sa moyenne 20`, "", false);
  // Règle du bot (information : c'est elle seule qui décide).
  const bot = [], s = c.strategy;
  if (s && s.days && s.days.length) {
    const today = s.days[s.days.length - 1], ph = today[1];
    const closed = s.days.filter((d) => d[2] != null), last = closed[closed.length - 1];
    if (ph != null) {
      const gap = (px / ph - 1) * 100;
      bot.push([`Cassure ${s.breakout_n} jours`, px >= ph ? `${price(ph)} : prix au-dessus (${fpctS(gap)}), achat possible à la clôture` : `${price(ph)} : il manque ${fpctS(-gap)}`, px >= ph ? "up" : ""]);
      if (last) bot.push(["Stop initial si achat", `≈ ${price(ph - s.init_stop_atr * last[2])} (${fpctS(-s.init_stop_atr * last[2] / ph * 100)})`, ""]);
    }
    if (last && last[3] != null) bot.push([`Momentum ${s.mom_n} jours`, `${s2(last[3])} : ${last[3] > 0 ? "positif" : "négatif, pas d'achat"}`, last[3] > 0 ? "up" : "down"]);
  }
  if (bull != null) bot.push(["Marché BTC", bull ? "haussier : achats permis" : "baissier : aucun achat", bull ? "up" : "down"]);
  return { rows, vote, bot };
}

// ---------- Menu des indicateurs ----------
export function renderIndicatorMenu(box, chart) {
  const groups = FAMILIES.map(([fam, title]) => {
    const fs = el("fieldset", "ind-family");
    fs.append(el("legend", "", title));
    INDICATORS.filter((d) => d.family === fam).forEach((d) => {
      const lab = el("label", "ind-item");
      const cb = el("input");
      cb.type = "checkbox";
      cb.dataset.ind = d.id;
      cb.checked = chart.active.has(d.id);
      const sw = el("i", "ind-sw");
      const color = chart.colorOf(d.id);
      if (cb.checked && color) sw.style.background = color;
      const text = el("span", "ind-text");
      const name = el("span", "ind-name", d.name);
      const where = el("span", "ind-where", d.pane ? "panneau" : d.lines ? "niveaux" : "sur le prix");
      name.append(where);
      text.append(name, el("small", "ind-help", d.help));
      const off = !chart.usable(d) && chart.ctx;
      if (off) {
        cb.disabled = true;
        text.append(el("small", "ind-off", d.intraday ? "En bougies de 15 min, 1 h ou 4 h." : "Règle du bot indisponible (cours journaliers non lus)."));
      }
      lab.append(cb, sw, text);
      fs.append(lab);
    });
    return fs;
  });
  box.replaceChildren(...groups);
}
