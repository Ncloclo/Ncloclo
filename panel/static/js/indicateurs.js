// Panneau TrendGuard : indicateurs techniques (calculs purs, sans page web),
// pour les graphiques détaillés et leur lecture technique. Ils informent :
// aucun ne passe d'ordre. Les règles du bot (cassure, momentum, stops)
// restent celles de trendguard/trend_strategy.py, envoyées par le serveur.
//
// Conventions : chaque fonction rend des tableaux de la longueur des données,
// avec null tant que la fenêtre n'est pas pleine. Moyennes exponentielles
// amorcées sur la première valeur (pandas ewm(adjust=False)), lissages de
// Wilder (RSI, ATR, ADX) amorcés sur la moyenne simple des n premières
// valeurs : les définitions des plateformes de trading courantes.

const num = (v) => typeof v === "number" && isFinite(v);
const nulls = (n) => new Array(n).fill(null);
const col = (rows, k) => rows.map((r) => r[k]);

// ---------- Moyennes et dispersion ----------

// Moyenne simple des n dernières valeurs (toutes connues).
export function sma(xs, n) {
  const out = nulls(xs.length);
  let sum = 0, known = 0;
  for (let i = 0; i < xs.length; i++) {
    if (num(xs[i])) { sum += xs[i]; known++; }
    if (i >= n && num(xs[i - n])) { sum -= xs[i - n]; known--; }
    if (i >= n - 1 && known === n) out[i] = sum / n;
  }
  return out;
}

// Moyenne exponentielle, α = 2 / (n + 1), amorcée sur la première valeur
// connue ; rendue à partir de la n-ième.
export function ema(xs, n) {
  const out = nulls(xs.length), a = 2 / (n + 1);
  let e = null, seen = 0;
  for (let i = 0; i < xs.length; i++) {
    if (!num(xs[i])) { if (e !== null) { e = null; seen = 0; } continue; }
    e = e === null ? xs[i] : a * xs[i] + (1 - a) * e;
    seen++;
    if (seen >= n) out[i] = e;
  }
  return out;
}

// Lissage de Wilder (RMA), α = 1 / n, amorcé sur la moyenne des n premières.
export function rma(xs, n) {
  const out = nulls(xs.length);
  let e = null, sum = 0, seen = 0;
  for (let i = 0; i < xs.length; i++) {
    if (!num(xs[i])) { e = null; sum = 0; seen = 0; continue; }
    if (e === null) {
      sum += xs[i];
      seen++;
      if (seen === n) { e = sum / n; out[i] = e; }
      continue;
    }
    e = (e * (n - 1) + xs[i]) / n;
    out[i] = e;
  }
  return out;
}

// Écart type (population) des n dernières valeurs : celui des bandes de Bollinger.
export function stdev(xs, n) {
  const m = sma(xs, n), out = nulls(xs.length);
  for (let i = n - 1; i < xs.length; i++) {
    if (m[i] === null) continue;
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += (xs[j] - m[i]) ** 2;
    out[i] = Math.sqrt(s / n);
  }
  return out;
}

function rolling(xs, n, pick) {
  const out = nulls(xs.length);
  for (let i = n - 1; i < xs.length; i++) {
    let v = null, ok = true;
    for (let j = i - n + 1; j <= i; j++) {
      if (!num(xs[j])) { ok = false; break; }
      v = v === null ? xs[j] : pick(v, xs[j]);
    }
    if (ok) out[i] = v;
  }
  return out;
}
export const highest = (xs, n) => rolling(xs, n, Math.max);
export const lowest = (xs, n) => rolling(xs, n, Math.min);

// Décale une série de k barres (k > 0 : vers la droite, dans le futur).
export function shift(xs, k, length = xs.length) {
  const out = nulls(length);
  for (let i = 0; i < xs.length; i++) if (i + k >= 0 && i + k < length) out[i + k] = xs[i];
  return out;
}

// ---------- Prix dérivés ----------

export const typical = (rows) => rows.map((r) => (r.high + r.low + r.close) / 3);
export const median = (rows) => rows.map((r) => (r.high + r.low) / 2);

// Bougies Heikin Ashi : tendance lissée, moins de bruit que les bougies.
export function heikinAshi(rows) {
  const out = [];
  rows.forEach((r, i) => {
    const close = (r.open + r.high + r.low + r.close) / 4;
    const open = i === 0 ? (r.open + r.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({ time: r.time, open, high: Math.max(r.high, open, close), low: Math.min(r.low, open, close), close });
  });
  return out;
}

export function trueRange(rows) {
  return rows.map((r, i) => {
    if (i === 0) return r.high - r.low;
    const pc = rows[i - 1].close;
    return Math.max(r.high - r.low, Math.abs(r.high - pc), Math.abs(r.low - pc));
  });
}
export const atr = (rows, n = 14) => rma(trueRange(rows), n);

// ---------- Tendance ----------

export function macd(close, fast = 12, slow = 26, signal = 9) {
  const f = ema(close, fast), s = ema(close, slow);
  const line = close.map((_, i) => (f[i] !== null && s[i] !== null ? f[i] - s[i] : null));
  const sig = ema(line, signal);
  const hist = line.map((v, i) => (v !== null && sig[i] !== null ? v - sig[i] : null));
  return { macd: line, signal: sig, hist };
}

// ADX et indicateurs directionnels (+DI, −DI) de Wilder.
export function adx(rows, n = 14) {
  const plus = nulls(rows.length), minus = nulls(rows.length);
  for (let i = 1; i < rows.length; i++) {
    const upMove = rows[i].high - rows[i - 1].high, downMove = rows[i - 1].low - rows[i].low;
    plus[i] = upMove > downMove && upMove > 0 ? upMove : 0;
    minus[i] = downMove > upMove && downMove > 0 ? downMove : 0;
  }
  const tr = trueRange(rows).map((v, i) => (i === 0 ? null : v));
  const trS = rma(tr, n), pS = rma(plus, n), mS = rma(minus, n);
  const pDI = trS.map((t, i) => (t ? 100 * pS[i] / t : null));
  const mDI = trS.map((t, i) => (t ? 100 * mS[i] / t : null));
  const dx = pDI.map((p, i) => {
    const m = mDI[i];
    if (p === null || m === null) return null;
    return p + m === 0 ? 0 : 100 * Math.abs(p - m) / (p + m);
  });
  return { adx: rma(dx, n), plusDI: pDI, minusDI: mDI };
}

// SAR parabolique de Wilder (départ 0,02, pas 0,02, plafond 0,2), calculé
// comme la référence ta.sar de Pine : niveau et sens (1 haussier, −1 baissier).
export function psar(rows, start = 0.02, inc = 0.02, max = 0.2) {
  const sar = nulls(rows.length), dir = nulls(rows.length);
  let result = null, maxMin = null, acc = null, below = null;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i], p = rows[i - 1];
    let first = false;
    if (i === 1) {
      below = r.close > p.close;
      maxMin = below ? r.high : r.low;
      result = below ? p.low : p.high;
      first = true;
      acc = start;
    }
    result += acc * (maxMin - result);
    if (below && result > r.low) {
      first = true; below = false; result = Math.max(r.high, maxMin); maxMin = r.low; acc = start;
    } else if (!below && result < r.high) {
      first = true; below = true; result = Math.min(r.low, maxMin); maxMin = r.high; acc = start;
    }
    if (!first) {
      if (below && r.high > maxMin) { maxMin = r.high; acc = Math.min(acc + inc, max); }
      else if (!below && r.low < maxMin) { maxMin = r.low; acc = Math.min(acc + inc, max); }
    }
    if (below) {
      result = Math.min(result, p.low);
      if (i > 1) result = Math.min(result, rows[i - 2].low);
    } else {
      result = Math.max(result, p.high);
      if (i > 1) result = Math.max(result, rows[i - 2].high);
    }
    sar[i] = result;
    dir[i] = below ? 1 : -1;
  }
  return { sar, dir };
}

// Supertrend (ATR 10, multiplicateur 3) : niveau suiveur et sens. Le sens
// change quand la clôture franchit la bande de la barre précédente.
export function supertrend(rows, n = 10, mult = 3) {
  const a = atr(rows, n), mid = median(rows);
  const line = nulls(rows.length), dir = nulls(rows.length);
  let up1 = null, dn1 = null, trend = 1;
  for (let i = 0; i < rows.length; i++) {
    if (a[i] === null) continue;
    let up = mid[i] - mult * a[i], dn = mid[i] + mult * a[i];
    const pc = i > 0 ? rows[i - 1].close : null;
    if (up1 !== null && pc > up1) up = Math.max(up, up1);
    if (dn1 !== null && pc < dn1) dn = Math.min(dn, dn1);
    if (trend === -1 && dn1 !== null && rows[i].close > dn1) trend = 1;
    else if (trend === 1 && up1 !== null && rows[i].close < up1) trend = -1;
    line[i] = trend === 1 ? up : dn;
    dir[i] = trend;
    up1 = up;
    dn1 = dn;
  }
  return { line, dir };
}

// Ichimoku (9, 26, 52) : nuage projeté de 26 périodes dans le futur, ligne
// retardée 26 périodes en arrière (décalages de 25 barres, comme les
// plateformes courantes). Les tableaux du nuage ont `ahead` barres de plus.
export function ichimoku(rows, conv = 9, base = 26, spanB = 52, ahead = 25) {
  const h = col(rows, "high"), l = col(rows, "low"), c = col(rows, "close");
  const mid = (n) => { const hh = highest(h, n), ll = lowest(l, n); return hh.map((v, i) => (v !== null && ll[i] !== null ? (v + ll[i]) / 2 : null)); };
  const tenkan = mid(conv), kijun = mid(base), b = mid(spanB);
  const a = tenkan.map((v, i) => (v !== null && kijun[i] !== null ? (v + kijun[i]) / 2 : null));
  const len = rows.length + ahead;
  return {
    tenkan: shift(tenkan, 0, len), kijun: shift(kijun, 0, len),
    spanA: shift(a, ahead, len), spanB: shift(b, ahead, len),
    chikou: shift(c, -ahead, len),
  };
}

// VWAP de séance : prix moyen pondéré par le volume depuis 00:00 UTC.
export function vwap(rows) {
  const out = nulls(rows.length);
  let day = null, pv = 0, vol = 0;
  rows.forEach((r, i) => {
    const d = Math.floor(r.time / 86400);
    if (d !== day) { day = d; pv = 0; vol = 0; }
    pv += (r.high + r.low + r.close) / 3 * r.volume;
    vol += r.volume;
    out[i] = vol > 0 ? pv / vol : null;
  });
  return out;
}

// ---------- Canaux et volatilité ----------

export function bollinger(close, n = 20, k = 2) {
  const mid = sma(close, n), sd = stdev(close, n);
  const upper = mid.map((m, i) => (m !== null ? m + k * sd[i] : null));
  const lower = mid.map((m, i) => (m !== null ? m - k * sd[i] : null));
  const pctB = close.map((c, i) => (upper[i] !== null && upper[i] > lower[i] ? (c - lower[i]) / (upper[i] - lower[i]) : null));
  const width = mid.map((m, i) => (m ? (upper[i] - lower[i]) / m : null));
  return { mid, upper, lower, pctB, width };
}

// Canal de Keltner : moyenne exponentielle 20 ± 2 ATR 10.
export function keltner(rows, n = 20, k = 2, atrN = 10) {
  const mid = ema(col(rows, "close"), n), a = atr(rows, atrN);
  return {
    mid,
    upper: mid.map((m, i) => (m !== null && a[i] !== null ? m + k * a[i] : null)),
    lower: mid.map((m, i) => (m !== null && a[i] !== null ? m - k * a[i] : null)),
  };
}

// Canal de Donchian : plus haut et plus bas des n dernières barres.
export function donchian(rows, n = 20) {
  const upper = highest(col(rows, "high"), n), lower = lowest(col(rows, "low"), n);
  return { upper, lower, mid: upper.map((u, i) => (u !== null ? (u + lower[i]) / 2 : null)) };
}

// ---------- Oscillateurs ----------

export function rsi(close, n = 14) {
  const gain = nulls(close.length), loss = nulls(close.length);
  for (let i = 1; i < close.length; i++) {
    const d = close[i] - close[i - 1];
    gain[i] = Math.max(d, 0);
    loss[i] = Math.max(-d, 0);
  }
  const g = rma(gain, n), l = rma(loss, n);
  return g.map((v, i) => {
    if (v === null || l[i] === null) return null;
    if (l[i] === 0) return v === 0 ? 50 : 100;
    return 100 - 100 / (1 + v / l[i]);
  });
}

// Stochastique lent (14, 3, 3) : %K lissé et sa moyenne %D.
export function stochastic(rows, n = 14, smoothK = 3, d = 3) {
  const hh = highest(col(rows, "high"), n), ll = lowest(col(rows, "low"), n);
  const raw = rows.map((r, i) => {
    if (hh[i] === null) return null;
    return hh[i] > ll[i] ? 100 * (r.close - ll[i]) / (hh[i] - ll[i]) : 50;
  });
  const k = sma(raw, smoothK);
  return { k, d: sma(k, d) };
}

export function williamsR(rows, n = 14) {
  const hh = highest(col(rows, "high"), n), ll = lowest(col(rows, "low"), n);
  return rows.map((r, i) => (hh[i] === null ? null : hh[i] > ll[i] ? -100 * (hh[i] - r.close) / (hh[i] - ll[i]) : -50));
}

export function cci(rows, n = 20) {
  const tp = typical(rows), m = sma(tp, n), out = nulls(rows.length);
  for (let i = n - 1; i < rows.length; i++) {
    if (m[i] === null) continue;
    let dev = 0;
    for (let j = i - n + 1; j <= i; j++) dev += Math.abs(tp[j] - m[i]);
    dev /= n;
    out[i] = dev ? (tp[i] - m[i]) / (0.015 * dev) : 0;
  }
  return out;
}

export function roc(close, n = 12) {
  return close.map((c, i) => (i >= n && close[i - n] ? 100 * (c / close[i - n] - 1) : null));
}

// ---------- Volume ----------

export function obv(rows) {
  let v = 0;
  return rows.map((r, i) => {
    if (i > 0) v += r.close > rows[i - 1].close ? r.volume : r.close < rows[i - 1].close ? -r.volume : 0;
    return v;
  });
}

// Indice de flux monétaire (MFI) : RSI pondéré par le volume.
export function mfi(rows, n = 14) {
  const tp = typical(rows), out = nulls(rows.length);
  for (let i = n; i < rows.length; i++) {
    let pos = 0, neg = 0;
    for (let j = i - n + 1; j <= i; j++) {
      const flow = tp[j] * rows[j].volume;
      if (tp[j] > tp[j - 1]) pos += flow;
      else if (tp[j] < tp[j - 1]) neg += flow;
    }
    out[i] = neg === 0 ? (pos === 0 ? 50 : 100) : 100 - 100 / (1 + pos / neg);
  }
  return out;
}

// ---------- Niveaux ----------

// Points pivots classiques de la période précédente (jour pour les bougies
// de moins d'un jour, semaine pour les bougies journalières).
export function pivots(rows, periodSec) {
  if (!rows.length) return null;
  const key = (t) => (periodSec === 604800 ? Math.floor((t / 86400 + 3) / 7) : Math.floor(t / periodSec));
  const now = key(rows[rows.length - 1].time);
  const prev = rows.filter((r) => key(r.time) === now - 1);
  if (!prev.length) return null;
  const H = Math.max(...prev.map((r) => r.high)), L = Math.min(...prev.map((r) => r.low));
  const C = prev[prev.length - 1].close, P = (H + L + C) / 3;
  return { P, R1: 2 * P - L, S1: 2 * P - H, R2: P + (H - L), S2: P - (H - L), R3: H + 2 * (P - L), S3: L - 2 * (H - P) };
}

export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
// Retracements de Fibonacci entre le plus haut et le plus bas des données :
// mesurés depuis le dernier extrême atteint.
export function fibonacci(rows) {
  if (rows.length < 2) return null;
  let hi = 0, lo = 0;
  rows.forEach((r, i) => { if (r.high > rows[hi].high) hi = i; if (r.low < rows[lo].low) lo = i; });
  const H = rows[hi].high, L = rows[lo].low, rising = hi > lo;
  return { high: H, low: L, rising, levels: FIB_RATIOS.map((k) => ({ ratio: k, price: rising ? H - k * (H - L) : L + k * (H - L) })) };
}
