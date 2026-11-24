// Panneau TrendGuard, page « Cryptos » : les 21 paires, leur courbe de 48 h et
// le choix des cryptos achetables.

import { $, $$, api, CANDIDATE, el, fpct, fpx, fR, flashValue, fvol, STATUS, toast, up, withLoader } from "../core.js";
import { fillSpark } from "../charts.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";

let assetFilter = "all";
const sparkCache = new Map();
const sparkObs = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  entries.forEach((en) => { if (en.isIntersecting) { sparkObs.unobserve(en.target); drawSpark(en.target); } });
}) : null;
async function drawSpark(card) {
  const a = card.dataset.asset, hit = sparkCache.get(a);
  let closes = hit && Date.now() - hit.t < 300000 ? hit.v : null;
  if (!closes) {
    try {
      const d = await api(`/api/candles?asset=${a}&interval=1h&limit=48`);
      closes = d.candles.map((r) => r[4]);
      const times = d.candles.map((r) => r[0]);
      const marks = d.markers.map((m) => ({ i: times.indexOf(m.t), type: m.type })).filter((m) => m.i >= 0);
      sparkCache.set(a, { t: Date.now(), v: closes, m: marks });
    } catch { return; }
  }
  const svg = card.querySelector(".spark");
  if (svg) fillSpark(svg, closes, (sparkCache.get(a) || {}).m || []);
}
$$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  assetFilter = b.dataset.filter;
  $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  withLoader(`Sélection : ${b.textContent}…`, arrangeAssets);
}));
$("#asset-search").addEventListener("input", () => arrangeAssets());
$("#asset-sort").addEventListener("change", (e) => {
  withLoader(`Tri : ${e.currentTarget.selectedOptions[0].textContent}…`, arrangeAssets);
});
let ASSETS = [];
function arrangeAssets() {
  const q = $("#asset-search").value.trim().toLowerCase(), sort = $("#asset-sort").value;
  const grid = $("#asset-grid");
  const rows = ASSETS.slice().sort((a, b) => sort === "change" ? (b.change_pct || 0) - (a.change_pct || 0)
    : sort === "volume" ? (b.volume_quote || 0) - (a.volume_quote || 0) : a.asset.localeCompare(b.asset));
  rows.forEach((r) => {
    const card = grid.querySelector(`[data-asset="${r.asset}"]`);
    if (!card) return;
    const show = (assetFilter === "all" || (assetFilter === "held" && r.held) || (assetFilter === "vetoed" && r.vetoed)
      || (assetFilter === "selected" && r.selected)
      || (assetFilter === "watch" && CANDIDATE.has(r.status)))
      && (!q || r.asset.includes(q) || r.name.toLowerCase().includes(q));
    card.hidden = !show;
    grid.append(card);
  });
}
let SEL = { mode: "auto", active: [] };
// Cases cochées ou décochées pas encore enregistrées (crypto → cochée) : ni une
// actualisation de la liste, ni une liste lue avant le dernier enregistrement
// (version dépassée) ne les effacent.
const PICKS = new Map();
let selVersion = 0, pickTimer = 0, selQueue = Promise.resolve();
const picked = (a) => (PICKS.has(a) ? PICKS.get(a) : SEL.active.includes(a));
function syncPicks() {
  ASSETS.forEach((a) => { a.selected = picked(a.asset); });
  $$("#asset-grid .pick input").forEach((box) => {
    box.checked = picked(box.dataset.pick);
    box.disabled = SEL.mode === "auto";
    box.closest(".asset").classList.toggle("unselected", !box.checked);
  });
}
// Un enregistrement à la fois, dans l'ordre des clics : chacun part de la
// sélection laissée par le précédent.
function queueSelection(job) {
  selQueue = selQueue.then(job, job);
  return selQueue;
}
function renderSelection(sel) {
  SEL = sel;
  $$("[data-sel]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sel === sel.mode)));
  $("#sel-count").textContent = `${sel.active.length} / ${sel.universe}`;
  $("#sel-actions").hidden = sel.mode === "auto";
  $("#sel-help").textContent = sel.mode === "auto"
    ? `Sélection auto (recommandée) : le bot peut acheter les 21 cryptos, toutes cochées. ${sel.note} Le classement de chaque carte est affiché pour information.`
    : `Sélection manuelle : au départ, les ${sel.n_top} cryptos les plus rentables sur 2 ans (bénéfice des achats ET des ventes) sont cochées${sel.top.length ? ` (${sel.top.map(up).join(", ")})` : ", dès que le classement est calculé"} ; cochez ou décochez celles que le bot peut acheter.${sel.active.length ? "" : " Tant qu'aucune n'est cochée, le bot n'achète rien."} Une crypto décochée déjà détenue reste gérée jusqu'à sa vente. Pour mémoire, les 21 ont fait mieux par le passé (sélection auto).`;
}
async function saveSelection(mode, manual, preset) {
  selVersion += 1;
  try {
    const r = await api("/api/selection", { body: preset ? { mode, preset } : manual ? { mode, manual } : { mode } });
    toast(r.mode === "auto" ? `Sélection auto : les ${r.active.length} cryptos sont achetables.` : r.active.length ? `Sélection manuelle : ${r.active.length} crypto${r.active.length > 1 ? "s" : ""} achetable${r.active.length > 1 ? "s" : ""}.` : "Sélection manuelle : aucune crypto cochée, cochez celles que le bot peut acheter.", "ok");
    renderSelection(r);
    syncPicks();                        // cases à jour tout de suite, sans attendre la liste
    return r;
  } catch (e) {
    toast(`Sélection non enregistrée : ${e.message}`, "err");
    return null;
  } finally {
    selVersion += 1;                    // une liste lue pendant l'enregistrement est dépassée
  }
}
// Les cases en attente, appliquées à la dernière sélection enregistrée.
async function savePicks() {
  const sent = new Map(PICKS), want = new Set(SEL.active);
  sent.forEach((on, a) => { if (on) want.add(a); else want.delete(a); });
  const r = await saveSelection("manual", ASSETS.map((x) => x.asset).filter((a) => want.has(a)));
  sent.forEach((on, a) => { if (!r || PICKS.get(a) === on) PICKS.delete(a); });
  syncPicks();
  renderAssets().catch(() => {});
}
function onPick(box) {
  PICKS.set(box.dataset.pick, box.checked);
  box.closest(".asset").classList.toggle("unselected", !box.checked);
  clearTimeout(pickTimer);
  pickTimer = setTimeout(() => queueSelection(savePicks).catch(() => {}), 400);
}
// Les boutons de sélection remplacent les cases pas encore enregistrées.
function presetSelection(text, mode, manual, preset) {
  clearTimeout(pickTimer);
  PICKS.clear();
  return withLoader(text, () => queueSelection(async () => {
    await saveSelection(mode, manual, preset);
    await renderAssets();
  }));
}
$$("[data-sel]").forEach((b) => b.addEventListener("click", () => {
  if (SEL.mode === b.dataset.sel) return;
  const auto = b.dataset.sel === "auto";
  presetSelection(auto ? "Sélection auto : les 21 cryptos…" : "Sélection manuelle : les 10 cryptos les plus rentables…",
    b.dataset.sel, undefined, auto ? undefined : "top");          // manuel : les 10 plus rentables
}));
$("[data-sel-top]").addEventListener("click", () => {
  presetSelection("Sélection : les 10 cryptos les plus rentables…", "manual", undefined, "top");
});
$$("[data-sel-all]").forEach((b) => b.addEventListener("click", () => {
  const all = b.dataset.selAll === "1";
  presetSelection(all ? "Sélection : les 21 cryptos…" : "Sélection : aucune crypto…", "manual",
    all ? ASSETS.map((a) => a.asset) : []);
}));
async function renderAssets() {
  const v = selVersion;
  const d = await api("/api/assets");
  ASSETS = d.assets;
  if (v === selVersion) renderSelection(d.selection);   // sinon : lue avant le dernier enregistrement
  const grid = $("#asset-grid");
  d.assets.forEach((r, k) => {
    let card = grid.querySelector(`[data-asset="${r.asset}"]`);
    if (!card) {
      card = el("article", "card asset");
      card.dataset.asset = r.asset;
      card.style.animationDelay = (k * 25) + "ms";
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `${up(r.asset)} : ouvrir le graphique`);
      const top = el("div", "row");
      top.append(el("span", "tk", up(r.asset)), el("span", "chg"));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "spark");
      svg.setAttribute("viewBox", "0 0 200 42");
      svg.setAttribute("preserveAspectRatio", "none");
      const bottom = el("div", "row");
      bottom.append(el("span", "tags"), el("span", "sub vol"));
      const pick = el("label", "pick");
      const box = el("input");
      box.type = "checkbox";
      box.dataset.pick = r.asset;
      box.setAttribute("aria-label", `${up(r.asset)} : le bot peut l'acheter`);
      pick.append(box, el("span", "", "Achetable"));
      pick.addEventListener("click", (e) => e.stopPropagation());
      pick.addEventListener("keydown", (e) => e.stopPropagation());
      box.addEventListener("change", () => onPick(box));
      card.append(top, el("span", "name", r.name), pick, el("span", "px"), svg, el("p", "rank"), el("p", "why"), bottom);
      const open = () => openDetail({ kind: "asset", asset: r.asset });
      card.addEventListener("click", open);
      card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      grid.append(card);
      if (sparkObs) sparkObs.observe(card); else drawSpark(card);
    }
    const px = card.querySelector(".px");
    px.textContent = fpx(r.price);
    flashValue(px, Number(px.dataset.v), r.price);
    px.dataset.v = r.price;
    const chg = card.querySelector(".chg");
    chg.textContent = r.change_pct == null ? "–" : `${r.change_pct >= 0 ? "▲" : "▼"} ${fpct(r.change_pct)}`;
    chg.className = "chg " + ((r.change_pct || 0) >= 0 ? "up" : "down");
    const tags = card.querySelector(".tags");
    const st = STATUS[r.status];
    tags.replaceChildren(...[r.held && el("span", "tag held", "Détenue"), r.vetoed && el("span", "tag vetoed", "Achats bloqués"),
      st && r.status !== "held" && r.status !== "veto" && el("span", "tag " + st[0], st[1])].filter(Boolean));
    const why = card.querySelector(".why");
    why.textContent = r.why || "";
    why.hidden = !r.why;
    const rk = card.querySelector(".rank");
    rk.textContent = r.rank ? `N° ${r.rank.rank} sur 2 ans : ${fR(r.rank.total_r)} en ${r.rank.trades} trade${r.rank.trades > 1 ? "s" : ""} (achats et ventes)` : "";
    rk.hidden = !r.rank;
    if (r.veto_reason) card.title = r.veto_reason;
    card.querySelector(".vol").textContent = `Vol. 24 h ${fvol(r.volume_quote)}`;
  });
  syncPicks();
  arrangeAssets();
  if (d.stale) toast("Cours Binance momentanément indisponibles : dernières valeurs affichées.", "err");
}

register("assets", renderAssets, 15);
