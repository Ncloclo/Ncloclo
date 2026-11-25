// Panneau TrendGuard, page « Tableau de bord » : capital (période et zoom
// de sa courbe, zoom.js), indicateurs clés, raisonnement du bot,
// anticipation de la prochaine clôture, positions et alertes.

import { $, api, countUp, el, fage, fdate, fdur, fmax2, fpct, fpx, ftime, fusd, LWC, nf, prefs, up } from "../core.js";
import { buyMarkersOn, COLORS, LIVE, makeChart, uniq } from "../charts.js";
import { refreshStatus, S } from "../etat.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";
import { loadNews, renderTicker } from "./actualites.js";
import { daysFor, TimeZoom, zoomBar } from "../zoom.js";

let dashChart = null, dashSeries = null, dashZoom = null;
let dashWin = prefs.get("window:dash", "1m");

// Courbe du capital : la période choisie (24 h… Tout) glisse avec le temps.
const equityDays = () => daysFor(dashWin, 30);
function mountDashChart() {
  dashChart = makeChart($("#d-equity-chart"), { handleScroll: false, handleScale: false, rightPriceScale: { visible: false }, timeScale: { visible: true } }, false, false);
  const c = COLORS();
  dashSeries = dashChart.addAreaSeries({ lineColor: c.accent, topColor: c.accent + "55", bottomColor: c.accent + "05", lineWidth: 2, priceLineVisible: false, ...LIVE });
  const bar = zoomBar(["1d", "7d", "1m", "3m", "all"]);
  $("#d-equity-side").append(bar.root);
  dashZoom = new TimeZoom(dashChart, bar.parts, {
    keys: ["1d", "7d", "1m", "3m", "all"], extend: true, interactive: false, win: dashWin,
    onWindow: (key) => {
      dashWin = key;
      prefs.set("window:dash", key);
      api(`/api/equity?days=${equityDays()}`).then(drawDashEquity).catch(() => { /* réessai au prochain rafraîchissement */ });
      return true;
    },
  });
  dashChart.timeScale().subscribeSizeChange(() => { if (!dashZoom.free) dashZoom.apply(); });
}
function drawDashEquity(eq) {
  if (!dashSeries) return;
  const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
  dashSeries.setData(pts);
  dashSeries.setMarkers(buyMarkersOn(pts, eq.buys, COLORS(), false, eq.sells));
  dashZoom.data(pts.map((p) => p.time));
}

// Capital dans votre monnaie (trendguard/devise.py), quelle qu'elle soit, et
// ce qu'un retrait complet donnerait, frais de change et impôt déduits.
function renderLocalMoney(d) {
  const node = $("#d-equity-local");
  if (!node) return;
  if (!d || !d.code || d.code === "USDT" || d.equity == null) { node.hidden = true; return; }
  const digits = d.decimals != null ? d.decimals : d.code === "FCFA" ? 0 : 2;
  const money = (v) => nf(digits).format(v) + " " + (d.label || d.code);
  node.textContent = `≈ ${money(d.equity)} · retrait complet : ${money(d.withdraw_net)} nets `
    + `(${fpct(d.gain_net_pct, 1)}, frais de change ${nf(1).format(d.change_cost * 100)} %, `
    + `impôt ${nf(0).format(d.tax_rate * 100)} %)`;
  node.className = "sub " + (d.gain_net_pct >= 0 ? "up" : "down");
  node.hidden = false;
}
async function renderDash() {
  if (!S) await refreshStatus();
  const newsP = loadNews().then((n) => { renderTicker(n); newsAlerts(n); }).catch(() => { /* bandeau : réessai au prochain rafraîchissement */ });
  api("/api/anticipation").then(renderAnticipation).catch(() => { /* réessai au prochain rafraîchissement */ });
  const [pos, eq, mind] = await Promise.all([api("/api/positions"), api(`/api/equity?days=${equityDays()}`), api("/api/reasoning")]);
  renderMind(mind);
  countUp($("#d-equity"), S.equity, fusd);
  const sub = $("#d-equity-sub");
  if (S.equity && S.start_equity) {
    const ch = (S.equity / S.start_equity - 1) * 100;
    sub.textContent = `${fpct(ch)} depuis le départ (${fusd(S.start_equity)})`;
    sub.className = "sub " + (ch >= 0 ? "up" : "down");
  } else sub.textContent = "Aucune décision encore prise";
  renderLocalMoney(S.devise);
  if (!dashChart && LWC) mountDashChart();
  drawDashEquity(eq);
  countUp($("#d-pos"), S.positions, (v) => `${Math.round(v)} / ${S.max_positions}`);
  $("#d-pos-sub").textContent = `${fmax2.format(S.risk_now_pct ?? S.risk_pct)} % du capital risqué par achat`;
  const reg = $("#d-regime");
  reg.textContent = S.regime_bull == null ? "—" : S.regime_bull ? "Haussier" : "Baissier";
  reg.className = S.regime_bull ? "up" : S.regime_bull === false ? "down" : "";
  const rd = (S.regime_detail || {}).texte;
  $("#d-regime-sub").textContent = (S.regime_bull ? "achats autorisés" : S.regime_bull === false ? "aucun achat, stops resserrés" : "") + (rd ? ` · ${rd}` : "");
  const dd = $("#d-dd");
  dd.textContent = S.drawdown_pct == null ? "—" : S.drawdown_pct < 0 ? fpct(S.drawdown_pct) : "Aucune";
  dd.className = "num " + (S.drawdown_pct < -10 ? "down" : "");
  $("#d-dd-sub").textContent = `arrêt d'urgence à −${nf(0).format(S.kill_drawdown_pct)} %`;
  $("#d-cycle").textContent = S.last_cycle_age_s == null ? "—" : "il y a " + fage(S.last_cycle_age_s);
  $("#d-cycle-sub").textContent = S.last_decision_day ? `décision : bougie du ${fdate(S.last_decision_day)}` : "";
  // Fraîcheur (étape 21) : une donnée ancienne n'est jamais montrée comme en direct.
  const fresh = (S.freshness || {}).cycle, badge = $("#d-fresh");
  if (badge) {
    badge.hidden = !fresh;
    if (fresh) {
      badge.textContent = fresh.label;
      badge.className = "badge fresh " + String(fresh.state).toLowerCase();
    }
  }
  const w = S.watch, wd = $("#d-watch");
  wd.textContent = !w ? "—" : w.sentiment > 0.2 ? "Positive" : w.sentiment < -0.2 ? "Négative" : "Neutre";
  wd.className = !w ? "" : w.sentiment > 0.2 ? "up" : w.sentiment < -0.2 ? "down" : "";
  $("#d-watch-sub").textContent = w ? (w.providers_total ? `${w.providers}/${w.providers_total} IA · ${fdate(w.day)}` : `mots-clés · ${fdate(w.day)}`) : "pas encore de rapport";

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
  if (S.safe_mode && S.safe_mode.active) alerts.push(["warn", `Mode sûr : aucun achat (${S.safe_mode.reason || "demandé"}) ; les positions restent protégées et peuvent être vendues. Pour le lever : python trendguard_bot.py mode-sur off`]);
  const sup = (S.autonomy && S.autonomy.supervisor) || {};
  if (S.state === "restarting") alerts.push(["warn", "Le bot s'est arrêté sur une erreur : relance automatique en cours."]);
  else if (S.state === "running" && !sup.running && !S.demo) alerts.push(["warn", "Relance automatique inactive (bot lancé hors du panneau) : ARRÊTER puis AUTO pour l'activer."]);
  if (sup.running && sup.restarts > 0 && sup.last_exit) alerts.push(["warn", `Le bot s'est relancé seul ${sup.restarts} fois (dernière erreur le ${fdate(sup.last_exit.at)}).`]);
  const power = S.autonomy && S.autonomy.power;
  if (power && power.ac === false && power.battery_pct != null) alerts.push(["crit", `PC sur batterie (${power.battery_pct} %) : branchez le chargeur. Sur batterie, le PC se met en veille capot fermé, puis s'éteint, et le bot s'arrête.`]);
  const gap = S.uptime && S.uptime.recent;
  if (gap) alerts.push(["warn", `Le bot a été arrêté ${fdur(gap.end - gap.start)} (du ${ftime(gap.start)} au ${ftime(gap.end)}) : ${gap.text}. Il a repris et rattrapé les contrôles manqués.`]);
  S.alerts.filter((c) => c.enabled && c.last && !c.last.ok).forEach((c) => alerts.push(["warn", `Les alertes ${c.label} n'arrivent pas (dernier envoi raté le ${ftime(c.last.at)}) : voir Réglages ▸ Sécurité.`]));
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
  $("#a-note").textContent = f.calibrated
    ? "Probabilités corrigées par l'expérience du bot : chaque prévision passée a été comparée à la clôture. Les règles ne changent pas : c'est la clôture de 00:00 UTC qui décide."
    : "Probabilités indicatives, calculées avec les cours du moment et la volatilité de chaque crypto. Les règles ne changent pas : c'est la clôture de 00:00 UTC qui décide.";
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

register("dash", renderDash, 15);
