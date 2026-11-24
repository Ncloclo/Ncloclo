// Panneau TrendGuard : fenêtre de détail d'un graphique. Une crypto :
// graphique d'analyse (analyse.js) ; le capital et le régime : leur courbe
// et un panneau. Période et zoom (zoom.js) : la période choisie glisse avec
// le temps ; pour une crypto, l'unité de temps suit la période. Réglages
// gardés d'une ouverture à l'autre.

import { $, $$, api, el, fdate, fpct, fpx, fR, fusd, prefs, toast, up, withLoader } from "./core.js";
import { buyMarkersOn, candleData, COLORS, legend, uniq } from "./charts.js";
import { AssetChart, DEFAULT_IDS, EquityChart, MAX_COLORS, MAX_PANES, PRESETS, RegimeChart, renderIndicatorMenu, technicalReading } from "./analyse.js";
import { S } from "./etat.js";
import { daysFor, intervalFor, IV_NAME, labelOf, limitFor, TimeZoom } from "./zoom.js";

// Une crypto : graphique d'analyse (js/analyse.js : prix, indicateurs,
// panneaux synchronisés, lecture technique). Le capital et le régime : leur
// courbe et un panneau. Les réglages sont gardés d'une ouverture à l'autre.
let D = null;
const detail = $("#detail");
const indMenu = $("#ind-menu"), indBtn = $("#ind-btn");
const savedIds = () => {
  const v = prefs.get("indicators", null);
  return v == null ? DEFAULT_IDS : v.split(",").filter(Boolean);
};
const pressed = (sel, key, value) => $$(`${sel} button`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[key] === value)));
const isAsset = () => D && D.view instanceof AssetChart;
function closeMenu() {
  indMenu.hidden = true;
  indBtn.setAttribute("aria-expanded", "false");
}
function closeDetail() {
  if (!D) return;
  clearInterval(D.timer);
  if (D.view) D.view.destroy();
  D = null;
  closeMenu();
  if (detail.open) detail.close();
  detail.classList.remove("full");
  $("#detail-full").setAttribute("aria-pressed", "false");
}
$("#detail-close").addEventListener("click", closeDetail);
detail.addEventListener("close", closeDetail);
// Échap ferme d'abord la liste des indicateurs, puis le graphique.
detail.addEventListener("cancel", (e) => {
  if (!indMenu.hidden) { e.preventDefault(); closeMenu(); indBtn.focus(); }
});
detail.addEventListener("click", (e) => {
  if (e.target === detail) closeDetail();
  else if (!indMenu.hidden && !indMenu.contains(e.target) && !indBtn.contains(e.target)) closeMenu();
});
$("#detail-full").addEventListener("click", (e) => {
  const on = detail.classList.toggle("full");
  e.currentTarget.setAttribute("aria-pressed", String(on));
  e.currentTarget.title = on ? "Quitter le plein écran" : "Plein écran";
});
$("#detail-fit").addEventListener("click", () => { if (D && D.zoom) D.zoom.reset(); });
$("#detail-zoom-in").addEventListener("click", () => { if (D && D.zoom) D.zoom.zoomIn(); });
$("#detail-zoom-out").addEventListener("click", () => { if (D && D.zoom) D.zoom.zoomOut(); });
$$("#detail-intervals button").forEach((b) => b.addEventListener("click", () => {
  if (!D) return;
  D.interval = b.dataset.interval;
  if (D.zoom) D.zoom.resume();                     // la période choisie, avec la nouvelle unité
  prefs.set("interval", D.interval);
  withLoader(`Unité de temps : ${b.textContent}…`, loadDetail, "detail-loader");
}));
$$("#detail-types button").forEach((b) => b.addEventListener("click", () => {
  prefs.set("ctype", b.dataset.ctype);
  pressed("#detail-types", "ctype", b.dataset.ctype);
  if (isAsset()) D.view.setType(b.dataset.ctype);
}));
$$("#detail-scales button").forEach((b) => b.addEventListener("click", () => {
  prefs.set("scale", b.dataset.scale);
  pressed("#detail-scales", "scale", b.dataset.scale);
  if (D && D.view) D.view.setScale(b.dataset.scale);
}));

// Liste des indicateurs : modèles, puis cases par famille.
$("#ind-presets").replaceChildren(...PRESETS.map(([key, label]) => {
  const b = el("button", "chip", label);
  b.type = "button";
  b.dataset.preset = key;
  return b;
}));
function renderMenu(focusId) {
  if (!isAsset()) return;
  const v = D.view, ids = v.ids();
  renderIndicatorMenu($("#ind-families"), v);
  $("#ind-count").textContent = String(ids.length);
  $("#ind-limits").textContent = ` · ${v.paneCount()} / ${MAX_PANES} panneaux · ${v.colorCount()} / ${MAX_COLORS} couleurs sur le prix`;
  const same = (a) => a.length === ids.length && a.every((id) => ids.includes(id));
  $$("#ind-presets [data-preset]").forEach((b) => b.setAttribute("aria-pressed", String(same(PRESETS.find((p) => p[0] === b.dataset.preset)[2]))));
  if (focusId) { const cb = $(`#ind-families [data-ind="${focusId}"]`); if (cb) cb.focus(); }
}
function indicatorsChanged(focusId) {
  prefs.set("indicators", D.view.ids().join(","));
  renderMenu(focusId);
  renderTech();
}
indBtn.addEventListener("click", () => {
  const open = indMenu.hidden;
  indMenu.hidden = !open;
  indBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    renderMenu();
    const first = indMenu.querySelector("[data-preset]");
    if (first) first.focus();
  }
});
$("#ind-close").addEventListener("click", () => { closeMenu(); indBtn.focus(); });
$("#ind-presets").addEventListener("click", (e) => {
  const b = e.target.closest("[data-preset]");
  if (!b || !isAsset()) return;
  D.view.setIds(PRESETS.find((p) => p[0] === b.dataset.preset)[2]);
  indicatorsChanged();
  toast(`Indicateurs : modèle « ${b.textContent} »`, "ok");
});
$("#ind-families").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-ind]");
  if (!cb || !isAsset()) return;
  const refused = D.view.toggle(cb.dataset.ind, cb.checked);
  if (refused) { cb.checked = !cb.checked; toast(refused, "err"); return; }
  indicatorsChanged(cb.dataset.ind);
});

// Lecture technique de la dernière bougie (colonne de droite) : texte normal,
// sens donné par un repère coloré (▲ haussier, ▼ baissier, ◆ à surveiller).
const SIGN = { up: ["▲", "haussier"], down: ["▼", "baissier"], warn: ["◆", "à surveiller"] };
function techList(rows) {
  const dl = el("dl", "dl tech-list");
  rows.forEach(([k, v, cls]) => {
    const dd = el("dd");
    const [mark, word] = SIGN[cls] || ["•", "neutre"];
    const sig = el("span", "sig " + (cls || "flat"), mark);
    sig.title = word;
    sig.setAttribute("aria-label", word);
    dd.append(sig, v);
    dl.append(el("dt", "", k), dd);
  });
  return dl;
}
function renderTech() {
  const box = $("#detail-tech");
  const r = isAsset() ? technicalReading(D.view, S ? S.regime_bull : null) : null;
  if (!r) { box.hidden = true; return; }
  const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;
  const head = el("h3", "", "Lecture technique");
  head.id = "tech-title";
  head.append(el("span", "sub", ` · bougies de ${IV_NAME[D.interval] || D.interval}`));
  const tally = el("p", "tally");
  tally.append(el("b", "up", plural(r.vote.up, "haussier")), " · ", el("b", "down", plural(r.vote.down, "baissier")), " · ", el("b", "", plural(r.vote.flat, "neutre")));
  const bar = el("div", "tally-bar");
  bar.setAttribute("aria-hidden", "true");
  [["up", r.vote.up], ["flat", r.vote.flat], ["down", r.vote.down]].forEach(([k, n]) => {
    if (!n) return;
    const seg = el("i", k);
    seg.style.flexGrow = String(n);
    bar.append(seg);
  });
  const parts = [head, tally, bar, techList(r.rows)];
  if (r.bot.length) parts.push(el("h3", "", "Règle du bot"), techList(r.bot));
  parts.push(el("p", "tech-note", "Lecture indicative de la dernière bougie, pour décrypter le marché. Le bot ne s'en sert pas : il suit sa règle et ses stops."));
  box.replaceChildren(...parts);
  box.hidden = false;
}

// Vue du graphique selon son genre (refaite au changement de thème).
function mountView() {
  const spec = D.spec, c = COLORS(), box = $("#detail-chart"), ro = $("#detail-readout");
  ro.replaceChildren();
  $$("#detail-chart > :not(.readout):not(.loader)").forEach((n) => n.remove());
  const scale = prefs.get("scale", "normal");
  if (spec.kind === "asset") {
    D.view = new AssetChart(box, ro, { ids: savedIds(), ctype: prefs.get("ctype", "candles"), scale });
    legend($("#detail-legend"), [[c.up, "Hausse"], [c.down, "Baisse"], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes"], [c.accent, "Entrée", true], [c.down, "Stop de clôture"], [c.down, "Stop catastrophe", true]]);
    $("#ind-count").textContent = String(D.view.ids().length);
  } else if (spec.kind === "equity") {
    D.view = new EquityChart(box, ro);
    legend($("#detail-legend"), [[c.up, "Au-dessus du départ"], [c.down, "En dessous du départ"], [c.up, "▲ Achats du bot"], [c.down, "▼ Ventes du bot"]]);
  } else {
    D.view = new RegimeChart(box, ro);
    legend($("#detail-legend"), [[c.ink, "BTC (clôture)"], [c.sma, "Moyenne 150 jours : au-dessus, achats autorisés"]]);
  }
  D.view.setScale(scale);
  mountZoom();
}
// Période et zoom du graphique : une crypto et le capital rechargent leurs
// données pour la période (unité de temps, jours de capital) ; le régime
// (un an de BTC au jour) la découpe sur place.
function mountZoom() {
  const kind = D.spec.kind, view = D.view;
  $("#detail-range").textContent = "";
  D.zoom = new TimeZoom(view.stack ? view.stack.main : null, { windows: $("#detail-windows"), label: $("#detail-range") }, {
    win: prefs.get("window:" + kind, "all"),
    extend: kind !== "regime",
    before: () => { if (view.stack) view.stack.leader = null; },   // le graphique principal mène ses panneaux
    onWindow: (key) => {
      prefs.set("window:" + kind, key);
      if (kind === "regime") return false;
      if (kind === "asset") {
        D.interval = intervalFor(key, D.interval);
        prefs.set("interval", D.interval);
      }
      withLoader(`Période : ${labelOf(key)}…`, loadDetail, "detail-loader");
      return true;
    },
  });
}
export function openDetail(spec) {
  closeDetail();
  const asset = spec.kind === "asset";
  D = { spec, interval: prefs.get("interval", "1h") };
  $("#detail-title").textContent = asset ? `${up(spec.asset)}/USDT` : spec.title;
  $$("#detail .asset-only").forEach((n) => { n.hidden = !asset; });
  pressed("#detail-types", "ctype", prefs.get("ctype", "candles"));
  pressed("#detail-scales", "scale", prefs.get("scale", "normal"));
  $("#detail-tech").hidden = true;
  $("#detail-stats").replaceChildren();          // pas de chiffres d'un autre graphique
  detail.showModal();
  mountView();
  withLoader("Chargement du graphique…", loadDetail, "detail-loader");
  D.timer = setInterval(loadDetail, 10000);
}
export function restyleDetail() {
  if (!D || !D.view) return;
  D.view.destroy();
  mountView();
  loadDetail();
}

function stats(rows) {
  $("#detail-stats").replaceChildren(...rows.flatMap(([k, v, cls]) => [el("dt", "", k), el("dd", cls || "", v)]));
}
async function loadDetail() {
  if (!D || !D.view) return;
  const spec = D.spec, view = D.view;
  pressed("#detail-intervals", "interval", D.interval);
  try {
    if (spec.kind === "asset") {
      const limit = limitFor(D.zoom.win, D.interval, 250, 500);   // la période et de quoi calculer les indicateurs
      const d = await api(`/api/candles?asset=${spec.asset}&interval=${D.interval}&limit=${limit}&levels=1`);
      if (!D || D.view !== view) return;
      view.update(d);
      const cd = candleData(d.candles);
      const lastC = cd[cd.length - 1], first = cd[0];
      const hi = Math.max(...cd.map((x) => x.high)), lo = Math.min(...cd.map((x) => x.low));
      const rows = [["Cours", fpx(lastC && lastC.close)], ["Sur la période", fpct(lastC && first ? (lastC.close / first.open - 1) * 100 : null), lastC && first && lastC.close >= first.open ? "up" : "down"], ["Plus haut", fpx(hi)], ["Plus bas", fpx(lo)]];
      if (d.position) {
        const p = d.position, px = lastC ? lastC.close : null;
        rows.push(["Prix d'achat", fpx(p.entry)], ["Stop de clôture", fpx(p.stop)], ["Stop catastrophe", fpx(p.disaster)],
          ["Résultat", fpct(px ? (px / p.entry - 1) * 100 : null), px >= p.entry ? "up" : "down"],
          ["En R (≈)", fR(px && p.risk ? (px - p.entry) * p.qty / p.risk : null)], ["Achat le", fdate(p.entry_date)]);
      } else rows.push(["Position", "aucune"]);
      const buys = d.markers.filter((m) => m.type === "buy");
      rows.push(["Achats du bot visibles", String(buys.length)]);
      if (buys.length) rows.push(["Dernier achat", `${fdate(buys[buys.length - 1].date)} à ${fpx(buys[buys.length - 1].price)}`]);
      if (d.stale) rows.push(["Données", "en retard (réseau)", "warn"]);
      stats(rows);
      renderTech();
      if (!indMenu.hidden) renderMenu();
    } else if (spec.kind === "equity") {
      const eq = await api(`/api/equity?days=${daysFor(D.zoom.win, 365)}`);
      if (!D || D.view !== view) return;
      const pts = uniq(eq.points.map((p) => ({ time: p.t, value: p.v })));
      const start = pts.length ? pts[0].value : 0;
      const dd = view.update(pts, fusd);
      if (view.series) view.series.setMarkers(buyMarkersOn(pts, eq.buys, COLORS(), true, eq.sells));
      const last = pts[pts.length - 1], hi = Math.max(...pts.map((p) => p.value));
      const worst = dd.length ? Math.min(...dd.map((p) => p.value)) : null;
      stats([["Capital", fusd(last && last.value)], ["Au départ de la courbe", fusd(start)],
        ["Variation", fpct(last && start ? (last.value / start - 1) * 100 : null), last && last.value >= start ? "up" : "down"],
        ["Plus haut", fusd(hi)], ["Baisse depuis le plus haut", fpct(last && hi ? (last.value / hi - 1) * 100 : null)],
        ["Pire baisse de la courbe", fpct(worst), worst < 0 ? "down" : ""], ["Points", String(pts.length)]]);
    } else {
      const reg = await api("/api/regime");
      if (!D || D.view !== view) return;
      view.update(reg);
      const l = reg.points[reg.points.length - 1];
      stats([["BTC", fpx(l && l.close)], [`Moyenne ${reg.sma} j`, fpx(l && l.sma)], ["Écart", fpct(l ? (l.close / l.sma - 1) * 100 : null), reg.bull ? "up" : "down"], ["Régime", reg.bull ? "Haussier : achats autorisés" : "Baissier : aucun achat", reg.bull ? "up" : "down"]]);
    }
    // La période choisie glisse jusqu'à la dernière donnée ; un zoom fait à
    // la main reste tel quel.
    D.zoom.data(...view.zoomData());
  } catch (e) {
    toast(`Graphique : ${e.message}`, "err");
  }
}

// Échap : ferme d'abord la liste des indicateurs, puis la fenêtre ; rend vrai
// si la touche a servi ici.
export function handleDetailEscape(e) {
  if (D && !indMenu.hidden) { e.preventDefault(); closeMenu(); indBtn.focus(); return true; }
  if (D) { closeDetail(); return true; }
  return false;
}
