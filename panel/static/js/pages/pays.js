// Panneau TrendGuard, page « Pays du monde » : chaque pays couvert par MSCI,
// classé par région et par niveau de développement de son marché (revue
// 2026), avec son principal indice, sa devise face au dollar et les
// indicateurs de la Banque mondiale (trendguard/pays.py). Information
// seulement : aucun ordre réel, aucune décision du bot n'en dépend.

import { $, $$, api, el, fdate, fpct, fpx, nf, prefs, withLoader } from "../core.js";
import { fillSpark } from "../charts.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";

const FILTERS = [["all", "Tous"], ["Développé", "Développés"], ["Émergent", "Émergents"], ["Frontière", "Frontières"], ["Autonome", "Autonomes"]];
const WINDOWS = [["1m", "1 mois"], ["3m", "3 mois"], ["1y", "1 an"], ["5y", "5 ans"]];
const CLASS_TAG = { "Développé": "up", "Émergent": "watch", "Frontière avancé": "warn", "Frontière": "warn", "Autonome": "muted" };
const KPIS = [["all", "Pays classés"], ["Développé", "Développés"], ["Émergent", "Émergents"], ["Frontière", "Frontières"], ["Autonome", "Autonomes"]];
let filter = "all", win = WINDOWS.some(([k]) => k === prefs.get("window:countries", "1y")) ? prefs.get("window:countries", "1y") : "1y";
let ROWS = [];

const sec = el("section", "page country-page");
sec.id = "page-countries";
sec.dataset.title = "Pays du monde";
const grid = el("div", "asset-grid country-grid");

function seg(id, label, items, attr, value) {
  const group = el("div", "tool-group"), lab = el("span", "tool-label", label), box = el("div", "seg");
  lab.id = id;
  box.setAttribute("role", "group");
  box.setAttribute("aria-labelledby", id);
  items.forEach(([key, text]) => {
    const b = el("button", "", text);
    b.type = "button";
    b.dataset[attr] = key;
    b.setAttribute("aria-pressed", String(key === value));
    box.append(b);
  });
  group.append(lab, box);
  return group;
}

// En-tête : sources, chiffres clés ; outils : classement, région, courbes, recherche.
(function build() {
  const head = el("div", "card"), top = el("div", "card-head"), kpis = el("div", "kpis market-kpis");
  top.append(el("h2", "", "Pays du monde"), el("span", "sub country-sub"));
  KPIS.forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.ckpi = key;
    box.append(el("span", "label", label), el("strong", "num", "—"));
    kpis.append(box);
  });
  head.append(top, el("p", "hint", "Chaque pays couvert par MSCI, classé selon sa revue 2026 : marché développé, émergent, frontière (dont « avancé ») ou autonome, et les changements annoncés. Pour chacun : son principal indice boursier (ou le fonds coté à New York qui suit son indice MSCI, en dollars), sa devise face au dollar, et les derniers chiffres de la Banque mondiale. Information seulement : aucun ordre réel."), kpis);
  const tools = el("div", "toolbar");
  const regionGroup = el("div", "tool-group"), regionLabel = el("label", "tool-label", "Région"), region = el("select", "country-region");
  region.id = "country-region";
  regionLabel.htmlFor = region.id;
  region.append(Object.assign(el("option", "", "Toutes"), { value: "all" }));
  regionGroup.append(regionLabel, region);
  const search = el("input", "country-search");
  search.type = "search";
  search.placeholder = "Rechercher (France, Côte d'Ivoire…)";
  search.setAttribute("aria-label", "Rechercher un pays");
  tools.append(seg("cf-class", "Afficher", FILTERS, "cfilter", "all"), regionGroup, seg("cf-win", "Courbes", WINDOWS, "cwin", win), search);
  sec.append(head, tools, grid);
  $("#pages").append(sec);
  $$("[data-cfilter]", sec).forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.cfilter;
    $$("[data-cfilter]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    withLoader(`Pays : ${b.textContent}…`, async () => arrange());
  }));
  $$("[data-cwin]", sec).forEach((b) => b.addEventListener("click", () => {
    win = b.dataset.cwin;
    prefs.set("window:countries", win);
    $$("[data-cwin]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    withLoader(`Courbes : ${b.textContent}…`, render);
  }));
  region.addEventListener("change", () => arrange());
  search.addEventListener("input", () => arrange());
})();

const fold = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
function arrange() {
  const q = fold($(".country-search", sec).value.trim()), region = $(".country-region", sec).value;
  $$(".country", grid).forEach((card) => {
    const r = ROWS.find((x) => x.iso === card.dataset.iso);
    card.hidden = !r || !((filter === "all" || r.msci.startsWith(filter)) && (region === "all" || r.region === region)
      && (!q || fold(r.name).includes(q) || r.iso.toLowerCase() === q || (r.index && fold(r.index.name).includes(q))));
  });
  // Un titre de région n'apparaît que si l'un de ses pays est affiché.
  $$(".grid-head", grid).forEach((h) => {
    h.hidden = !$$(`.country[data-region="${h.dataset.region}"]:not([hidden])`, grid).length;
  });
}

const pct1 = (v) => (v == null ? "–" : `${nf(1).format(v)} %`);
function macro(wb) {
  const i = (wb && wb.indicators) || {}, year = (k) => (i[k] ? ` (${i[k][1]})` : "");
  const parts = [];
  if (i.growth) parts.push(`croissance ${fpct(i.growth[0], 1)}${year("growth")}`);
  if (i.inflation) parts.push(`inflation ${pct1(i.inflation[0])}${year("inflation")}`);
  if (i.unemployment) parts.push(`chômage ${pct1(i.unemployment[0])}`);
  if (i.debt) parts.push(`dette de l'État ${nf(0).format(i.debt[0])} % du PIB`);
  if (i.current) parts.push(`solde courant ${fpct(i.current[0], 1)} du PIB`);
  if (i.gdp) parts.push(`PIB ${nf(0).format(i.gdp[0] / 1e9)} Md$`);
  return parts.length ? parts.join(" · ") : "Chiffres de la Banque mondiale : pas encore lus.";
}
function currency(c) {
  if (!c || c.code === "USD") return "Devise : le dollar américain.";
  if (c.value == null) return `Devise : ${c.code}${c.note ? `, ${c.note}` : ", sans cours chez Yahoo"}.`;
  const rate = c.inverse ? `1 $ = ${fpx(c.value)} ${c.code}` : `1 ${c.code} = ${fpx(c.value)} $`;
  return `Devise : ${rate} ; ${fpct(c.change_1y, 1)} face au dollar sur un an${c.note ? ` (${c.note})` : ""}.`;
}

function makeCard(r, k) {
  const card = el("article", "card asset country");
  card.dataset.iso = r.iso;
  card.dataset.region = r.region;
  card.style.animationDelay = (Math.min(k, 30) * 20) + "ms";
  const top = el("div", "row");
  top.append(el("span", "tk", `${r.flag} ${r.name}`), el("span", "tag " + (CLASS_TAG[r.msci] || "muted"), r.msci));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 200 42");
  svg.setAttribute("preserveAspectRatio", "none");
  const pxRow = el("div", "row");
  pxRow.append(el("span", "px"), el("span", "chg"));
  const bottom = el("div", "row");
  bottom.append(el("span", "tags"), el("span", "sub vol"));
  card.append(top, el("span", "name"), pxRow, svg, el("p", "why idx"), el("p", "why cur"), el("p", "rank mac"), bottom);
  if (r.index && r.index.code) {                       // le BRVM Composite n'a pas encore de graphique
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `${r.name} : ouvrir le graphique de ${r.index.name}`);
    const open = () => openDetail({ kind: "market", code: r.index.code, title: r.index.name, label: r.name });
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  } else card.classList.add("no-index");
  return card;
}

async function render() {
  const d = await api(`/api/countries?window=${win}`);
  ROWS = d.rows;
  const region = $(".country-region", sec);
  if (region.options.length === 1) d.regions.forEach((x) => region.append(Object.assign(el("option", "", x), { value: x })));
  if (!grid.children.length) {
    let k = 0;
    d.regions.forEach((reg) => {
      const head = el("h3", "grid-head", reg);
      head.dataset.region = reg;
      grid.append(head);
      d.rows.filter((r) => r.region === reg).forEach((r) => grid.append(makeCard(r, k++)));
    });
  }
  d.rows.forEach((r) => {
    const card = grid.querySelector(`.country[data-iso="${r.iso}"]`);
    if (!card) return;
    const wb = r.wb || {};
    card.querySelector(".name").textContent = [r.region, wb.income, wb.capital && `capitale ${wb.capital}`].filter(Boolean).join(" · ");
    const px = card.querySelector(".px"), chg = card.querySelector(".chg"), svg = card.querySelector(".spark");
    const ix = r.index;
    px.textContent = ix ? fpx(ix.close) : "";
    chg.textContent = ix && ix.change != null ? `${ix.change >= 0 ? "▲" : "▼"} ${fpct(ix.change)}` : "";
    chg.className = "chg " + (ix && (ix.change || 0) >= 0 ? "up" : "down");
    px.parentElement.hidden = !ix;                     // sans indice : ni cours, ni variation…
    svg.style.display = ix ? "" : "none";              // … ni courbe (un SVG ignore « hidden »)
    if (ix) {
      if (svg.dataset.win !== win) { delete svg.dataset.drawn; svg.classList.remove("reveal"); void svg.getBoundingClientRect(); svg.dataset.win = win; }
      fillSpark(svg, ix.spark || []);
    }
    card.querySelector(".idx").textContent = !ix ? "Pas d'indice lisible chez Yahoo Finance pour ce marché."
      : ix.trend == null ? `${ix.name} : ${fpct(ix.change)} sur la séance, ${fpct(ix.ytd)} depuis le 31 décembre ; historique en construction (page BRVM).`
        : `${ix.name} : ${ix.trend ? "tendance haussière" : "tendance baissière"}${ix.signal ? ", signal d'achat de la règle" : ""}.`;
    card.querySelector(".cur").textContent = currency(r.currency);
    card.querySelector(".mac").textContent = macro(wb);
    card.querySelector(".tags").replaceChildren(...[r.note && el("span", "tag warn note", r.note)].filter(Boolean));
    card.querySelector(".vol").textContent = ix && ix.date ? `Séance du ${fdate(ix.date)}` : "";
  });
  const count = (k) => d.rows.filter((r) => k === "all" || r.msci.startsWith(k)).length;
  $$("[data-ckpi]", sec).forEach((b) => { b.querySelector("strong").textContent = String(count(b.dataset.ckpi)); });
  const withIndex = d.rows.filter((r) => r.index).length;
  $(".country-sub", sec).textContent = `${d.rows.length} pays · ${withIndex} avec un indice · `
    + (d.fetched_at ? `Banque mondiale lue le ${fdate(d.fetched_at)}` : "Banque mondiale pas encore lue")
    + (d.error ? ` · dernière lecture ratée : ${d.error}` : "");
  arrange();
}

register("countries", render, 300);
