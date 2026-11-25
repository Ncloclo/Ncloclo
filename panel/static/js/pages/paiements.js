// Panneau TrendGuard, page « Paiement » (trendguard/paiements.py) : les moyens
// de payer et d'être payé dans le pays de l'appareil, reconnu sur place
// (geo.js : fuseau horaire et langue ; position si vous l'autorisez, pour
// trouver votre ville) : paiement mobile, cartes de débit et prépayées,
// passerelles de paiement en ligne, banque et virements, envoi d'argent, et le
// passage aux USDT du bot. Information seulement : le bot n'encaisse,
// n'envoie ni ne garde aucun paiement, et ne demande jamais un numéro de
// carte ni un code.

import { $, api, el, lang, toast } from "../core.js";
import { detect, forget, geoData, locate, officialOrder, positionPermission, remember } from "../geo.js";
import { autoLanguage, decide } from "../i18n.js";
import { languageControl, languageName } from "../langue.js";
import { register } from "../navigation.js";

const ICON = { mobile: "📱", carte: "💳", en_ligne: "🌐", virement: "🏦", transfert: "💸", crypto: "🪙" };
const SCOPE = { pays: "dans votre pays", zone: "dans votre zone", monde: "partout" };
const HOW = {
  position: "d'après votre position", fuseau: "d'après le fuseau horaire de l'appareil",
  langue: "d'après la langue de l'appareil", choix: "choisi sur cet appareil", profil: "pays du profil du bot",
};
let G = null, where = null, DATA = null, autoTried = false;

const regionName = (iso) => {
  try { return new Intl.DisplayNames([lang()], { type: "region" }).of(iso) || iso; } catch { return iso; }
};
const flag = (iso) => iso.toUpperCase().replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)));
// Un texte des données : en français, sinon en anglais (que l'appareil
// traduit s'il sait le faire, data-lang).
function dataText(tag, cls, fr, en) {
  const node = el(tag, cls, lang() === "fr" ? fr : en);
  if (lang() !== "fr") node.dataset.lang = "en";
  return node;
}

const sec = el("section", "page payments-page");
sec.id = "page-payments";
sec.dataset.title = "Paiement";

(function build() {
  const head = el("div", "card"), top = el("div", "card-head");
  top.append(el("h2", "", "Payer dans votre pays"), el("span", "sub pay-sub"));
  const hint = el("p", "hint", "Les moyens de payer, d'être payé et d'envoyer de l'argent dans votre pays et votre zone : paiement mobile, cartes bancaires de débit ou prépayées, paiement en ligne, banque et virements. Le pays est reconnu sur cet appareil (fuseau horaire et langue) ; « Me localiser » trouve votre ville si vous l'autorisez, la position n'est ni gardée ni envoyée à un service extérieur. Information seulement : le bot n'encaisse, n'envoie ni ne garde aucun paiement.");
  const tools = el("div", "toolbar"), group = el("div", "tool-group"), lab = el("label", "tool-label", "Votre pays");
  const pick = el("select", "pay-country");
  pick.id = "pay-country";
  lab.htmlFor = pick.id;
  pick.setAttribute("translate", "no");
  group.append(lab, pick);
  const me = el("button", "btn small pay-locate", "📍 Me localiser");
  me.type = "button";
  const back = el("button", "btn small ghost pay-auto", "Pays de l'appareil");
  back.type = "button";
  back.title = "Oublier le pays choisi : revenir à celui que l'appareil indique";
  tools.append(group, me, back);
  const kpis = el("div", "kpis market-kpis");
  [["count", "Moyens recensés"], ["mobile", "Paiement mobile"], ["lang", "Langue officielle"], ["money", "Monnaie"]].forEach(([key, label]) => {
    const box = el("div", "kpi");
    box.dataset.pkpi = key;
    box.append(el("span", "label", label), el("strong", "num", "—"), el("span", "sub", ""));
    kpis.append(box);
  });
  head.append(top, hint, tools, el("p", "sub pay-where"), kpis);
  const langCard = el("div", "card pay-lang");
  langCard.append(el("div", "card-head"), el("p", "hint", "La langue du panneau et de Rachelle suit la langue officielle du pays de l'appareil. Vous pouvez en choisir une autre."));
  langCard.firstChild.append(el("h2", "", "Langue"));
  const list = el("div", "pay-kinds");
  const safety = el("div", "card"), sHead = el("div", "card-head");
  sHead.append(el("h2", "", "Payer en sécurité"));
  safety.append(sHead, el("ul", "pay-safety"));
  sec.append(head, langCard, list, safety);
  $("#pages").append(sec);

  pick.addEventListener("change", () => { remember(pick.value, "choix"); choose(); });
  back.addEventListener("click", () => { forget(); choose(); });
  me.addEventListener("click", () => findMe(true));
})();

// Le pays change : nouvelles données, et la langue du pays si elle est en
// automatique (la page peut alors se recharger dans la nouvelle langue).
async function choose() {
  where = detect(G);
  await draw();
  await autoLanguage(await decide(officialOrder(G, where.country)));
}

async function findMe(asked) {
  const btn = $(".pay-locate", sec);
  btn.disabled = true;
  try {
    const found = await locate();
    if (!found.near) {
      toast(`Position loin de toute ville connue (${found.km} km de ${found.city}) : pays inchangé.`, "err");
      return;
    }
    await choose();
    if (asked) toast(`${found.city}, ${regionName(found.country)}`);
  } catch (e) {
    if (asked) toast(`Position : ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

function whereLine() {
  const line = $(".pay-where", sec);
  const place = el("strong", "", (where.city ? where.city + ", " : "") + regionName(where.country));
  place.setAttribute("translate", "no");
  const how = el("span", "", HOW[where.how] || "");
  line.replaceChildren(flag(where.country), " ", place, " · ", how);
  if (where.how === "fuseau" && where.zone) {
    const z = el("span", "", ` (${where.zone})`);
    z.setAttribute("translate", "no");
    line.append(z);
  }
}

function methodCard(m) {
  const li = el("li", "pay-method");
  const name = el("strong", "", lang() === "fr" ? m.name : m.name_en);
  name.setAttribute("translate", "no");                  // noms de services et de marques
  const scope = el("span", "tag " + (m.scope === "pays" ? "up" : "muted"), SCOPE[m.scope]);
  const top = el("div", "pay-mhead");
  top.append(name, scope);
  li.append(top, dataText("p", "pay-what", m.fr, m.en));
  if (m.care_fr) li.append(dataText("p", "pay-care", "⚠ " + m.care_fr, "⚠ " + m.care_en));
  return li;
}

async function draw() {
  DATA = await api(`/api/payments?country=${encodeURIComponent(where.country)}`);
  const pick = $(".pay-country", sec);
  if (!pick.options.length) {
    G.countries.map((c) => [c.iso, regionName(c.iso)]).sort((a, b) => a[1].localeCompare(b[1], lang()))
      .forEach(([iso, name]) => { const o = el("option", "", `${flag(iso)} ${name}`); o.value = iso; pick.append(o); });
  }
  pick.value = where.country;
  whereLine();
  $(".pay-sub", sec).textContent = DATA.zone ? `zone ${DATA.zone}` : "";
  const official = G.official[where.country] || [];
  const kpi = (key, v, sub, keepText = false) => {
    const box = $(`[data-pkpi="${key}"]`, sec), strong = box.querySelector("strong");
    strong.textContent = v;
    if (keepText) strong.setAttribute("translate", "no");
    box.querySelector(".sub").textContent = sub;
  };
  const own = DATA.methods.filter((m) => m.scope === "pays");
  kpi("count", String(DATA.methods.length), DATA.known ? `dont ${own.length} propres au pays` : "moyens de partout");
  kpi("mobile", String(DATA.methods.filter((m) => m.kind === "mobile").length), own.filter((m) => m.kind === "mobile").map((m) => m.name).slice(0, 3).join(", ") || "—");
  kpi("lang", official.length ? languageName(official[0]) : "—", official.slice(1).map((c) => languageName(c)).join(", "), true);
  kpi("money", DATA.currency, DATA.zone || "", true);
  const langCard = $(".pay-lang", sec);
  langCard.querySelector(".lang-ctl")?.remove();
  langCard.append(languageControl(official));

  const list = $(".pay-kinds", sec);
  list.replaceChildren(...DATA.kinds.map((k) => {
    const rows = DATA.methods.filter((m) => m.kind === k.code);
    if (!rows.length) return null;
    const card = el("div", "card"), h = el("div", "card-head"), title = el("h2", "");
    title.append(el("span", "pay-icon", ICON[k.code] || ""), el("span", "", k.fr));   // traduit par les dictionnaires
    h.append(title, el("span", "sub", String(rows.length)));
    const ul = el("ul", "pay-list");
    rows.forEach((m) => ul.append(methodCard(m)));
    card.append(h, ul);
    return card;
  }).filter(Boolean));
  if (!DATA.known) {
    const note = el("p", "hint", "Les moyens propres à ce pays ne sont pas encore recensés : voici ceux de sa zone et de partout.");
    list.prepend(note);
  }
  $(".pay-safety", sec).replaceChildren(...DATA.safety.map((s) => el("li", "", s.fr)));   // dans chaque dictionnaire
}

async function render() {
  if (!G) G = await geoData();
  if (!where) where = detect(G);
  await draw();
  // Position déjà autorisée : la ville se retrouve seule, sans rien demander.
  if (!autoTried && !["position", "choix"].includes(where.how)) {
    autoTried = true;
    if ((await positionPermission()) === "granted") findMe(false);
  }
}

register("payments", render, 300);
