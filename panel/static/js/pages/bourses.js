// Panneau TrendGuard, page « Bourses du monde » (trendguard/bourses.py) : la
// structure de cotation de chaque grande place, comme la BRVM pour l'UEMOA.
// En séance ou non (heure locale et heure d'Abidjan), indices et tendance
// pour la règle du bot, régulateur, accès depuis la Côte d'Ivoire, ce que le
// bot en lit, et la règle mesurée sur l'indice principal. Information
// seulement : aucun ordre.

import { $, $$, api, el, fpct, nf } from "../core.js";
import { fillSpark } from "../charts.js";
import { register } from "../navigation.js";

const REGIONS = [["all", "Toutes"], ["Afrique", "Afrique"], ["Amériques", "Amériques"], ["Europe", "Europe"],
  ["Asie et Moyen-Orient", "Asie et Moyen-Orient"], ["Océanie", "Océanie"]];
let region = "all", openOnly = false, DATA = null;
const flag = (iso) => iso.toUpperCase().replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)));
const value = (v) => (v == null ? "–" : nf(v >= 10 ? 2 : 4).format(v));
const day = (iso) => (iso ? String(iso).slice(0, 10).split("-").reverse().join("/") : "–");   // 2026-11-25 → 25/11/2026

const sec = el("section", "page exchanges-page");
sec.id = "page-exchanges";
sec.dataset.title = "Bourses du monde";

(function build() {
  const head = el("div", "card"), top = el("div", "card-head"), kpis = el("div", "kpis market-kpis");
  top.append(el("h2", "", "Bourses du monde"), el("span", "sub ex-sub"));
  [["count", "Places suivies"], ["open", "En séance maintenant"], ["official", "Pages officielles lues"],
    ["study", "La règle tient sur"]].forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.xkpi = key;
    box.append(el("span", "label", label), el("strong", "num", "—"), el("span", "sub", ""));
    kpis.append(box);
  });
  head.append(top, el("p", "hint", "La structure de cotation de chaque grande place, comme la BRVM pour l'UEMOA : ses heures de séance (heure locale et heure d'Abidjan), ses indices phares et leur tendance pour la règle du bot, son régulateur, l'accès depuis la Côte d'Ivoire. Les indices sont lus chaque jour chez Yahoo Finance ; le Nigeria, le Kenya, le Maroc et l'Afrique centrale le sont sur leurs pages officielles, après chaque séance, comme la BRVM. Information seulement : aucun ordre."), kpis);
  const tools = el("div", "toolbar"), group = el("div", "tool-group"), lab = el("span", "tool-label", "Région"), seg = el("div", "seg");
  lab.id = "xr-label";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-labelledby", lab.id);
  REGIONS.forEach(([key, text]) => {
    const b = el("button", "", text);
    b.type = "button";
    b.dataset.xregion = key;
    b.setAttribute("aria-pressed", String(key === region));
    seg.append(b);
  });
  group.append(lab, seg);
  const live = el("button", "chip ex-open", "En séance maintenant");
  live.type = "button";
  live.setAttribute("aria-pressed", "false");
  const search = el("input", "ex-search");
  search.type = "search";
  search.placeholder = "Rechercher (Nigeria, Tokyo, SEC…)";
  search.setAttribute("aria-label", "Rechercher une bourse, un pays ou un régulateur");
  tools.append(group, live, search);
  const list = el("div", "card"), listHead = el("div", "card-head");
  listHead.append(el("h2", "", "Les places de cotation"), el("span", "sub ex-count"));
  list.append(listHead, tools, el("div", "exchange-grid"));
  const guide = el("div", "card"), gHead = el("div", "card-head");
  gHead.append(el("h2", "", "Investir depuis la Côte d'Ivoire"), el("span", "sub", "à vérifier avant d'agir"));
  guide.append(gHead, el("ul", "ex-guide"));
  sec.append(head, list, guide);
  $("#pages").append(sec);
  $$("[data-xregion]", sec).forEach((b) => b.addEventListener("click", () => {
    region = b.dataset.xregion;
    $$("[data-xregion]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  }));
  live.addEventListener("click", () => {
    openOnly = !openOnly;
    live.setAttribute("aria-pressed", String(openOnly));
    draw();
  });
  search.addEventListener("input", draw);
})();

function line(dl, label, text) {
  if (!text) return;
  dl.append(el("dt", "", label), el("dd", "", text));
}

function studyText(s) {
  if (!s) return "";
  const p = (m) => `${fpct(m.rule_cagr, 1)} par an, baisse max ${nf(1).format(m.rule_dd)} % (garder : ${fpct(m.hold_cagr, 1)}, ${nf(1).format(m.hold_dd)} %)`;
  return `${s.holds ? "elle tient" : "elle ne tient pas"} face à l'achat-conservation · 2018-2022 : ${p(s.design)} · depuis 2023 : ${p(s.verify)}`;
}

function card(r) {
  const box = el("article", "exchange");
  box.dataset.ex = r.id;
  const top = el("div", "ex-head"), st = r.status || {};
  const state = st.open ? ["en séance", "up"] : st.open === false ? ["fermée", "muted"] : ["heures non lues", "warn"];
  const title = el("h3", "", `${r.countries.slice(0, 4).map(flag).join(" ")} ${r.name}`);
  top.append(title, el("span", `tag ${state[1]} ex-state`, state[0]));
  box.append(top, el("p", "sub", [r.city, r.currency, r.hours ? `${r.hours} (heure locale, ${r.days_text})` : "", st.next].filter(Boolean).join(" · ")));
  const idx = el("ul", "ex-indices");
  r.indices.forEach((i) => {
    const li = el("li");
    li.append(el("span", "ex-iname", i.name), el("span", "num", value(i.close)));
    const ch = el("span", "num " + (i.change > 0 ? "up" : i.change < 0 ? "down" : ""), i.change == null ? "" : fpct(i.change));
    li.append(ch);
    if (i.trend != null) li.append(el("span", `tag ${i.trend ? "up" : "down"}`, i.trend ? "tendance haussière" : "tendance baissière"));
    else if (i.close == null) li.append(el("span", "tag muted", "non lu"));
    if (i.spark && i.spark.length > 1) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "spark");
      svg.setAttribute("viewBox", "0 0 200 42");
      svg.setAttribute("preserveAspectRatio", "none");
      li.append(svg);
      fillSpark(svg, i.spark);
    }
    idx.append(li);
  });
  box.append(idx);
  const dl = el("dl", "ex-lines");
  line(dl, "Régulateur", r.regulator);
  line(dl, "Depuis la Côte d'Ivoire", r.access);
  const o = r.official_state;
  line(dl, "Le bot lit", r.reads + (o ? ` (séance du ${day(o.session)},${o.stocks} titres, historique de ${o.sessions} séance${o.sessions > 1 ? "s" : ""}${o.error ? ` ; lecture ratée : ${o.error}` : ""})` : ""));
  if (o && o.movers && o.movers.length) line(dl, "Plus forts mouvements", o.movers.map((m) => `${m.name} ${fpct(m.change)}`).join(" · "));
  line(dl, "Règle du bot", studyText(r.study));
  line(dl, "Horaires", r.hours_note);
  line(dl, "À savoir", r.note);
  box.append(dl);
  if (/^https:\/\//.test(r.site || "")) {
    const a = el("a", "ex-site", new URL(r.site).hostname.replace(/^www\./, ""));
    a.href = r.site;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    box.append(a);
  }
  return box;
}

function draw() {
  if (!DATA) return;
  const q = $(".ex-search", sec).value.trim().toLowerCase();
  const rows = DATA.rows.filter((r) => (region === "all" || r.region === region) && (!openOnly || r.status.open)
    && (!q || [r.name, r.city, r.regulator, r.countries.join(" "), r.indices.map((i) => i.name).join(" ")].join(" ").toLowerCase().includes(q)));
  const grid = $(".exchange-grid", sec);
  grid.replaceChildren(...rows.map(card));
  if (!rows.length) grid.append(el("p", "empty", "Aucune bourse ne correspond."));
  $(".ex-count", sec).textContent = `${rows.length} sur ${DATA.rows.length}`;
}

async function render() {
  const d = await api("/api/exchanges");
  DATA = d;
  const open = d.rows.filter((r) => r.status.open);
  const studied = d.rows.filter((r) => r.study);
  const kpi = (key, v, sub) => {
    const box = $(`[data-xkpi="${key}"]`, sec);
    box.querySelector("strong").textContent = v;
    box.querySelector(".sub").textContent = sub;
  };
  kpi("count", String(d.rows.length), `${d.regions.length} régions`);
  kpi("open", String(open.length), open.length ? open.slice(0, 3).map((r) => r.city.split(",")[0]).join(", ") : "aucune en ce moment");
  const off = d.rows.filter((r) => r.official_state);
  kpi("official", String(off.length), off.filter((r) => r.official_state.session).length + " lues au moins une fois");
  kpi("study", studied.length ? `${studied.filter((r) => r.study.holds).length} sur ${studied.length}` : "—", "indices principaux, depuis 2018");
  $(".ex-sub", sec).textContent = `heure d'Abidjan : ${new Date(d.now).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}`;
  const ul = $(".ex-guide", sec);
  ul.replaceChildren(...d.guide.map((g) => {
    const li = el("li");
    li.append(el("strong", "", g.topic + " : "), document.createTextNode(g.text + " "), el("span", "tag muted", g.source));
    return li;
  }));
  draw();
}

register("exchanges", render, 60);
