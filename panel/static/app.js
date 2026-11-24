// Panneau TrendGuard : point d'entrée. Données : API JSON du serveur local
// (panel/server.py) ; tout texte venant des données passe par textContent.
//
// Organisation (panel/static/js/) :
//   core.js        outils communs : formats, DOM, préférences, chargement, API
//   charts.js      graphiques communs : options, flèches, stops, courbes animées
//   indicateurs.js calculs des indicateurs techniques (purs, testés)
//   analyse.js     graphique d'analyse : prix, indicateurs, panneaux, lecture
//   etat.js        état du bot, barre du haut, bouton AUTO
//   navigation.js  onglets, rafraîchissement de la page affichée
//   detail.js      fenêtre de détail d'un graphique
//   connexion.js   mot de passe du panneau
//   assistant.js   Rachelle ; rapport.js : rapport quotidien, sécurité
//   pages/*.js     une page par onglet, qui s'enregistre elle-même ; marches.js :
//                  une page par classe d'actifs hors cryptos

import { $, $$, cssVar, onUnauthorized, prefs } from "./js/core.js";
import { restyleCharts } from "./js/charts.js";
import { closeChat } from "./js/assistant.js";
import { refreshStatus } from "./js/etat.js";
import { currentTab, menuOpen, refreshTab, route, setMenu, tick } from "./js/navigation.js";
import { handleDetailEscape, restyleDetail } from "./js/detail.js";
import { showLogin } from "./js/connexion.js";
import "./js/pages/tableau.js";
import "./js/pages/actualites.js";
import { flashBuy } from "./js/pages/graphiques.js";
import "./js/pages/cryptos.js";
import "./js/pages/marches.js";
import "./js/pages/positions.js";
import "./js/pages/veille.js";
import "./js/pages/journal.js";
import "./js/pages/reglages.js";

onUnauthorized(() => showLogin());      // session expirée

// ---------- Thème ----------
function applyTheme(mode) {
  if (mode === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  prefs.set("theme", mode);
  $$("[data-theme-set]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === mode)));
  $('meta[name="theme-color"]').setAttribute("content", cssVar("--bg"));
  restyleCharts();
  restyleDetail();
}
$("#theme-btn").addEventListener("click", () => {
  const dark = cssVar("color-scheme") !== "light";
  applyTheme(dark ? "light" : "dark");
});
$$("[data-theme-set]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themeSet)));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { restyleCharts(); restyleDetail(); });

// ---------- Achat en temps réel ----------
// Annoncé par etat.js : les pages qui le montrent se redessinent, la carte
// de la crypto achetée s'illumine.
document.addEventListener("tg:achat", (e) => {
  if (!["dash", "charts", "positions", "assets"].includes(currentTab())) return;
  refreshTab(true).then(() => flashBuy(e.detail.asset));
});

// ---------- Clavier ----------
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (menuOpen()) { setMenu(false); $("#menu-btn").focus(); return; }
  if (!handleDetailEscape(e) && !$("#chat").hidden) closeChat();
});

// ---------- Démarrage ----------
applyTheme(prefs.get("theme", "auto"));
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* application installable : facultatif */ });
}
refreshStatus().then(() => { route(); setInterval(tick, 1000); });
