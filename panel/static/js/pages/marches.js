// Panneau TrendGuard, pages des autres marchés, une par classe d'actifs
// (matières premières, devises, indices, actions, obligations), sur le
// modèle de la page Cryptos : chaque marché a sa carte (cours, variation,
// courbe, tendance et signal de la règle du bot) et sa fenêtre de détail
// (graphique, moyenne de 150 séances, niveau de cassure, position fictive).
// Information seulement : aucun ordre réel sur ces marchés.

import { $, $$, api, el, fdate, fpct, fpx, prefs, toast, withLoader } from "../core.js";
import { fillSpark } from "../charts.js";
import { register } from "../navigation.js";
import { openDetail } from "../detail.js";

// Une page par classe d'actifs, dans l'ordre du menu.
export const CLASSES = [
  { tab: "commodities", title: "Matières premières", what: "Métaux (or, argent, platine, palladium, cuivre, aluminium), énergie (pétrole WTI et Brent, essence, fioul, gaz naturel) et produits agricoles (blé, maïs, soja, café, sucre, coton), par leurs contrats à terme",
    note: "L'or se traite aussi sur Binance en jetons (PAXG, XAUT) : il est suivi dans la manche Binance fictive.", search: "Rechercher (or, pétrole…)" },
  { tab: "forex", title: "Devises", what: "L'euro, la livre, le yen, le franc suisse et les dollars australien, canadien et néo-zélandais face au dollar américain, et l'indice du dollar (DXY)",
    note: "L'euro se traite aussi sur Binance (EUR/USDT). Le franc CFA (XOF) n'a pas de page : sa parité avec l'euro est fixe (655,957).", search: "Rechercher (EUR/USD, yen…)" },
  { tab: "indices", title: "Indices boursiers", what: "Les grands indices boursiers : États-Unis (S&P 500, Nasdaq, Dow Jones, Russell 2000), Europe (CAC 40, DAX, FTSE 100, FTSE MIB, IBEX 35), Asie (Nikkei, Shanghai, Hang Seng, Nifty 50) et Brésil (Bovespa)",
    note: "Le fonds Nasdaq 100 existe aussi en jeton sur Binance (QQQB).", search: "Rechercher (S&P 500, CAC 40…)" },
  { tab: "stocks", title: "Actions", what: "De grandes actions américaines, françaises et asiatiques (TSMC), celles que Binance propose en jetons (bStocks), et les introductions récentes d'Anthropic et de SpaceX",
    note: "Choisies parmi les plus grandes d'aujourd'hui : leur passé est flatté (biais du survivant), elles ne comptent dans aucun verdict. Anthropic et SpaceX sont trop récentes pour la règle (250 séances d'historique).", search: "Rechercher (Nvidia, Apple…)" },
  { tab: "bonds", title: "Obligations", what: "Les obligations d'État américaines, courtes (1-3 ans), moyennes (7-10 ans) et longues (plus de 20 ans), par des fonds cotés (ETF)",
    note: "Un fonds d'obligations monte quand les taux baissent, et baisse quand ils montent.", search: "Rechercher (TLT, IEF…)" },
];
const FILTERS = [["all", "Tous"], ["trend", "Tendance haussière"], ["signal", "Signal d'achat"], ["held", "En portefeuille fictif"], ["binance", "Sur Binance"]];
const WINDOWS = [["1m", "1 mois"], ["3m", "3 mois"], ["1y", "1 an"], ["5y", "5 ans"]];
const SORTS = [["name", "Nom"], ["change", "Variation sur la période"], ["gap", "Écart à la moyenne 150"], ["mom", "Momentum 90 séances"]];
const KPIS = [["count", "Marchés suivis"], ["trend", "En tendance haussière"], ["signal", "Signaux d'achat du jour"], ["held", "En portefeuille fictif"]];

let win = WINDOWS.some(([k]) => k === prefs.get("window:market", "1y")) ? prefs.get("window:market", "1y") : "1y";
const PAGES = new Map();                    // onglet → { cls, rows, filter }

// Libellé court d'un marché (gras, en haut de sa carte).
function short(r) {
  if (r.category === "Matières premières") return r.code.toUpperCase();
  if (r.category === "Devises" || r.category === "Indices") return r.name.replace(/ \(.*\)$/, "");
  return String(r.symbol || r.code).split(".")[0].toUpperCase();
}
const token = (r) => (r.binance.match(/\(([^)]+)\)/) || [0, r.binance])[1];
const change = (r) => (r.spark && r.spark.length > 1 ? (r.spark[r.spark.length - 1] / r.spark[0] - 1) * 100 : null);

function segGroup(id, label, items, attr, value) {
  const group = el("div", "tool-group"), lab = el("span", "tool-label", label), seg = el("div", "seg");
  lab.id = id;
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-labelledby", id);
  items.forEach(([key, text]) => {
    const b = el("button", "", text);
    b.type = "button";
    b.dataset[attr] = key;
    b.setAttribute("aria-pressed", String(key === value));
    seg.append(b);
  });
  group.append(lab, seg);
  return group;
}

// La section d'une classe d'actifs : en-tête, outils, grille des cartes.
function buildPage(cls) {
  const sec = el("section", "page market-page");
  sec.id = "page-" + cls.tab;
  sec.dataset.title = cls.title;
  const head = el("div", "card market-head"), top = el("div", "card-head"), kpis = el("div", "kpis market-kpis");
  top.append(el("h2", "", cls.title), el("span", "sub market-sub"));
  KPIS.forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.kpi = key;
    box.append(el("span", "label", label), el("strong", "num", "—"));
    kpis.append(box);
  });
  head.append(top, el("p", "hint", `${cls.what} : cours de clôture de chaque séance (Yahoo Finance), suivis chaque jour avec la règle du bot, tendance propre à chaque marché. Argent fictif seulement : aucun ordre réel sur ces marchés. ${cls.note}`), kpis);
  const tools = el("div", "toolbar");
  const sortGroup = el("div", "tool-group"), sortLabel = el("label", "tool-label", "Trier par"), sort = el("select", "market-sort");
  sort.id = `sort-${cls.tab}`;
  sortLabel.htmlFor = sort.id;
  SORTS.forEach(([key, text]) => { const o = el("option", "", text); o.value = key; sort.append(o); });
  sortGroup.append(sortLabel, sort);
  const search = el("input", "market-search");
  search.type = "search";
  search.placeholder = cls.search;
  search.setAttribute("aria-label", `Rechercher : ${cls.title.toLowerCase()}`);
  tools.append(segGroup(`mf-${cls.tab}`, "Afficher", FILTERS, "mfilter", "all"),
    segGroup(`mw-${cls.tab}`, "Courbes", WINDOWS, "mwin", win), sortGroup, search);
  const grid = el("div", "asset-grid market-grid");
  sec.append(head, tools, grid);
  $("#pages").append(sec);
  const page = { cls, sec, grid, rows: [], filter: "all" };
  PAGES.set(cls.tab, page);
  $$("[data-mfilter]", sec).forEach((b) => b.addEventListener("click", () => {
    page.filter = b.dataset.mfilter;
    $$("[data-mfilter]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    withLoader(`Sélection : ${b.textContent}…`, async () => arrange(page));
  }));
  $$("[data-mwin]", sec).forEach((b) => b.addEventListener("click", () => {
    win = b.dataset.mwin;
    prefs.set("window:market", win);
    $$("[data-mwin]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mwin === win)));
    withLoader(`Courbes : ${b.textContent}…`, () => render(page));
  }));
  sort.addEventListener("change", () => withLoader(`Tri : ${sort.selectedOptions[0].textContent}…`, async () => arrange(page)));
  search.addEventListener("input", () => arrange(page));
  register(cls.tab, () => render(page), 300);
}

function arrange(page) {
  const q = $(".market-search", page.sec).value.trim().toLowerCase(), sort = $(".market-sort", page.sec).value;
  const key = { change, gap: (r) => r.gap_pct, mom: (r) => r.mom }[sort];
  const rows = page.rows.slice().sort((a, b) => (key ? (key(b) ?? -1e9) - (key(a) ?? -1e9) : a.name.localeCompare(b.name, "fr")));
  rows.forEach((r) => {
    const card = page.grid.querySelector(`[data-code="${r.code}"]`);
    if (!card) return;
    const f = page.filter;
    const show = (f === "all" || (f === "trend" && r.trend) || (f === "signal" && r.signal)
      || (f === "held" && r.positions.length) || (f === "binance" && r.binance))
      && (!q || r.name.toLowerCase().includes(q) || short(r).toLowerCase().includes(q) || String(r.symbol).toLowerCase().includes(q));
    card.hidden = !show;
    page.grid.append(card);
  });
}

// Ce que la règle du bot dit du marché, en une phrase.
function reading(r) {
  if (r.close == null) return "Historique illisible.";
  if (r.sma == null) return "Coté depuis peu : la règle attend 150 séances pour sa moyenne et 250 pour acheter.";
  if (r.signal) return `Au-dessus de son plus haut de 30 séances, momentum positif : la règle achèterait aujourd'hui (stop initial ${fpx(r.stop)}).`;
  if (!r.trend) return r.gap_pct == null ? "Historique trop court pour la moyenne de 150 séances." : `Sous sa moyenne de 150 séances (${fpct(r.gap_pct)}) : la règle n'achète pas.`;
  return r.breakout ? `Tendance haussière ; achat si le cours dépasse ${fpx(r.breakout)} (${fpct((r.breakout / r.close - 1) * 100)}).` : "Tendance haussière.";
}

function makeCard(page, r, k) {
  const card = el("article", "card asset market");
  card.dataset.code = r.code;
  card.style.animationDelay = (k * 25) + "ms";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${r.name} : ouvrir le graphique`);
  const top = el("div", "row");
  top.append(el("span", "tk", short(r)), el("span", "chg"));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 200 42");
  svg.setAttribute("preserveAspectRatio", "none");
  const bottom = el("div", "row");
  bottom.append(el("span", "tags"), el("span", "sub vol"));
  card.append(top, el("span", "name", r.name), el("span", "px"), svg, el("p", "why"), bottom);
  const open = () => openDetail({ kind: "market", code: r.code, title: r.name, label: short(r) });
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  page.grid.append(card);
  return card;
}

async function render(page) {
  const d = await api(`/api/markets?class=${page.cls.tab}&window=${win}`);
  page.rows = d.rows;
  const known = new Set(d.rows.map((r) => r.code));
  $$(".asset", page.grid).forEach((c) => { if (!known.has(c.dataset.code)) c.remove(); });
  d.rows.forEach((r, k) => {
    const card = page.grid.querySelector(`[data-code="${r.code}"]`) || makeCard(page, r, k);
    card.querySelector(".px").textContent = fpx(r.close);
    const ch = change(r), chg = card.querySelector(".chg");
    chg.textContent = ch == null ? "–" : `${ch >= 0 ? "▲" : "▼"} ${fpct(ch)}`;
    chg.className = "chg " + ((ch || 0) >= 0 ? "up" : "down");
    chg.title = `Variation sur ${WINDOWS.find((w) => w[0] === win)[1]}`;
    const svg = card.querySelector(".spark");
    if (svg.dataset.win !== win) {
      delete svg.dataset.drawn;                    // nouvelle durée : la courbe se retrace
      svg.classList.remove("reveal");
      void svg.getBoundingClientRect();
      svg.dataset.win = win;
    }
    fillSpark(svg, r.spark || []);
    card.querySelector(".tags").replaceChildren(...[
      el("span", "tag " + (r.trend ? "up" : "down"), r.trend ? "Tendance haussière" : "Tendance baissière"),
      r.signal && el("span", "tag watch", "Signal d'achat"),
      r.positions.length && el("span", "tag held", "En portefeuille fictif"),
      r.binance && el("span", "tag muted", `Sur Binance : ${token(r)}`)].filter(Boolean));
    card.querySelector(".why").textContent = reading(r);
    card.querySelector(".vol").textContent = r.date ? `Séance du ${fdate(r.date)}` : "";
  });
  if (!d.rows.length) {
    const empty = page.grid.querySelector(".market-empty") || el("p", "card market-empty", "");
    empty.textContent = d.error ? `Lecture ratée : ${d.error} (nouvel essai dans 6 h).` : "Pas encore lus : première lecture avec le noyau de savoir (une fois par jour).";
    page.grid.replaceChildren(empty);
  } else $$(".market-empty", page.grid).forEach((n) => n.remove());
  const count = (fn) => d.rows.filter(fn).length;
  const kpi = { count: d.rows.length, trend: count((r) => r.trend), signal: count((r) => r.signal), held: count((r) => r.positions.length) };
  $$("[data-kpi]", page.sec).forEach((box) => { box.querySelector("strong").textContent = String(kpi[box.dataset.kpi]); });
  const last = d.rows.map((r) => r.date).filter(Boolean).sort().pop();
  $(".market-sub", page.sec).textContent = (last ? `séance du ${fdate(last)}` : "pas encore lus")
    + (d.fetched_at ? ` · lus le ${fdate(d.fetched_at)}` : "") + (d.missing.length ? ` · illisibles : ${d.missing.join(", ")}` : "");
  arrange(page);
  if (d.error && d.rows.length) toast(`Marchés : dernière lecture ratée (${d.error}), derniers cours gardés.`, "err");
}

CLASSES.forEach(buildPage);
