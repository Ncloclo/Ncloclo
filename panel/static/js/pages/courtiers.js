// Panneau TrendGuard, page « Courtiers » (trendguard/courtiers.py) : les
// comptes de démonstration du bot (MetaTrader 5, Alpaca, testnets crypto),
// le plan miroir à blanc, les cours des cryptos du bot comparés entre
// plateformes et l'annuaire des courtiers. Démonstration seulement : un
// compte réel est refusé, aucun ordre n'est envoyé.

import { $, $$, api, el, fpct, fpx, nf, table } from "../core.js";
import { register } from "../navigation.js";

// État d'un compte : [libellé, couleur de l'étiquette]
const STATES = {
  absent: ["non branché", "muted"], attente: ["pas encore lu", "watch"], demo: ["démo branchée", "up"],
  refuse: ["refusé : compte réel", "down"], erreur: ["erreur", "warn"], ferme: ["terminal fermé", "warn"],
  indisponible: ["indisponible", "warn"],
};
const ACCOUNTS = [
  ["mt5", "MetaTrader 5", "Devises, or, pétrole, indices : le compte démo d'un courtier, par le terminal Windows de ce PC.",
    "Installez MetaTrader 5 (site de MetaTrader 5 ou d'un courtier de l'annuaire), ouvrez-y un compte démo, puis tapez :",
    "python trendguard_bot.py courtiers set-key mt5"],
  ["alpaca", "Alpaca (compte papier)", "Actions et fonds cotés américains, en argent fictif.",
    "Créez un compte papier sur alpaca.markets (une adresse e-mail suffit), prenez ses clés « Paper Trading », puis tapez :",
    "python trendguard_bot.py courtiers set-key alpaca"],
];
const KINDS = [["all", "Tous"], ["Forex et CFD (MetaTrader 5)", "Forex et CFD (MT5)"], ["Actions américaines", "Actions US"],
  ["Cryptos", "Cryptos"], ["BRVM (Abidjan)", "BRVM"]];
let kind = "all", demoOnly = false, gapsOnly = false, DATA = null;

const sec = el("section", "page brokers-page");
sec.id = "page-brokers";
sec.dataset.title = "Courtiers";
const money = (v, cur) => (v == null ? "–" : `${nf(2).format(v)} ${cur || ""}`.trim());
const when = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
};

function seg(items, attr, current, label) {
  const group = el("div", "tool-group"), lab = el("span", "tool-label", label), box = el("div", "seg");
  lab.id = `bl-${attr}`;
  box.setAttribute("role", "group");
  box.setAttribute("aria-labelledby", lab.id);
  items.forEach(([key, text]) => {
    const b = el("button", "", text);
    b.type = "button";
    b.dataset[attr] = key;
    b.setAttribute("aria-pressed", String(key === current));
    box.append(b);
  });
  group.append(lab, box);
  return group;
}

function card(title, sub, ...body) {
  const c = el("div", "card"), head = el("div", "card-head");
  head.append(el("h2", "", title), el("span", "sub " + (sub || ""), ""));
  c.append(head, ...body);
  return c;
}

function wrapped(id) {
  const t = el("table");
  t.id = id;
  const w = el("div", "table-wrap");
  w.append(t);
  return w;
}

(function build() {
  const kpis = el("div", "kpis market-kpis");
  [["accounts", "Comptes démo branchés"], ["platforms", "Plateformes lues"], ["gap", "Écart médian de Binance"],
    ["directory", "Annuaire"]].forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.bk = key;
    box.append(el("span", "label", label), el("strong", "num", "—"), el("span", "sub", ""));
    kpis.append(box);
  });
  const head = card("Courtiers et plateformes", "br-sub",
    el("p", "hint", "Où le bot pourrait jouer sa règle, et comment il s'y branche : en démonstration seulement. Un compte réel est refusé dès qu'il est reconnu, aucun ordre n'est envoyé, et vos identifiants restent dans le fichier .env de ce PC. Les cours des autres plateformes sont lus toutes les six heures, sans compte : ils servent au savoir du bot, aucune décision n'en dépend."),
    kpis);
  const accounts = card("Vos comptes de démonstration", "", el("div", "broker-grid"));
  accounts.querySelector(".card-head .sub").textContent = "lecture seule, chaque heure";
  const mirror = card("Plan miroir, à blanc", "br-mirror-sub",
    el("p", "hint", "Les positions du portefeuille fictif hors cryptos (la règle du bot sur l'or, les devises, les indices et les obligations), recopiées au même poids sur chaque compte de démonstration branché. Rien n'est envoyé : les ordres de démonstration attendront un premier essai réussi sur votre compte."),
    wrapped("br-mirror"));
  const tools = el("div", "toolbar");
  tools.append(seg([["all", "Toutes"], ["gaps", "Écarts notables"]], "brgap", "all", "Afficher"));
  const prices = card("Cours des cryptos du bot sur d'autres plateformes", "br-prices-sub", tools, wrapped("br-prices"),
    el("p", "sub br-platforms"));
  const dtools = el("div", "toolbar");
  const demo = el("button", "chip br-demo", "Avec démo ou testnet");
  demo.type = "button";
  demo.setAttribute("aria-pressed", "false");
  const search = el("input", "br-search");
  search.type = "search";
  search.placeholder = "Rechercher (Exness, Bybit…)";
  search.setAttribute("aria-label", "Rechercher un courtier ou une plateforme");
  dtools.append(seg(KINDS, "brkind", kind, "Type"), demo, search);
  const dir = card("Annuaire des courtiers", "br-count", dtools, wrapped("br-directory"),
    el("p", "hint", "Disponibilité en Côte d'Ivoire : rien n'est affirmé sans source. Vérifiez-la sur le site de chaque courtier avant d'ouvrir un compte. Frais des plateformes crypto : barème de base indiqué par la bibliothèque CCXT."));
  sec.append(head, accounts, mirror, prices, dir);
  $("#pages").append(sec);
  $$("[data-brgap]", sec).forEach((b) => b.addEventListener("click", () => {
    gapsOnly = b.dataset.brgap === "gaps";
    $$("[data-brgap]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    drawPrices();
  }));
  $$("[data-brkind]", sec).forEach((b) => b.addEventListener("click", () => {
    kind = b.dataset.brkind;
    $$("[data-brkind]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    drawDirectory();
  }));
  demo.addEventListener("click", () => {
    demoOnly = !demoOnly;
    demo.setAttribute("aria-pressed", String(demoOnly));
    drawDirectory();
  });
  search.addEventListener("input", drawDirectory);
})();

function line(dl, label, value) {
  if (value == null || value === "") return;
  dl.append(el("dt", "", label), el("dd", "", value));
}

function accountCard(key, title, what, how, cmd, s) {
  const box = el("div", "broker-acc");
  box.dataset.acc = key;
  const [label, tone] = STATES[s.state] || [s.state, "muted"];
  const top = el("div", "acc-head");
  top.append(el("strong", "", title), el("span", `tag ${tone} acc-state`, label));
  box.append(top, el("p", "sub", what));
  const a = s.account || {}, dl = el("dl", "acc-lines");
  if (key === "mt5") {
    line(dl, "Courtier", a.company);
    line(dl, "Serveur", a.server);
    line(dl, "Compte", a.login);
    line(dl, "Levier", a.leverage ? `1:${a.leverage}` : null);
  } else {
    line(dl, "Compte", a.number);
    line(dl, "Wall Street", s.state === "demo" ? (s.market_open ? "séance ouverte" : "séance fermée") : null);
  }
  line(dl, "Capital", a.equity != null ? money(a.equity, a.currency) : null);
  line(dl, "Positions", s.positions ? String(s.positions.length) : null);
  line(dl, "Lu le", s.seen_at || s.checked_at ? when(s.seen_at || s.checked_at) : null);
  if (dl.children.length) box.append(dl);
  box.append(el("p", "acc-msg", s.message || ""));
  if (s.state !== "demo") {
    const p = el("p", "acc-how", how + " ");
    p.append(el("code", "", cmd));
    box.append(p);
  }
  return box;
}

function testnetCard(list, names) {
  const box = el("div", "broker-acc");
  box.dataset.acc = "testnets";
  const top = el("div", "acc-head"), on = list.filter((t) => t.state === "demo").length;
  top.append(el("strong", "", "Testnets crypto"),
    el("span", `tag ${on ? "up" : "muted"} acc-state`, on ? `${on} branché${on > 1 ? "s" : ""}` : "aucun"));
  box.append(top, el("p", "sub", `Plateformes crypto en argent fictif : ${names.join(", ")}. Binance : son testnet est celui du bot lui-même.`));
  list.forEach((t) => {
    const [label, tone] = STATES[t.state] || [t.state, "muted"];
    const row = el("div", "acc-net");
    const bal = Object.entries(t.balances || {}).slice(0, 4).map(([c, v]) => `${fpx(v)} ${c}`).join(" · ");
    row.append(el("strong", "", t.name), el("span", `tag ${tone}`, label), el("span", "sub", bal || t.message || ""));
    box.append(row);
  });
  if (!on) {
    const p = el("p", "acc-how", "Créez un compte de test (testnet.bybit.com, par exemple) et sa clé API, puis tapez : ");
    p.append(el("code", "", "python trendguard_bot.py courtiers set-key bybit"));
    box.append(p);
  }
  return box;
}

function drawAccounts(d) {
  const grid = $(".broker-grid", sec);
  grid.replaceChildren(...ACCOUNTS.map(([key, title, what, how, cmd]) => accountCard(key, title, what, how, cmd, d.accounts[key])),
    testnetCard(d.accounts.testnets || [], d.testnets.map((t) => t.name)));
}

function drawMirror(d) {
  const m = d.mirror;
  const rows = m.rows.map((r) => {
    const mt = r.mt5.lots ? `${r.mt5.symbol} · ${nf(2).format(r.mt5.lots)} lot` : r.mt5.symbol ? `${r.mt5.symbol} : ${r.mt5.note}` : r.mt5.note;
    const al = r.alpaca.qty ? `${r.alpaca.symbol} · ${nf(4).format(r.alpaca.qty)}` : r.alpaca.symbol ? `${r.alpaca.symbol} : ${r.alpaca.note}` : r.alpaca.note;
    return { cells: [r.name, r.weight_pct != null ? fpct(r.weight_pct, 1).replace("+", "") : "–", fpx(r.stop), mt, al] };
  });
  table($("#br-mirror"), ["Marché", "Poids visé", "Stop de la règle", "MetaTrader 5 (démo)", "Alpaca (papier)"], rows, null,
    "Le portefeuille fictif hors cryptos ne tient aucune position aujourd'hui.", false);
  $(".br-mirror-sub", sec).textContent = `${m.rows.length} position${m.rows.length > 1 ? "s" : ""} · rien n'est envoyé`;
}

function drawPrices() {
  const p = DATA && DATA.prices;
  const sub = $(".br-prices-sub", sec), plat = $(".br-platforms", sec);
  if (!p || !p.rows) {
    table($("#br-prices"), ["Crypto"], [], null, "Pas encore lus : première lecture dans les six heures (ou python trendguard_bot.py courtiers cours).", false);
    sub.textContent = p && p.error ? `lecture ratée : ${p.error}` : "pas encore lus";
    plat.textContent = "";
    return;
  }
  const notable = new Set(p.notable || []);
  const rows = p.rows.filter((r) => !gapsOnly || notable.has(r.asset))
    .sort((a, b) => Math.abs(b.gap_pct || 0) - Math.abs(a.gap_pct || 0))
    .map((r) => ({
      cells: [r.asset.toUpperCase(), fpx(r.binance), fpx(r.median), fpct(r.gap_pct), String(r.count),
        r.widest ? `${r.widest.name} ${fpct(r.widest.gap_pct)}` : "–", r.binance_share != null ? `${nf(0).format(r.binance_share)} %` : "–"],
      cls: [null, null, null, notable.has(r.asset) ? "down" : null, null, null, null],
    }));
  table($("#br-prices"), ["Crypto", "Binance (USDT)", "Médiane des autres", "Écart de Binance", "Plateformes",
    "Écart le plus grand", "Part de Binance dans le volume"], rows, null, "Aucun écart notable : Binance suit les autres plateformes.", true);
  const ok = p.platforms.filter((x) => x.ok && x.id !== "binance").length;
  sub.textContent = `lus le ${when(p.fetched_at)} · ${ok} plateformes face à Binance` + (p.error ? ` · dernière lecture ratée : ${p.error}` : "");
  plat.textContent = "Plateformes : " + p.platforms.map((x) => (x.ok ? `${x.name} (${x.listed})` : `${x.name} : ${x.error}`)).join(" · ")
    + ". Écart de Binance : son cours face à la médiane des autres ; écart le plus grand : une plateforme face à Binance. Volumes déclarés par les plateformes.";
}

function note(tb, text) {
  const tr = el("tr"), td = el("td", "empty", text);
  td.colSpan = 5;
  tr.append(td);
  tb.append(tr);
}

function drawDirectory() {
  if (!DATA) return;
  const q = $(".br-search", sec).value.trim().toLowerCase();
  let rows = DATA.directory.filter((r) => (kind === "all" || r.kind === kind)
    && (!demoOnly || (r.demo && r.demo !== "non"))
    && (!q || r.name.toLowerCase().includes(q) || (r.id || "").includes(q)));
  // Vue d'ensemble : parmi les plateformes crypto, celles qui servent au bot
  // (cours comparés, testnet branchable) ; les autres par le filtre ou la recherche.
  const overview = kind === "all" && !q && !demoOnly;
  const useful = (r) => r.kind !== "Cryptos" || r.compared || r.branchable;
  const hidden = overview ? rows.filter((r) => !useful(r)).length : 0;
  if (overview) rows = rows.filter(useful);
  const t = $("#br-directory"), thead = el("thead"), hr = el("tr"), tb = el("tbody");
  ["Courtier", "Compte démo", "Branchement du bot", "Côte d'Ivoire", "Frais"].forEach((h) => {
    const th = el("th", "", h);
    th.scope = "col";
    hr.append(th);
  });
  thead.append(hr);
  if (!rows.length) note(tb, "Aucun courtier ne correspond.");
  rows.forEach((r) => {
    const tr = el("tr"), name = el("td", "br-name");
    tr.dataset.kind = r.kind;
    const what = el("span", "sub", `${r.kind === "Cryptos" ? "" : r.kind + " · "}${r.markets}`);
    if (/^https?:\/\//.test(r.site || "")) {
      const a = el("a", "", new URL(r.site).hostname.replace(/^www\./, ""));
      a.href = r.site;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      what.append(" · ", a);
    }
    name.append(el("strong", "", r.name), what);
    tr.append(name);
    [r.demo, r.connection + (r.note ? ` (${r.note})` : ""), r.availability, r.fees].forEach((c) => tr.append(el("td", "", c || "")));
    tb.append(tr);
  });
  if (hidden) note(tb, `Et ${hidden} autres plateformes crypto : choisissez « Cryptos » ou cherchez-en une.`);
  t.replaceChildren(thead, tb);
  $(".br-count", sec).textContent = `${rows.length} sur ${DATA.directory.length}`;
}

async function render() {
  const d = await api("/api/brokers");
  DATA = d;
  const on = ["mt5", "alpaca"].filter((k) => d.accounts[k].state === "demo").length
    + (d.accounts.testnets || []).filter((t) => t.state === "demo").length;
  const p = d.prices || {}, ok = (p.platforms || []).filter((x) => x.ok && x.id !== "binance").length;
  const kpi = (key, value, sub, cls = "") => {
    const box = $(`[data-bk="${key}"]`, sec);
    box.querySelector("strong").textContent = value;
    box.querySelector("strong").className = "num " + cls;
    box.querySelector(".sub").textContent = sub;
  };
  kpi("accounts", String(on), on ? "lecture seule, aucun ordre" : "aucun : voir ci-dessous");
  kpi("platforms", p.rows ? `${ok} sur ${d.compare.length}` : "—", p.rows ? "cours publics, sans compte" : "pas encore lus");
  kpi("gap", p.median_gap_pct != null ? fpct(p.median_gap_pct, 2).replace("+", "") : "—",
    p.rows ? ((p.notable || []).length ? `écarts notables : ${p.notable.map((a) => a.toUpperCase()).join(", ")}` : "aucun écart notable") : "",
    (p.notable || []).length ? "down" : "");
  const crypto = d.directory.filter((r) => r.kind === "Cryptos");
  kpi("directory", String(d.directory.length), `dont ${crypto.length} plateformes crypto, ${crypto.filter((r) => r.testnet).length} avec testnet`);
  $(".br-sub", sec).textContent = "démonstration seulement · aucun ordre réel";
  drawAccounts(d);
  drawMirror(d);
  drawPrices();
  drawDirectory();
}

register("brokers", render, 300);
