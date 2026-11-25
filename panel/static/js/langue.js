// Panneau TrendGuard : le choix de la langue du panneau (Réglages ▸ Affichage
// et page Paiement). « Automatique » suit la langue officielle du pays de
// l'appareil (geo.js) ; sont proposées les langues que le panneau écrit et
// celles du pays. Pour une langue que le panneau n'écrit qu'en partie, ou pas
// du tout, l'état de la traduction de l'appareil s'affiche, avec le bouton
// qui la télécharge (Chrome sur ordinateur, sur l'appareil, gratuitement).

import { el, lang } from "./core.js";
import { choice, downloadLanguage, machine, setLanguage, WRITTEN } from "./i18n.js";

export const languageName = (code, inLang = code) => {
  try { return new Intl.DisplayNames([inLang], { type: "language" }).of(code) || code; } catch { return code; }
};
const MACHINE = {
  ready: "traduction de l'appareil prête : tout le panneau s'affiche dans cette langue.",
  downloadable: "traduction de l'appareil à télécharger, gratuite, faite sur l'appareil :",
  downloading: "téléchargement de la langue en cours…",
  unavailable: "ce navigateur ne sait pas traduire dans cette langue : le reste s'affiche en anglais.",
  none: "ce navigateur ne traduit pas sur l'appareil (Chrome sur ordinateur le fait) : le reste s'affiche en anglais.",
};
const controls = new Set();

function refresh(c) {
  const code = lang(), written = WRITTEN[code];
  c.name.textContent = languageName(code);
  c.text.textContent = code === "fr" ? "langue d'origine du panneau."
    : written === "full" ? "tout le panneau est écrit dans cette langue."
    : (written ? "l'essentiel est écrit dans cette langue ; " : "") + MACHINE[machine()];
  c.dl.hidden = !(code !== "fr" && written !== "full" && machine() === "downloadable");
}
document.addEventListener("tg:langue", () => controls.forEach((c) => (c.box.isConnected ? refresh(c) : controls.delete(c))));

// Un sélecteur de langue, avec l'état de la traduction de l'appareil.
// `official` : les langues officielles du pays de l'appareil.
export function languageControl(official = []) {
  const box = el("div", "lang-ctl");
  const label = el("label", "tool-label", "Langue du panneau");
  const pick = el("select", "lang-pick");
  pick.id = "lang-" + Math.random().toString(36).slice(2, 8);
  label.htmlFor = pick.id;
  const auto = el("option", "", "Automatique : langue officielle du pays");
  auto.value = "";
  pick.append(auto);
  [...new Set(["fr", "en", ...Object.keys(WRITTEN), ...official, lang()])].forEach((code) => {
    const o = el("option", "", `${languageName(code)} (${code})`);
    o.value = code;
    o.setAttribute("translate", "no");                       // le nom de chaque langue, dans sa langue
    pick.append(o);
  });
  pick.value = choice();
  pick.addEventListener("change", () => setLanguage(pick.value));
  const state = el("p", "sub lang-state"), name = el("strong", ""), text = el("span", "");
  name.setAttribute("translate", "no");
  state.append(name, " : ", text);
  const dl = el("button", "btn small", "Télécharger la langue");
  dl.type = "button";
  dl.hidden = true;
  dl.addEventListener("click", async () => {
    dl.disabled = true;
    const ok = await downloadLanguage((p) => { dl.textContent = `${Math.round(p * 100)} %`; });
    dl.disabled = false;
    dl.textContent = ok ? "Télécharger la langue" : "Réessayer le téléchargement";
  });
  box.append(label, pick, state, dl);
  const c = { box, name, text, dl };
  controls.add(c);
  refresh(c);
  return box;
}
