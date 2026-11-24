// Panneau TrendGuard, page « BRVM » : la bourse régionale des huit pays de
// l'UEMOA, à Abidjan (trendguard/brvm.py). Indices, activité du marché et
// cours de chaque action, lus chaque jour après la séance sur les pages
// publiques de brvm.org. Information seulement : aucun ordre réel (il
// faudrait une SGI agréée).

import { $, $$, api, el, fdate, fpct, nf, table } from "../core.js";
import { fillSpark } from "../charts.js";
import { register } from "../navigation.js";

const SORTS = [["change", "Variation du jour"], ["volume", "Volume"], ["name", "Société"]];
const FILTERS = [["all", "Toutes"], ["up", "En hausse"], ["down", "En baisse"]];
let filter = "all", DATA = null;

const sec = el("section", "page brvm-page");
sec.id = "page-brvm";
sec.dataset.title = "BRVM (Abidjan)";
const fcfa = (v) => (v == null ? "–" : v >= 1e9 ? `${nf(0).format(v / 1e9)} Md FCFA` : `${nf(0).format(v / 1e6)} M FCFA`);

(function build() {
  const head = el("div", "card"), top = el("div", "card-head"), kpis = el("div", "kpis market-kpis");
  top.append(el("h2", "", "BRVM : la bourse de l'UEMOA"), el("span", "sub brvm-sub"));
  [["BRVM - COMPOSITE", "BRVM Composite"], ["BRVM-30", "BRVM 30"], ["BRVM - PRESTIGE", "BRVM Prestige"],
    ["stocks_cap", "Capitalisation des actions"], ["turnover", "Valeur des échanges"]].forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.bkpi = key;
    box.append(el("span", "label", label), el("strong", "num", "—"), el("span", "sub", ""));
    kpis.append(box);
  });
  head.append(top, el("p", "hint", "Bourse régionale des valeurs mobilières, à Abidjan : la bourse commune du Bénin, du Burkina Faso, de la Côte d'Ivoire, de la Guinée-Bissau, du Mali, du Niger, du Sénégal et du Togo (marché frontière pour MSCI). Lue chaque jour après la séance sur les pages publiques de brvm.org. La BRVM ne publie pas d'historique : le bot construit le sien, séance après séance ; sa règle s'appliquera quand il sera assez long (150 séances pour la tendance, 250 pour un achat). Information seulement : aucun ordre réel (il faudrait une SGI agréée)."), kpis);
  const idxCard = el("div", "card"), idxHead = el("div", "card-head");
  idxHead.append(el("h2", "", "Indices"), el("span", "sub", "clôture, variation du jour et depuis le 31 décembre"));
  const idxTable = el("table");
  idxTable.id = "brvm-indices";
  const idxWrap = el("div", "table-wrap");
  idxWrap.append(idxTable);
  idxCard.append(idxHead, idxWrap, el("div", "spark-row"));
  const stCard = el("div", "card"), stHead = el("div", "card-head");
  stHead.append(el("h2", "", "Actions cotées"), el("span", "sub brvm-count"));
  const tools = el("div", "toolbar"), group = el("div", "tool-group"), lab = el("span", "tool-label", "Afficher"), seg = el("div", "seg");
  lab.id = "bf-dir";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-labelledby", lab.id);
  FILTERS.forEach(([key, text]) => {
    const b = el("button", "", text);
    b.type = "button";
    b.dataset.bfilter = key;
    b.setAttribute("aria-pressed", String(key === filter));
    seg.append(b);
  });
  group.append(lab, seg);
  const sortGroup = el("div", "tool-group"), sortLabel = el("label", "tool-label", "Trier par"), sort = el("select", "brvm-sort");
  sort.id = "brvm-sort";
  sortLabel.htmlFor = sort.id;
  SORTS.forEach(([key, text]) => { const o = el("option", "", text); o.value = key; sort.append(o); });
  sortGroup.append(sortLabel, sort);
  const search = el("input", "brvm-search");
  search.type = "search";
  search.placeholder = "Rechercher (Sonatel, SGBCI…)";
  search.setAttribute("aria-label", "Rechercher une action de la BRVM");
  tools.append(group, sortGroup, search);
  const stTable = el("table");
  stTable.id = "brvm-stocks";
  const wrap = el("div", "table-wrap");
  wrap.append(stTable);
  stCard.append(stHead, tools, wrap);
  sec.append(head, idxCard, stCard);
  $("#pages").append(sec);
  $$("[data-bfilter]", sec).forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.bfilter;
    $$("[data-bfilter]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    drawStocks();
  }));
  sort.addEventListener("change", drawStocks);
  search.addEventListener("input", drawStocks);
})();

const dir = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
function drawStocks() {
  if (!DATA) return;
  const q = $(".brvm-search", sec).value.trim().toLowerCase(), key = $(".brvm-sort", sec).value;
  const rows = DATA.stocks.filter((s) => (filter === "all" || (filter === "up" ? s.change > 0 : s.change < 0))
    && (!q || s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)))
    .sort((a, b) => (key === "name" ? a.name.localeCompare(b.name, "fr") : (b[key] || 0) - (a[key] || 0)))
    .map((s) => ({ cells: [s.symbol, s.name, nf(0).format(s.close), fpct(s.change), nf(0).format(s.volume || 0), nf(0).format(s.prev)],
      cls: [null, null, null, dir(s.change), null, null] }));
  table($("#brvm-stocks"), ["Symbole", "Société", "Cours (FCFA)", "Jour", "Volume", "Veille"], rows, null,
    DATA.session ? "Aucune action ne correspond." : "Pas encore lue : première lecture après la prochaine séance.", false);
  $(".brvm-count", sec).textContent = `${rows.length} sur ${DATA.stocks.length}`;
}

async function render() {
  const d = await api("/api/brvm");
  DATA = d;
  const by = Object.fromEntries(d.indices.map((r) => [r.name.toUpperCase(), r]));
  $$("[data-bkpi]", sec).forEach((box) => {
    const k = box.dataset.bkpi, r = by[k], strong = box.querySelector("strong"), sub = box.querySelector(".sub");
    if (k === "stocks_cap" || k === "turnover") {
      strong.textContent = fcfa(d.activity[k]);
      sub.textContent = k === "turnover" ? "sur la séance" : "";
      return;
    }
    strong.textContent = r ? nf(2).format(r.close) : "—";
    strong.className = "num " + (r ? dir(r.change) : "");
    sub.textContent = r ? `${fpct(r.change)} sur la séance · ${fpct(r.ytd)} depuis le 31/12` : "pas encore lu";
  });
  table($("#brvm-indices"), ["Indice", "Clôture", "Jour", "Depuis le 31/12", "Veille"],
    d.indices.map((r) => ({ cells: [r.name, nf(2).format(r.close), fpct(r.change), fpct(r.ytd), nf(2).format(r.prev)],
      cls: [null, null, dir(r.change), dir(r.ytd), null] })), null,
    "Pas encore lus : première lecture après la prochaine séance.", false);
  const main = d.indices.find((r) => r.name.toUpperCase() === "BRVM - COMPOSITE");
  const row = $(".spark-row", sec);
  row.replaceChildren();
  if (main && main.spark && main.spark.length > 1) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 200 42");
    svg.setAttribute("preserveAspectRatio", "none");
    row.append(el("span", "sub", "BRVM Composite, séances gardées par le bot"), svg);
    fillSpark(svg, main.spark);
  }
  $(".brvm-sub", sec).textContent = (d.session ? `séance du ${fdate(d.session)}` : "pas encore lue")
    + ` · historique du bot : ${d.sessions} séance${d.sessions > 1 ? "s" : ""}`
    + (d.error ? ` · dernière lecture ratée : ${d.error}` : "");
  drawStocks();
}

register("brvm", render, 300);
