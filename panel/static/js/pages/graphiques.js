// Panneau TrendGuard, page « Graphiques » : capital, régime BTC et chaque
// position en temps réel. Cartes vivantes : bougies avec moyennes 20 et 50
// et volume, point qui bat au dernier prix, lecture rapide (tendance, RSI,
// MACD, volume) et valeur qui s'illumine quand elle change. Un clic ouvre
// l'analyse complète (detail.js).

import { $, api, cssVar, el, fdate, flashValue, fpct, fpx, fR, fusd, up } from "../core.js";
import { addPositionLines, buyMarkersOn, candleData, COLORS, dropChart, gridInterval, legend, LIVE, makeChart, tradeMarkers, uniq } from "../charts.js";
import { candleRows, quickReading } from "../analyse.js";
import { sma } from "../indicateurs.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";

const WARMUP = 60;                  // bougies de plus, pour les moyennes 20 et 50
const gridCharts = new Map();       // id → carte : graphique, séries, cadrage

// Cadrage : les `lim` dernières bougies d'une crypto, toute la courbe sinon ;
// refait quand la largeur change.
function frame(g) {
  if (!g.chart || !g.bars) return;
  if (g.lim) g.chart.timeScale().setVisibleLogicalRange({ from: g.bars - g.lim, to: g.bars + 1 });
  else g.chart.timeScale().fitContent();
}

function newCard(s, k) {
  const c = COLORS();
  const card = el("article", "card chart-card");
  card.style.animationDelay = (k * 60) + "ms";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${s.title} : ouvrir le détail`);
  const head = el("div", "head"), title = el("h2", "", s.title);
  if (s.kind !== "equity") {                       // cours de marché : en direct
    const live = el("span", "live-tag");
    live.append(el("i"), el("span", "", "Direct"));
    title.append(live);
  }
  head.append(title, el("span", "val", "…"));
  const lg = el("div", "legend");
  legend(lg, s.legend);
  const box = el("div", "chart-box");
  const foot = el("div", "chart-foot");
  foot.append(el("div", "mini-reading"), el("span", "open-hint", "Cliquer pour l'analyse complète"));
  card.append(head, lg, box, foot);
  const open = () => openDetail(s);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  const chart = makeChart(box, { handleScroll: false, handleScale: false }, false, false);
  const g = { chart, card, lines: [], lim: 0, bars: 0 };
  if (!chart) return g;
  chart.timeScale().subscribeSizeChange(() => frame(g));
  const thin = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
  if (s.kind === "equity") {
    g.main = chart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, topLineColor: c.up, topFillColor1: c.up + "44", topFillColor2: c.up + "05",
      bottomLineColor: c.down, bottomFillColor1: c.down + "05", bottomFillColor2: c.down + "44", lineWidth: 2, priceLineVisible: false, ...LIVE });
  } else if (s.kind === "regime") {
    g.main = chart.addLineSeries({ color: c.ink, lineWidth: 2, priceLineVisible: false, ...LIVE });
    g.sma = chart.addLineSeries({ color: c.sma, lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false });
  } else {
    // Volume dans une bande à part en bas, sous les bougies.
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.24 } });
    g.vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    g.main = chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false });
    g.ma20 = chart.addLineSeries({ ...thin, color: cssVar("--s1") });
    g.ma50 = chart.addLineSeries({ ...thin, color: cssVar("--s2") });
  }
  return g;
}

// Valeur de la carte, qui s'illumine quand le nombre suivi change.
function setVal(g, text, cls, num) {
  const v = g.card.querySelector(".val");
  v.textContent = text;
  v.className = "val " + (cls || "");
  if (num != null) {
    flashValue(v, Number(v.dataset.v), num);
    v.dataset.v = num;
  }
}
function setReading(g, items) {
  g.card.querySelector(".mini-reading").replaceChildren(...items.map(([text, cls, why]) => {
    const t = el("span", "tag " + (cls || "muted"), text);
    if (why) t.title = why;
    return t;
  }));
}
function setLive(g, stale) {
  const tag = g.card.querySelector(".live-tag");
  if (!tag) return;
  tag.classList.toggle("stale", !!stale);
  tag.lastChild.textContent = stale ? "En retard" : "Direct";
}

// Bougies d'une crypto avec leurs moyennes 20 et 50, leur volume, les
// achats et ventes du bot ; rend la réponse de l'API.
async function drawAsset(g, asset, entryDate) {
  const c = COLORS(), [iv, lim] = gridInterval({ entry_date: entryDate });
  const d = await api(`/api/candles?asset=${asset}&interval=${iv}&limit=${lim + WARMUP}`);
  const rows = candleRows(d.candles), closes = rows.map((r) => r.close);
  const line = (vals) => rows.map((r, i) => (vals[i] == null ? { time: r.time } : { time: r.time, value: vals[i] }));
  g.main.setData(candleData(d.candles));
  g.vol.setData(rows.map((r) => ({ time: r.time, value: r.volume, color: (r.close >= r.open ? c.up : c.down) + "55" })));
  g.ma20.setData(line(sma(closes, 20)));
  g.ma50.setData(line(sma(closes, 50)));
  g.main.setMarkers(tradeMarkers(d.markers, c));
  const first = !g.bars;
  g.lim = lim;
  g.bars = rows.length;
  if (first) frame(g);
  setLive(g, d.stale);
  setReading(g, quickReading(d.candles));
  return d;
}

async function renderCharts() {
  const [eq, reg, pos, tr] = await Promise.all([api("/api/equity?days=90"), api("/api/regime"), api("/api/positions"), api("/api/trades")]);
  const c = COLORS();
  const held = new Set(pos.positions.map((p) => p.asset));
  const month = Date.now() - 30 * 86400e3, soldSeen = new Set();
  const sold = tr.trades.filter((t) => t.entry_date && Date.parse(t.date) >= month && !held.has(t.asset)
    && !soldSeen.has(t.asset) && soldSeen.add(t.asset));
  const assetLegend = [[c.up, "Bougies"], [cssVar("--s1"), "MM 20"], [cssVar("--s2"), "MM 50"], [c.muted, "Volume"]];
  const specs = [
    { id: "equity", kind: "equity", title: "Capital du bot", legend: [[c.accent, "Capital (USDT)"], [c.muted, "Capital de départ", true], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes du bot"]] },
    { id: "regime", kind: "regime", title: `Régime BTC · moyenne ${reg.sma} jours`, legend: [[c.ink, "BTC (clôture)"], [c.sma, `Moyenne ${reg.sma} j`]] },
    ...pos.positions.map((p) => ({ id: "asset:" + p.asset, kind: "asset", asset: p.asset, title: `${up(p.asset)}/USDT · ${gridInterval(p)[2]}`, pos: p,
      legend: [...assetLegend, [c.up, "▲ Achats du bot"], [c.accent, "Entrée", true], [c.down, "Stop de clôture (vente)"], [c.down, "Stop catastrophe", true]] })),
    ...sold.map((t) => ({ id: "sold:" + t.asset, kind: "asset", asset: t.asset, sold: t,
      title: `${up(t.asset)}/USDT · vendue · ${gridInterval({ entry_date: t.entry_date })[2]}`,
      legend: [...assetLegend, [c.up, "▲ Achat du bot"], [c.down, "▼ Vente du bot"]] })),
  ];
  const grid = $("#chart-grid");
  const ids = specs.map((s) => s.id).join("|");
  if (grid.dataset.ids !== ids) {
    gridCharts.forEach((g) => dropChart(g.chart));
    gridCharts.clear();
    grid.dataset.ids = ids;
    grid.replaceChildren(...specs.map((s, k) => {
      const g = newCard(s, k);
      gridCharts.set(s.id, g);
      return g.card;
    }));
  }

  // Capital : au-dessus ou en dessous du départ, achats et ventes du bot.
  const ge = gridCharts.get("equity");
  if (ge && ge.main) {
    const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
    ge.main.applyOptions({ baseValue: { type: "price", price: pts.length ? pts[0].value : 0 } });
    ge.main.setData(pts);
    ge.main.setMarkers(buyMarkersOn(pts, eq.buys, c, true, eq.sells));
    ge.bars = pts.length;
    frame(ge);
    const last = pts[pts.length - 1], first = pts[0];
    let peak = -Infinity, worst = 0;
    pts.forEach((p) => { peak = Math.max(peak, p.value); worst = Math.min(worst, p.value / peak - 1); });
    setVal(ge, last ? fusd(last.value) : "—", "", last ? last.value : null);
    setReading(ge, last && first ? [
      [`90 j ${fpct((last.value / first.value - 1) * 100, 1)}`, last.value >= first.value ? "up" : "down", "Variation du capital sur la courbe affichée"],
      [`Baisse max ${fpct(worst * 100, 1)}`, worst < -0.1 ? "down" : "", "Plus forte baisse depuis un plus haut, sur la courbe"],
      [`${eq.buys.length} achat${eq.buys.length > 1 ? "s" : ""}`, "", "Achats du bot sur la période"],
    ] : []);
  }

  // Régime : BTC et sa moyenne ; l'écart décide des achats.
  const gr = gridCharts.get("regime");
  if (gr && gr.main) {
    gr.main.setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.close }))));
    gr.sma.setData(uniq(reg.points.map((p) => ({ time: p.t, value: p.sma }))));
    gr.main.setMarkers(tradeMarkers(reg.markers, c));
    gr.bars = reg.points.length;
    frame(gr);
    const l = reg.points[reg.points.length - 1];
    setVal(gr, reg.bull ? "Haussier" : "Baissier", reg.bull ? "up" : "down");
    setLive(gr, reg.stale);
    setReading(gr, l ? [
      [`BTC ${fpx(l.close)}`, "", "Dernière clôture de BTC"],
      [`Écart ${fpct((l.close / l.sma - 1) * 100, 1)}`, l.close >= l.sma ? "up" : "down", `Écart à la moyenne ${reg.sma} jours : au-dessus, achats permis`],
      [reg.bull ? "Achats permis" : "Aucun achat", reg.bull ? "up" : "warn", "Règle du bot : BTC au-dessus de sa moyenne"],
    ] : []);
  }

  await Promise.all([
    ...sold.map(async (t) => {
      const g = gridCharts.get("sold:" + t.asset);
      if (!g || !g.main) return;
      await drawAsset(g, t.asset, t.entry_date);
      setVal(g, `${fR(t.r)} · ${fdate(t.date)}`, (t.r || 0) >= 0 ? "up" : "down");
    }),
    ...pos.positions.map(async (p) => {
      const g = gridCharts.get("asset:" + p.asset);
      if (!g || !g.main) return;
      const d = await drawAsset(g, p.asset, p.entry_date);
      g.lines.forEach((l) => g.main.removePriceLine(l));
      g.lines = addPositionLines(g.main, d.position);
      setVal(g, `${fpx(p.price)} · ${fpct(p.pnl_pct, 1)}`, (p.pnl_pct || 0) >= 0 ? "up" : "down", p.price);
    }),
  ]);
}

register("charts", renderCharts, 15);

// Achat en temps réel : la carte de la crypto achetée s'illumine.
export function flashBuy(asset) {
  const g = gridCharts.get("asset:" + asset);
  if (!g) return;
  g.card.classList.remove("new-buy");
  void g.card.offsetWidth;
  g.card.classList.add("new-buy");
}
