// Panneau TrendGuard, page « Positions » : positions ouvertes, trades clos et
// analyse du portefeuille.

import { $, api, el, fdate, fpct, fpx, fR, nf, REASON, sign, table, up } from "../core.js";
import { S } from "../etat.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";

async function renderPositions() {
  const [pos, tr, an] = await Promise.all([api("/api/positions"), api("/api/trades"), api("/api/analyse").catch(() => null)]);
  renderAnalysis(an);
  const open = (a) => openDetail({ kind: "asset", asset: a });
  table($("#p-table"), ["Crypto", "Achat le", "Prix d'achat", "Cours", "Vente auto si clôture <", "Gain verrouillé", "Écart à la vente", "Résultat", "En R (≈)"],
    pos.positions.map((p) => {
      const locked = p.entry ? (p.stop / p.entry - 1) * 100 : null;
      return { key: p.asset, cells: [up(p.asset), fdate(p.entry_date), fpx(p.entry), fpx(p.price), fpx(p.stop), fpct(locked, 1), fpct(p.stop_dist_pct, 1), fpct(p.pnl_pct), fR(p.r)],
        cls: { 5: (locked || 0) >= 0 ? "up" : "down", 7: (p.pnl_pct || 0) >= 0 ? "up" : "down", 8: (p.r || 0) >= 0 ? "up" : "down" } };
    }), open, "Aucune position ouverte : le capital est à 100 % en USDT.");
  $("#p-sub").textContent = `${pos.positions.length} / ${S ? S.max_positions : 8}`;
  const trades = tr.trades;
  const wins = trades.filter((t) => t.pnl > 0).length;
  $("#t-sub").textContent = trades.length ? `${trades.length} trades · ${nf(0).format(wins / trades.length * 100)} % gagnants` : "";
  table($("#t-table"), ["Crypto", "Achat", "Vente", "Prix d'achat", "Prix de vente", "Raison", "Résultat", "En R", "Leçon"],
    trades.map((t) => ({ key: t.asset, cells: [up(t.asset), fdate(t.entry_date), fdate(t.date), fpx(t.entry), fpx(t.exit), REASON[t.reason] || t.reason || "", `${sign(t.pnl)}${nf(2).format(Math.abs(t.pnl || 0))} USDT`, fR(t.r), LESSON[t.lesson] || ""],
      cls: { 6: t.pnl > 0 ? "up" : "down", 7: t.r > 0 ? "up" : "down" } })), open,
    "Aucun trade clos pour l'instant : le premier apparaîtra ici quand le bot vendra une position.");
}

// Leçon de chaque trade clos (analyse après trade, trendguard/postmortem.py).
const LESSON = { tendance: "Tendance captée", faux_depart: "Faux départ (normal)", gain_rendu: "Gain rendu en partie", urgence: "Sortie d'urgence", ordinaire: "Trade ordinaire" };
const usdSigned = (v) => `${sign(v)}${nf(2).format(Math.abs(v || 0))} USDT`;

// Analyse du portefeuille : d'où viennent les résultats (attribution.py),
// risque d'un jour, tests de résistance (stress.py), qualité des données.
function renderAnalysis(a) {
  $("#a-card").hidden = !a;
  if (!a) return;
  const at = a.attribution || {}, st = a.stress || {}, q = a.qualite || {}, rk = a.risque || {};
  const rows = [["ok", `Résultat réalisé ${usdSigned(at.realized)}, en cours ${usdSigned(at.unrealized)}.`]];
  (at.why || []).forEach((w) => rows.push(["ok", w.charAt(0).toUpperCase() + w.slice(1) + "."]));
  if (rk.var_pct != null) rows.push(["ok", `Risque d'un jour : 1 jour sur 20, perte de plus de ${nf(1).format(rk.var_pct)} % du capital (${nf(1).format(rk.cvar_pct)} % en moyenne ces jours-là).`]);
  if (st.text) rows.push([/déclencherait/.test(st.text) ? "warn" : "ok", st.text]);
  if (q.text) rows.push([q.score >= 90 ? "ok" : "warn", `Qualité des données : ${q.text}.`]);
  $("#a-list").replaceChildren(...rows.map(([k, t]) => el("li", k, t)));
  $("#a-sub").textContent = st.day || q.day ? `décision du ${fdate(st.day || q.day)}` : "";
  table($("#a-table"), ["Crypto", "Trades clos", "Réalisé", "En cours", "Total"],
    (at.assets || []).map((r) => ({ key: r.asset, cells: [up(r.asset), String(r.trades), usdSigned(r.realized), usdSigned(r.unrealized), usdSigned(r.total)],
      cls: { 4: r.total >= 0 ? "up" : "down" } })),
    (asset) => openDetail({ kind: "asset", asset }), "Pas encore de résultat à attribuer.");
  table($("#a-stress"), ["Scénario", "Perte", "Part du capital"],
    (st.rows || []).map((r) => ({ cells: [r.name, `−${nf(2).format(r.loss_usdt)} USDT`, `−${nf(1).format(r.loss_pct)} %`], cls: { 2: "down" } })),
    null, "Aucune position : rien à tester, le capital est en USDT.", false);
}

register("positions", renderPositions, 15);
