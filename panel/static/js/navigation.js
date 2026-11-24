// Panneau TrendGuard : onglets (adresse #page), rafraîchissement de la page
// affichée et horloge du haut. Chaque page s'enregistre avec register().

import { $, $$, fdur, toast, withLoader } from "./core.js";
import { lastOk, nextDecisionAt, refreshStatus, S, statusErr } from "./etat.js";

const PAGES = new Map();            // onglet → { render, period (s) }
let current = null, lastTab = 0, lastStatus = 0, busy = false;

// Une page : sa fonction d'affichage et sa période de rafraîchissement.
export function register(tab, render, period) {
  PAGES.set(tab, { render, period });
}
export const currentTab = () => current;

export function route() {
  const hash = location.hash.slice(1), t = PAGES.has(hash) ? hash : "dash";
  if (t === current) return;
  current = t;
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + t));
  $$(".tab").forEach((a) => {
    if (a.dataset.tab === t) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const title = $("#page-" + t).dataset.title;
  $("#page-title").textContent = title;
  window.scrollTo({ top: 0 });
  withLoader(`Chargement : ${title}…`, () => refreshTab(true));
}
window.addEventListener("hashchange", route);
$$("[data-go]").forEach((b) => b.addEventListener("click", () => { location.hash = "#" + b.dataset.go; }));

export async function refreshTab(force = false) {
  if (busy && !force) return;
  busy = true;
  lastTab = Date.now();
  try {
    if (!S) await refreshStatus();
    if (!S) return;                     // pas encore connecté
    await PAGES.get(current).render();
  } catch (e) {
    if (e.message !== "connexion requise") toast(`Actualisation : ${e.message}`, "err");
  } finally {
    busy = false;
  }
}
// Chaque seconde : compte à rebours, fraîcheur, état toutes les 5 s, page à sa période.
export function tick() {
  if (document.hidden || !$("#login").hidden) return;
  const now = Date.now();
  if (nextDecisionAt) $("#d-next").textContent = fdur((nextDecisionAt - now) / 1000);
  if (lastOk && !statusErr) $("#updated").textContent = `Mis à jour il y a ${Math.round((now - lastOk) / 1000)} s`;
  if (now - lastStatus > 5000) { lastStatus = now; refreshStatus(); }
  if (current && now - lastTab > PAGES.get(current).period * 1000) refreshTab();
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { lastStatus = 0; lastTab = 0; tick(); } });
