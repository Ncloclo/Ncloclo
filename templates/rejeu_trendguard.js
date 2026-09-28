// Rejeu TrendGuard : animation de la page produite par trendguard/replay_animation.py.
// Données : bloc JSON #replay-data (JSON.parse, plus rapide qu'un littéral JS
// pour ~300 Ko). Tout texte issu des données passe par textContent.
(() => {
"use strict";

const $ = (id) => document.getElementById(id);
let D;
try {
  D = JSON.parse($("replay-data").textContent);
} catch {
  const p = document.createElement("p");
  p.className = "alert";
  p.textContent = "Données du rejeu absentes : générez la page avec « python trendguard_bot.py animation ».";
  $("app").replaceChildren(p);
  return;
}
const N = D.dates.length, CAP = D.capital, P = D.params;

// ---------- Formats (formateurs créés une seule fois) ----------
const nfCache = new Map();
const nf = (d) => {
  if (!nfCache.has(d)) nfCache.set(d, new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  return nfCache.get(d);
};
const f0 = nf(0), f1 = nf(1), f2 = nf(2);
const up = (s) => s.toUpperCase();
function fpx(v) {
  if (v == null) return "–";
  const a = Math.abs(v);
  return nf(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 3 : a >= 0.1 ? 4 : 5).format(v);
}
const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
const fusd = (v) => f0.format(v) + " USDT";
const fpct = (v, d = 1) => sign(v) + nf(d).format(Math.abs(v * 100)) + " %";
const fR = (v) => sign(v) + f2.format(Math.abs(v)) + " R";
const fpnl = (v) => (v >= 0 ? "+" : "−") + f0.format(Math.abs(v)) + " USDT";
const dLong = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const dShort = new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });
const dMonth = new Intl.DateTimeFormat("fr-FR", { month: "short", year: "2-digit", timeZone: "UTC" });
const DT = D.dates.map((s) => new Date(s + "T00:00:00Z"));
const REASON = { STOP: "stop de clôture", EXCHANGE_STOP: "stop catastrophe", DELISTED: "retrait de la cote", STOP_LATE: "stop (rattrapage)" };
const reason = (r) => REASON[r] || r;

// ---------- Préparation (une fois) ----------
const btc = D.close.btc;
const bh = btc.map((v) => CAP * v / btc[0]);
const dd = [];
{ let pk = -Infinity, worst = 0; D.equity.forEach((e, i) => { pk = Math.max(pk, e); worst = Math.min(worst, e / pk - 1); dd[i] = worst; }); }
const eps = D.episodes.map((e) => ({ ...e, end: e.d1 == null ? N - 1 : e.d1 }));
const epsBy = Object.fromEntries(D.assets.map((a) => [a, []]));
eps.forEach((e) => epsBy[e.a].push(e));
const closedEps = eps.filter((e) => e.d1 != null).sort((a, b) => a.d1 - b.d1 || a.d0 - b.d0);
// Cumuls par jour : trades clos, gagnants, somme des R (aucun filtrage par image).
const cum = { n: new Int32Array(N), w: new Int32Array(N), r: new Float64Array(N) };
{
  let k = 0, n = 0, w = 0, r = 0;
  for (let i = 0; i < N; i++) {
    while (k < closedEps.length && closedEps[k].d1 <= i) { n++; if (closedEps[k].pnl > 0) w++; r += closedEps[k].r; k++; }
    cum.n[i] = n; cum.w[i] = w; cum.r[i] = r;
  }
}
const feed = [];
D.events.forEach((ev, i) => {
  ev.x.forEach((x) => feed.push({ i, kind: "sell", ...x }));
  ev.e.forEach((e) => feed.push({ i, kind: "buy", ...e }));
});
const heldAt = (i) => eps.filter((e) => e.d0 <= i && (e.d1 == null || e.d1 > i));
const stopAt = (e, i) => { const j = i - e.d0; return j >= 0 && j < e.stops.length ? e.stops[j] : null; };
const disAt = (e, i) => { const j = i - e.d0; return j >= 0 && j < e.dis.length ? e.dis[j] : null; };

// ---------- Préférences (navigateur du lecteur uniquement) ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem("rejeu-tg:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rejeu-tg:" + k, JSON.stringify(v)); } catch { /* stockage bloqué */ } },
};
const SPEEDS = [2, 8, 30];
const state = { idx: N - 1, playing: false, speed: store.get("speed", 8), asset: null, follow: store.get("follow", true), acc: 0, last: 0 };
if (!SPEEDS.includes(state.speed)) state.speed = 8;
{
  const fromHash = decodeURIComponent(location.hash.slice(1)).toLowerCase();
  const openNow = eps.find((e) => e.d1 == null);
  const busiest = D.assets.slice().sort((x, y) => epsBy[y].length - epsBy[x].length)[0];
  state.asset = D.assets.includes(fromHash) ? fromHash : openNow ? openNow.a : busiest;
}

// ---------- Textes fixes ----------
const m = D.metrics;
$("lede").textContent = `Le code du bot, celui qui tourne en paper, rejoué jour après jour sur les clôtures réelles de Binance du ${dLong.format(DT[0])} au ${dLong.format(DT[N - 1])}. Chaque jour après 00:00 UTC, il lit les ${D.assets.length} paires, vérifie le régime du BTC, vend ce qui a touché son stop, achète les cassures et pose un stop sous chaque position.`;
$("honesty").textContent = `Prix et décisions réels, ordres simulés : capital fictif de ${fusd(CAP)}, frais et glissement de 0,1 % chacun par ordre. Résultat du rejeu : ${fpct(m.total_return_pct / 100)} contre ${fpct(btc[N - 1] / btc[0] - 1)} pour le BTC acheté et conservé, ${m.trades} trades, ${f0.format(m.win_rate_pct)} % gagnants. Données extraites le ${D.generated}.`;
$("rules").replaceChildren(...[
  ["Régime", `achats seulement si le BTC clôture au-dessus de sa moyenne ${P.regime_sma} jours`],
  ["Achat", `clôture au-dessus du plus haut des ${P.breakout_n} clôtures précédentes, momentum 90 jours positif, liquidité ≥ ${f0.format(P.min_volume_usd / 1e6)} M$ par jour`],
  ["Taille", `${f0.format(P.risk_pct * 100)} % du capital risqué par trade, entre le prix d'achat et le stop`],
  ["Plafonds", `${P.max_positions} positions au plus, ${f0.format(P.max_total_risk * 100)} % de risque cumulé, 25 % du capital par position`],
  ["Stop de clôture", `départ à ${P.init_stop_atr} × volatilité sous l'achat, puis ${P.trail_atr} × volatilité sous le plus haut (${P.bear_trail_atr} × en régime baissier) ; il ne descend jamais`],
  ["Stop catastrophe", "ordre STOP_LOSS posé sur Binance 1 × volatilité sous le stop de clôture : protège d'un krach entre deux clôtures"],
].map(([b, t]) => { const li = document.createElement("li"); const s = document.createElement("b"); s.textContent = b + " : "; li.append(s, t); return li; }));
$("foot").textContent = "Les performances passées ne garantissent pas les performances futures. La stratégie vise environ 1 % de perte par trade perdant et des gains de plusieurs fois ce risque ; elle gagne un trade sur trois environ et traverse des baisses de 20 à 35 %. Aucune stratégie ne réussit 99 % de ses trades.";
$("eq-sub").textContent = `Départ : ${fusd(CAP)} le ${dShort.format(DT[0])}`;

function table(caption, head, rows, cls, clsCol) {
  const t = document.createElement("table");
  const cap = document.createElement("caption"); cap.className = "sr-only"; cap.textContent = caption; t.append(cap);
  const tr = document.createElement("tr");
  head.forEach((h) => { const th = document.createElement("th"); th.textContent = h; th.scope = "col"; tr.append(th); });
  const thead = document.createElement("thead"); thead.append(tr); t.append(thead);
  const tb = document.createElement("tbody");
  rows.forEach((r, k) => {
    const row = document.createElement("tr");
    r.forEach((c, j) => { const td = document.createElement("td"); td.textContent = c; if (j === 0) td.className = "asset"; if (cls && j === clsCol && cls[k]) td.classList.add(cls[k]); row.append(td); });
    tb.append(row);
  });
  t.append(tb);
  return t;
}

// Portefeuille paper réel (fixe)
(() => {
  const L = D.paper_live;
  // Page publique (GitHub Pages) : pas de base locale, section masquée.
  if (!L) { $("h-live").closest("section").hidden = true; return; }
  if (!L.holdings.length) { $("live-sub").textContent = "Aucune position dans la base locale du bot."; return; }
  $("live-sub").textContent = `Base locale du bot (trendguard_paper.db) · dernière décision : bougie du ${dLong.format(new Date(L.last_decision_day + "T00:00:00Z"))} · cours : clôture du ${dLong.format(DT[N - 1])}`;
  const rows = L.holdings.map((h) => [up(h.a), h.date.split("-").reverse().join("/"), fpx(h.entry), fpx(h.last), fpx(h.stop), fpx(h.dis),
    h.last ? fpct(h.last / h.entry - 1) : "–", f0.format(h.risk) + " USDT"]);
  $("live").replaceChildren(table("Portefeuille paper actuel du bot", ["Crypto", "Achat le", "Prix d'achat", "Cours", "Stop de clôture", "Stop catastrophe", "Résultat", "Risque"],
    rows, L.holdings.map((h) => (h.last >= h.entry ? "up" : "down")), 6));
})();

// ---------- Couleurs du thème (relues seulement au changement de thème) ----------
let palette = null;
function colors() {
  if (!palette) {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    palette = { ink: v("--ink"), muted: v("--muted"), grid: v("--grid"), axis: v("--axis"), surface: v("--surface"),
      accent: v("--accent"), wash: v("--accent-wash"), bh: v("--bh"), price: v("--price"), good: v("--good"), bad: v("--bad"),
      bull: v("--bull-wash"), bear: v("--bear-wash") };
  }
  return palette;
}

// ---------- Graphiques (canvas) ----------
function niceStep(range, n) {
  const raw = range / n, p = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / p;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * p;
}
const charts = [];
let outsideTapBound = false;
function makeChart(host, opts) {
  const cv = document.createElement("canvas");
  const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; tip.setAttribute("aria-hidden", "true");
  cv.setAttribute("role", "img"); cv.tabIndex = 0;
  if (opts.label) cv.setAttribute("aria-label", opts.label);
  host.append(cv, tip);
  const ch = { cv, tip, hover: null, w: 0, h: 0, opts, rangeKey: null, range: null };
  ch.size = () => {
    const w = host.clientWidth; if (!w) return;
    const h = opts.height(w), dpr = window.devicePixelRatio || 1;
    ch.w = w; ch.h = h; cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.height = h + "px";
    cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    ch.render();
  };
  ch.geo = () => ({ L: 6, R: ch.w < 480 ? 56 : 68, T: 12, B: 26 });
  ch.xOf = (i) => { const g = ch.geo(); return g.L + (ch.w - g.L - g.R) * i / (N - 1); };
  ch.render = () => {
    if (!ch.w) return;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, ch.w, ch.h);
    const g = ch.geo(), C = colors();
    const key = opts.rangeKey ? opts.rangeKey() : "";
    if (ch.rangeKey !== key || !ch.range) { ch.range = opts.range(); ch.rangeKey = key; }
    const [lo, hi] = ch.range;
    const step = niceStep(hi - lo, ch.h < 260 ? 4 : 5);
    const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
    const yOf = (v) => g.T + (ch.h - g.T - g.B) * (1 - (v - y0) / (y1 - y0));
    const c = { ctx, g, yOf, C, xOf: ch.xOf, w: ch.w, h: ch.h, idx: state.idx };
    ctx.font = "11.5px 'IBM Plex Sans', system-ui, sans-serif"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    for (let v = y0; v <= y1 + step / 2; v += step) {
      const y = Math.round(yOf(v)) + .5;
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(g.L, y); ctx.lineTo(ch.w - g.R, y); ctx.stroke();
      ctx.fillStyle = C.muted; ctx.fillText(opts.tick(v), ch.w - g.R + 6, y);
    }
    const every = ch.w > 760 ? 2 : ch.w > 480 ? 3 : 6;
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.strokeStyle = C.axis; ctx.beginPath(); ctx.moveTo(g.L, ch.h - g.B + .5); ctx.lineTo(ch.w - g.R, ch.h - g.B + .5); ctx.stroke();
    DT.forEach((d, i) => {
      if (d.getUTCDate() !== 1 || d.getUTCMonth() % every !== 0) return;
      const x = Math.round(ch.xOf(i)) + .5, label = dMonth.format(d), half = ctx.measureText(label).width / 2;
      if (x - half < 0 || x + half > ch.w - g.R) return;
      ctx.strokeStyle = C.axis; ctx.beginPath(); ctx.moveTo(x, ch.h - g.B); ctx.lineTo(x, ch.h - g.B + 4); ctx.stroke();
      ctx.fillStyle = C.muted; ctx.fillText(label, x, ch.h - g.B + 7);
    });
    opts.draw(c);
    if (ch.hover != null) {
      const x = Math.round(ch.xOf(ch.hover)) + .5;
      ctx.strokeStyle = C.axis; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, g.T); ctx.lineTo(x, ch.h - g.B); ctx.stroke();
    }
  };
  ch.showTip = (i, clientY) => {
    ch.hover = i; ch.render();
    tip.replaceChildren(...opts.tip(i)); tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = ch.xOf(i) + 12; if (left + tw > ch.w) left = ch.xOf(i) - tw - 12;
    tip.style.left = Math.max(0, left) + "px";
    const top = clientY == null ? 8 : clientY - cv.getBoundingClientRect().top - th / 2;
    tip.style.top = Math.max(0, Math.min(ch.h - th, top)) + "px";
  };
  ch.hideTip = () => { if (ch.hover == null && tip.hidden) return; ch.hover = null; tip.hidden = true; ch.render(); };
  const indexAt = (clientX) => {
    const r = cv.getBoundingClientRect(), g = ch.geo();
    const i = Math.round((clientX - r.left - g.L) / (ch.w - g.L - g.R) * (N - 1));
    return Math.max(0, Math.min(state.idx, i));
  };
  cv.addEventListener("pointermove", (ev) => ch.showTip(indexAt(ev.clientX), ev.clientY));
  cv.addEventListener("pointerdown", (ev) => ch.showTip(indexAt(ev.clientX), ev.clientY));
  // Souris : l'infobulle suit le pointeur. Tactile : elle reste affichée
  // jusqu'au prochain appui ailleurs (sinon elle disparaît en levant le doigt).
  cv.addEventListener("pointerleave", (ev) => { if (ev.pointerType !== "touch") ch.hideTip(); });
  if (!outsideTapBound) {
    outsideTapBound = true;
    document.addEventListener("pointerdown", (ev) => charts.forEach((k) => { if (ev.target !== k.cv) k.hideTip(); }));
  }
  // Clavier : mêmes informations qu'au survol.
  cv.addEventListener("focus", () => ch.showTip(ch.hover ?? state.idx));
  cv.addEventListener("blur", () => ch.hideTip());
  cv.addEventListener("keydown", (ev) => {
    const cur = ch.hover ?? state.idx;
    const moves = { ArrowLeft: cur - 1, ArrowRight: cur + 1, PageDown: cur - 30, PageUp: cur + 30, Home: 0, End: state.idx };
    if (ev.key in moves) { ev.preventDefault(); ch.showTip(Math.max(0, Math.min(state.idx, moves[ev.key]))); }
    else if (ev.key === "Escape") ch.hideTip();
  });
  new ResizeObserver(ch.size).observe(host);
  charts.push(ch);
  return ch;
}
function line(c, arr, color, width, i1) {
  const { ctx, xOf, yOf } = c; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath(); let pen = false;
  for (let i = 0; i <= i1; i++) { const v = arr[i]; if (v == null) { pen = false; continue; } const x = xOf(i), y = yOf(v); pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y); pen = true; }
  ctx.stroke();
}
function dot(c, x, y, color, r = 4) { const { ctx, C } = c; ctx.beginPath(); ctx.arc(x, y, r + 2, 0, 7); ctx.fillStyle = C.surface; ctx.fill(); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fillStyle = color; ctx.fill(); }
function tri(c, x, y, upward, color) {
  const { ctx, C } = c; const s = 7;
  ctx.beginPath();
  if (upward) { ctx.moveTo(x, y - s); ctx.lineTo(x + s, y + s * .8); ctx.lineTo(x - s, y + s * .8); } else { ctx.moveTo(x, y + s); ctx.lineTo(x + s, y - s * .8); ctx.lineTo(x - s, y - s * .8); }
  ctx.closePath(); ctx.lineWidth = 4; ctx.strokeStyle = C.surface; ctx.stroke(); ctx.fillStyle = color; ctx.fill();
}
function endLabel(c, v, text, color) {
  const { ctx, g, w, C } = c; const y = c.yOf(v);
  ctx.font = "600 11.5px 'IBM Plex Sans', system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  const tw = ctx.measureText(text).width;
  ctx.fillStyle = color; ctx.fillRect(w - g.R + 2, y - 9, Math.min(tw + 8, g.R - 2), 18);
  ctx.fillStyle = C.surface; ctx.fillText(text, w - g.R + 6, y);
}
function tipRow(label, value, color, thin) {
  const r = document.createElement("div"); r.className = "row";
  const a = document.createElement("span");
  if (color) { const k = document.createElement("i"); k.className = "key-line" + (thin ? " key-thin" : ""); k.style.background = color; a.append(k); }
  a.append(label); const b = document.createElement("strong"); b.textContent = value; r.append(a, b); return r;
}
const tipDate = (i) => Object.assign(document.createElement("div"), { className: "t-date", textContent: dLong.format(DT[i]) });
const tipNote = (cls, text) => Object.assign(document.createElement("div"), { className: "ev " + cls, textContent: text });
function extent(arrays) {
  let lo = Infinity, hi = -Infinity;
  arrays.forEach((arr) => arr.forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  const p = (hi - lo) * .04; return [lo - p, hi + p];
}

const priceChart = makeChart($("c-price"), {
  height: (w) => (w < 520 ? 270 : 360),
  rangeKey: () => state.asset,
  range: () => extent([D.close[state.asset], D.high30[state.asset], ...epsBy[state.asset].map((e) => e.dis)]),
  tick: fpx,
  draw: (c) => {
    const a = state.asset, idx = c.idx, { ctx, g, C, xOf, yOf } = c;
    epsBy[a].forEach((e) => {
      if (e.d0 > idx) return; const i1 = Math.min(e.end, idx);
      ctx.fillStyle = C.wash; ctx.fillRect(xOf(e.d0), g.T, Math.max(2, xOf(i1) - xOf(e.d0)), c.h - g.B - g.T);
    });
    line(c, D.high30[a], C.muted, 1, idx);
    line(c, D.close[a], C.price, 2, idx);
    epsBy[a].forEach((e) => {
      if (e.d0 > idx) return;
      const upto = Math.min(e.d0 + e.stops.length - 1, idx);
      for (const [arr, wdt, alpha] of [[e.dis, 1.25, .55], [e.stops, 2, 1]]) {
        ctx.globalAlpha = alpha; ctx.strokeStyle = C.bad; ctx.lineWidth = wdt; ctx.beginPath();
        for (let i = e.d0; i <= upto; i++) {
          const x = xOf(i), y = yOf(arr[i - e.d0]);
          if (i === e.d0) ctx.moveTo(x, y); else { ctx.lineTo(x, yOf(arr[i - e.d0 - 1])); ctx.lineTo(x, y); }
        }
        if (e.d1 != null && e.d1 <= idx) ctx.lineTo(xOf(e.d1), yOf(arr[arr.length - 1]));
        ctx.stroke(); ctx.globalAlpha = 1;
      }
    });
    epsBy[a].forEach((e) => {
      if (e.d0 > idx) return;
      tri(c, xOf(e.d0), yOf(e.entry) + 11, true, C.good);
      if (e.d1 != null && e.d1 <= idx) tri(c, xOf(e.d1), yOf(e.exit) - 11, false, e.pnl > 0 ? C.good : C.bad);
    });
    const v = D.close[a][idx];
    if (v != null) { dot(c, xOf(idx), yOf(v), C.price); endLabel(c, v, fpx(v), C.price); }
  },
  tip: (i) => {
    const a = state.asset, C = colors(), out = [tipDate(i)];
    out.push(tipRow("Clôture", fpx(D.close[a][i]), C.price));
    out.push(tipRow("Plus haut 30 j", fpx(D.high30[a][i]), C.muted, true));
    epsBy[a].forEach((e) => {
      if (e.d0 <= i && i <= e.end && stopAt(e, i) != null) {
        out.push(tipRow("Stop de clôture", fpx(stopAt(e, i)), C.bad));
        out.push(tipRow("Stop catastrophe", fpx(disAt(e, i)), C.bad, true));
      }
      if (e.d0 === i) out.push(tipNote("up", `↗ Achat à ${fpx(e.entry)} · risque ${f0.format(e.risk)} USDT`));
      else if (e.d1 === i) out.push(tipNote(e.pnl > 0 ? "up" : "down", `↘ Vente (${reason(e.reason)}) · ${fR(e.r)}`));
    });
    return out;
  },
});

makeChart($("c-btc"), {
  label: "BTC et sa moyenne 150 jours, périodes haussières et baissières. Flèches gauche et droite pour lire chaque jour.",
  height: (w) => (w < 520 ? 220 : 250),
  range: () => extent([btc, D.sma150]),
  tick: (v) => f0.format(v / 1000) + " k",
  draw: (c) => {
    const { ctx, g, C, xOf } = c, idx = c.idx;
    let s = 0;
    for (let i = 1; i <= idx + 1; i++) {
      if (i === idx + 1 || D.bull[i] !== D.bull[s]) {
        ctx.fillStyle = D.bull[s] ? C.bull : C.bear;
        const x0 = xOf(Math.max(0, s - .5)), x1 = xOf(Math.min(idx, i - .5));
        ctx.fillRect(x0, g.T, Math.max(1, x1 - x0), c.h - g.B - g.T); s = i;
      }
    }
    line(c, D.sma150, C.bh, 2, idx);
    line(c, btc, C.price, 2, idx);
    dot(c, xOf(idx), c.yOf(btc[idx]), C.price);
  },
  tip: (i) => {
    const C = colors();
    return [tipDate(i), tipRow("BTC", f0.format(btc[i]), C.price), tipRow("Moyenne 150 j", f0.format(D.sma150[i]), C.bh),
      tipNote(D.bull[i] ? "up" : "down", D.bull[i] ? "Haussier : achats autorisés" : "Baissier : aucun achat")];
  },
});

makeChart($("c-eq"), {
  label: "Capital du bot comparé au BTC acheté et conservé. Flèches gauche et droite pour lire chaque jour.",
  height: (w) => (w < 520 ? 220 : 250),
  range: () => extent([D.equity, bh]),
  tick: (v) => f0.format(v / 1000) + " k",
  draw: (c) => {
    const { ctx, C, xOf, yOf, g } = c, idx = c.idx;
    ctx.beginPath(); ctx.moveTo(xOf(0), c.h - g.B);
    for (let i = 0; i <= idx; i++) ctx.lineTo(xOf(i), yOf(D.equity[i]));
    ctx.lineTo(xOf(idx), c.h - g.B); ctx.closePath(); ctx.fillStyle = C.wash; ctx.fill();
    ctx.strokeStyle = C.axis; ctx.lineWidth = 1; const yb = Math.round(yOf(CAP)) + .5; ctx.beginPath(); ctx.moveTo(g.L, yb); ctx.lineTo(c.w - g.R, yb); ctx.stroke();
    line(c, bh, C.bh, 2, idx);
    line(c, D.equity, C.accent, 2, idx);
    dot(c, xOf(idx), yOf(bh[idx]), C.bh); dot(c, xOf(idx), yOf(D.equity[idx]), C.accent);
  },
  tip: (i) => {
    const C = colors();
    return [tipDate(i), tipRow("Bot", `${fusd(D.equity[i])} (${fpct(D.equity[i] / CAP - 1)})`, C.accent),
      tipRow("BTC conservé", `${fusd(bh[i])} (${fpct(bh[i] / CAP - 1)})`, C.bh)];
  },
});
const renderCharts = () => charts.forEach((k) => k.render());

// ---------- Sélecteur d'actifs ----------
const chipEls = {};
D.assets.forEach((a) => {
  const b = document.createElement("button"); b.type = "button"; b.className = "chip" + (D.liquid[a] ? "" : " illiquid");
  const i = document.createElement("i"); i.setAttribute("aria-hidden", "true");
  b.append(i, up(a)); b.setAttribute("aria-pressed", "false");
  b.title = D.liquid[a] ? up(a) + "/USDT" : up(a) + "/USDT · liquidité insuffisante aujourd'hui (exclue des achats)";
  b.addEventListener("click", () => { setFollow(false); selectAsset(a); });
  chipEls[a] = b; $("chips").append(b);
});
function selectAsset(a) {
  if (state.asset === a) return;
  state.asset = a; priceChart.hideTip();
  try { history.replaceState(null, "", "#" + a); } catch { /* cadre restreint */ }
  renderAsset(); priceChart.render();
}
function renderAsset() {
  const a = state.asset, i = state.idx;
  Object.entries(chipEls).forEach(([k, b]) => b.setAttribute("aria-pressed", String(k === a)));
  $("asset-name").textContent = up(a) + "/USDT";
  const list = epsBy[a].filter((e) => e.d0 <= i), done = list.filter((e) => e.d1 != null && e.d1 <= i);
  const res = done.reduce((s, e) => s + e.pnl, 0);
  const sub = list.length ? `${list.length} achat${list.length > 1 ? "s" : ""} · ${done.length} vendu${done.length > 1 ? "s" : ""} · résultat réalisé ${fpnl(res)}` : "Aucun achat du bot sur cette crypto à cette date";
  $("asset-sub").textContent = sub;
  priceChart.cv.setAttribute("aria-label", `${up(a)}/USDT, clôture ${fpx(D.close[a][i])} le ${dShort.format(DT[i])} : ${sub}. Flèches gauche et droite pour lire chaque jour.`);
}

// ---------- Mise à jour du jour affiché ----------
let lastTradesCount = -1;
function update(animate) {
  const i = state.idx, ev = D.events[i], eq = D.equity[i];
  $("scrub").value = i;
  $("scrub").setAttribute("aria-valuetext", dLong.format(DT[i]));
  $("when-date").textContent = dLong.format(DT[i]);
  $("when-day").textContent = `jour ${i + 1} / ${N}`;
  $("k-eq").textContent = fusd(eq);
  $("k-eq-sub").className = "sub " + (eq >= CAP ? "up" : "down"); $("k-eq-sub").textContent = fpct(eq / CAP - 1) + " depuis le départ";
  $("k-bh").textContent = fusd(bh[i]);
  $("k-bh-sub").className = "sub " + (bh[i] >= CAP ? "up" : "down"); $("k-bh-sub").textContent = fpct(bh[i] / CAP - 1) + " sur la même période";
  $("k-dd").textContent = fpct(dd[i]);
  const n = cum.n[i];
  $("k-tr").textContent = f0.format(n);
  $("k-tr-sub").textContent = n ? `${f0.format(cum.w[i] / n * 100)} % gagnants · moyenne ${fR(cum.r[i] / n)}` : "aucun encore";
  const held = heldAt(i), riskPct = ev.risk / eq;
  $("k-risk").textContent = f1.format(riskPct * 100) + " %";
  $("k-risk-sub").textContent = `plafond ${f0.format(P.max_total_risk * 100)} % · ${held.length} / ${P.max_positions} positions`;
  $("k-risk-bar").style.width = Math.min(100, riskPct / P.max_total_risk * 100) + "%";
  const pill = $("k-reg"); pill.querySelector("span").textContent = D.bull[i] ? "Haussier" : "Baissier";
  pill.querySelector("i").style.background = D.bull[i] ? "var(--good)" : "var(--bad)";
  $("k-reg-sub").textContent = D.bull[i] ? "achats autorisés" : "aucun achat, stops resserrés";
  const heldSet = new Set(held.map((e) => e.a)), sigs = new Set(ev.s);
  Object.entries(chipEls).forEach(([a, b]) => { b.classList.toggle("held", heldSet.has(a)); b.classList.toggle("signal", !heldSet.has(a) && sigs.has(a)); });
  renderAsset();
  renderSteps(animate, held);
  renderPositions(held);
  renderJournal();
  if (n !== lastTradesCount) { lastTradesCount = n; renderTrades(closedEps.slice(0, n)); }
  renderCharts();
}

function renderSteps(animate, held) {
  const i = state.idx, ev = D.events[i], bull = D.bull[i], eq = D.equity[i];
  $("steps-date").textContent = `Bougie du ${dShort.format(DT[i])} · décision le lendemain à 00:02 UTC`;
  const steps = [
    ["Clôture de la bougie", `${D.assets.length} paires Binance lues à 00:00 UTC. BTC clôture à ${f0.format(btc[i])} USDT.`, ""],
    ["Régime du BTC", bull ? `Au-dessus de sa moyenne 150 jours (${f0.format(D.sma150[i])}) : achats autorisés.` : `Sous sa moyenne 150 jours (${f0.format(D.sma150[i])}) : aucun achat, stops resserrés à ${P.bear_trail_atr} × volatilité.`, bull ? "" : "is-block"],
    ["Stops et ventes", ev.x.length ? ev.x.map((x) => `${up(x.a)} vendu (${reason(x.reason)}) : ${fpnl(x.pnl)}, ${fR(x.r)}`).join(" · ") : held.length ? "Aucun stop touché. Les stops montent avec les plus hauts, jamais ne descendent." : "Aucune position à surveiller.", ev.x.length ? "is-sell" : ""],
  ];
  const breaks = ev.e.map((e) => e.a).concat(ev.s);
  steps.push(["Cassures du plus haut 30 jours", breaks.length ? (bull ? `Signal sur ${breaks.map(up).join(", ")}, classés par momentum.` : `Cassures ignorées (régime baissier) : ${breaks.map(up).join(", ")}.`) : "Aucune crypto ne clôture au-dessus de son plus haut des 30 derniers jours.", ""]);
  if (ev.e.length) steps.push(["Taille : 1 % de risque par achat", ev.e.map((e) => `${up(e.a)} : ${f0.format(e.cost)} USDT achetés, ${f0.format(e.risk)} USDT risqués (${f1.format(e.risk / eq * 100)} % du capital)`).join(" · "), "is-buy"]);
  else if (bull && ev.s.length) steps.push(["Taille : 1 % de risque par achat", `Pas d'achat : plafond atteint (${f1.format(ev.risk / eq * 100)} % de risque engagé sur ${f0.format(P.max_total_risk * 100)} %, ou ${P.max_positions} positions).`, "is-block"]);
  else steps.push(["Taille : 1 % de risque par achat", `Rien à acheter. Risque engagé : ${f1.format(ev.risk / eq * 100)} % du capital.`, ""]);
  steps.push(["Protection sur Binance", held.length ? `${held.length} position${held.length > 1 ? "s" : ""}, chacune avec un stop catastrophe posé 1 × volatilité sous son stop de clôture.` : "Capital à 100 % en USDT : rien à protéger.", ""]);
  const ol = $("steps");
  const lis = steps.map(([t, b, c]) => {
    const li = document.createElement("li"); if (c) li.className = c;
    const h = document.createElement("span"); h.className = "st-title"; h.textContent = t;
    const p = document.createElement("span"); p.className = "st-body"; p.textContent = b;
    li.append(h, p); return li;
  });
  ol.classList.remove("run"); ol.replaceChildren(...lis);
  if (animate) { void ol.offsetWidth; ol.classList.add("run"); }
}

function renderPositions(held) {
  const i = state.idx, rows = held.slice().sort((a, b) => a.d0 - b.d0);
  $("pos-sub").textContent = `au ${dShort.format(DT[i])}`;
  if (!rows.length) { const p = document.createElement("p"); p.className = "empty"; p.textContent = "Aucune position : capital à 100 % en USDT."; $("pos").replaceChildren(p); return; }
  $("pos").replaceChildren(table(`Positions ouvertes au ${dShort.format(DT[i])}`, ["Crypto", "Achat le", "Prix d'achat", "Cours", "Stop", "Écart au stop", "Résultat", "En R (≈)"],
    rows.map((e) => { const px = D.close[e.a][i], st = stopAt(e, i); return [up(e.a), dShort.format(DT[e.d0]), fpx(e.entry), fpx(px), fpx(st), st ? fpct(st / px - 1) : "–", fpct(px / e.entry - 1), fR((px - e.entry) * e.qty / e.risk)]; }),
    rows.map((e) => (D.close[e.a][i] >= e.entry ? "up" : "down")), 6));
}

function orderText(f) {
  return f.kind === "buy"
    ? `↗ ACHAT ${up(f.a)} à ${fpx(f.entry)} · stop ${fpx(f.stop)} · risque ${f0.format(f.risk)} USDT`
    : `↘ VENTE ${up(f.a)} · ${reason(f.reason)} · ${fpnl(f.pnl)} (${fR(f.r)})`;
}
function renderJournal() {
  const i = state.idx, items = [];
  for (let k = feed.length - 1; k >= 0 && items.length < 16; k--) {
    const f = feed[k]; if (f.i > i) continue;
    const li = document.createElement("li");
    const d = document.createElement("span"); d.className = "d"; d.textContent = dShort.format(DT[f.i]);
    const t = document.createElement("span"); t.className = f.kind; t.textContent = orderText(f);
    li.append(d, t); items.push(li);
  }
  if (!items.length) { const li = document.createElement("li"); li.textContent = "Aucun ordre pour l'instant."; items.push(li); }
  $("journal").replaceChildren(...items);
}

function renderTrades(done) {
  $("trades-summary").textContent = `Tous les trades clos jusqu'au jour affiché (${done.length})`;
  const rev = done.slice().reverse();
  $("trades").replaceChildren(table("Trades clos", ["Crypto", "Achat", "Vente", "Prix d'achat", "Prix de vente", "Raison", "Résultat", "En R"],
    rev.map((e) => [up(e.a), dShort.format(DT[e.d0]), dShort.format(DT[e.d1]), fpx(e.entry), fpx(e.exit), reason(e.reason), fpnl(e.pnl), fR(e.r)]),
    rev.map((e) => (e.pnl > 0 ? "up" : "down")), 7));
}

// Lecteur d'écran : le jour choisi à la main est annoncé (pas pendant la
// lecture, qui changerait de jour plusieurs fois par seconde).
let announceTimer = 0;
function announce() {
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => {
    const i = state.idx, ev = D.events[i];
    const orders = [...ev.e.map((e) => `achat de ${up(e.a)}`), ...ev.x.map((x) => `vente de ${up(x.a)}`)];
    $("announce").textContent = `${dLong.format(DT[i])}. Capital ${fusd(D.equity[i])}. ${orders.length ? orders.join(", ") + "." : "Aucun ordre."}`;
  }, 400);
}

// ---------- Lecture ----------
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
let pendingFrame = 0, pendingAnimate = false;
function scheduleUpdate(animate) {           // une mise à jour par image, au plus
  pendingAnimate = pendingAnimate || animate;
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => { pendingFrame = 0; const a = pendingAnimate; pendingAnimate = false; update(a); });
}
function setIdx(i, fromPlay) {
  i = Math.max(0, Math.min(N - 1, i));
  if (i === state.idx && !fromPlay) return;
  const prev = state.idx; state.idx = i;
  const ev = D.events[i], eventful = ev.e.length + ev.x.length > 0;
  if (state.follow && fromPlay && eventful) {
    const a = (ev.e[0] || ev.x[0]).a;
    if (a !== state.asset) { state.asset = a; priceChart.hideTip(); }
  }
  charts.forEach((k) => { if (k.hover != null && k.hover > i) k.hideTip(); });
  update(!reduceMotion.matches && fromPlay && i !== prev && (state.speed <= 2 || (eventful && state.speed <= 8)));
  if (!state.playing) announce();
}
function setPlayButton(playing) {
  const b = $("play");
  b.querySelector(".ico").textContent = playing ? "❚❚" : "▶";
  b.querySelector(".lbl").textContent = playing ? "Pause" : state.idx >= N - 1 ? "Rejouer" : "Lecture";
}
function frame(t) {
  if (!state.playing) return;
  const dt = Math.min(.25, (t - state.last) / 1000); state.last = t;
  state.acc += dt * state.speed;
  if (state.acc >= 1) { const k = Math.floor(state.acc); state.acc -= k; setIdx(state.idx + k, true); }
  if (state.idx >= N - 1) { pause(); return; }
  requestAnimationFrame(frame);
}
function play() {
  if (state.idx >= N - 1) setIdx(0, true);
  state.playing = true; state.acc = 0; state.last = performance.now();
  setPlayButton(true); requestAnimationFrame(frame);
}
function pause() { state.playing = false; setPlayButton(false); announce(); }
function setFollow(on) { state.follow = on; $("follow").checked = on; store.set("follow", on); }
function setSpeed(s) {
  state.speed = s; store.set("speed", s);
  document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(+x.dataset.speed === s)));
}

$("play").addEventListener("click", () => (state.playing ? pause() : play()));
$("restart").addEventListener("click", () => { setIdx(0, false); setPlayButton(state.playing); });
document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setSpeed(+b.dataset.speed)));
$("scrub").max = N - 1;
$("scrub").addEventListener("input", (e) => {
  const i = +e.target.value; if (i === state.idx) return;
  state.idx = i; charts.forEach((k) => k.hideTip()); scheduleUpdate(false);
  if (!state.playing) { setPlayButton(false); announce(); }
});
$("follow").addEventListener("change", (e) => setFollow(e.target.checked));
document.addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || e.target.closest("input, button, select, textarea, summary, [tabindex]")) return;
  const moves = { ArrowRight: state.idx + 1, ArrowLeft: state.idx - 1, Home: 0, End: N - 1 };
  if (e.key === " ") { e.preventDefault(); state.playing ? pause() : play(); }
  else if (e.key in moves) { e.preventDefault(); setIdx(moves[e.key], false); setPlayButton(state.playing); }
});
window.addEventListener("hashchange", () => {
  const a = decodeURIComponent(location.hash.slice(1)).toLowerCase();
  if (D.assets.includes(a)) { setFollow(false); selectAsset(a); }
});

// Changement de thème ou d'écran (autre densité de pixels) : couleurs relues,
// canvas redimensionnés.
const retheme = () => { palette = null; renderCharts(); };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
(function watchDpr() {
  matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`).addEventListener("change", () => { charts.forEach((k) => k.size()); watchDpr(); }, { once: true });
})();
if (document.fonts) document.fonts.ready.then(renderCharts);

setSpeed(state.speed);
setFollow(state.follow);
setPlayButton(false);
update(false);
})();
