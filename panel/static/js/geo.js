// Panneau TrendGuard : où est la personne qui regarde le panneau, et quelles
// langues officielles parle son pays (trendguard/profil.py, /api/geo). Le
// pays se reconnaît sur l'appareil, par son fuseau horaire et sa langue ; ou
// par sa position si la personne l'autorise (« Me localiser ») : la ville la
// plus proche est cherchée sur le PC du bot (/api/geo/locate) ; la position
// n'est ni gardée ni envoyée à un service extérieur.

import { api } from "./core.js";
import { autoLanguage, decide } from "./i18n.js";

const PAYS_KEY = "tg:pays";                    // pays choisi ou trouvé, gardé sur cet appareil
const HOW_KEY = "tg:pays-comment";             // comment il a été trouvé
const CITY_KEY = "tg:ville";                   // ville trouvée par la position
let GEO = null, WHERE = null;
export const currentCountry = () => (WHERE ? WHERE.country : "");

const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => {
    try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* stockage bloqué : valable pour cette visite */ }
  },
};

export async function geoData() {
  if (!GEO) GEO = await api("/api/geo");
  return GEO;
}

export const deviceZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } };
export const deviceLanguages = () => (navigator.languages && navigator.languages.length ? [...navigator.languages] : [navigator.language || "fr"]);

// Le pays de l'appareil : celui choisi ou trouvé par sa position (gardé sur
// cet appareil), sinon celui de son fuseau horaire (la région de sa langue
// départage les fuseaux alias, comme Lagos et Douala), sinon la région de sa
// langue (fr-CI…), sinon celui du profil du bot (TG_PAYS).
export function detect(g) {
  WHERE = findCountry(g);
  return WHERE;
}
function findCountry(g) {
  const known = new Set(g.countries.map((c) => c.iso));
  const saved = store.get(PAYS_KEY);
  if (saved && known.has(saved)) return { country: saved, how: store.get(HOW_KEY) || "choix", city: store.get(CITY_KEY) || "" };
  const regions = deviceLanguages().map((tag) => (tag.split("-")[1] || "").toUpperCase()).filter((r) => known.has(r));
  const zone = deviceZone(), hit = g.zones.find((z) => z[0] === zone);
  if (hit) {
    const alias = new Set(g.zones.filter((z) => z[2] === hit[2]).map((z) => z[1]));
    return { country: regions.find((r) => alias.has(r)) || hit[1], how: "fuseau", city: "", zone };
  }
  if (regions.length) return { country: regions[0], how: "langue", city: "" };
  return { country: g.profile.country, how: "profil", city: "" };
}

export function remember(country, how = "choix", city = "") {
  store.set(PAYS_KEY, country);
  store.set(HOW_KEY, how);
  store.set(CITY_KEY, city);
}
export function forget() {
  [PAYS_KEY, HOW_KEY, CITY_KEY].forEach((k) => store.set(k, ""));
}

// La position de l'appareil (le navigateur demande l'autorisation) → la
// ville la plus proche, cherchée sur le PC du bot (au kilomètre près). Seuls
// le pays et la ville trouvés sont gardés, sur cet appareil.
export function locate() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { reject(new Error("géolocalisation indisponible sur cet appareil")); return; }
    navigator.geolocation.getCurrentPosition(async (p) => {
      try {
        const found = await api(`/api/geo/locate?lat=${p.coords.latitude.toFixed(2)}&lon=${p.coords.longitude.toFixed(2)}`);
        if (found.near) remember(found.country, "position", found.city);
        resolve(found);
      } catch (e) { reject(e); }
    }, (e) => reject(new Error(e.code === 1 ? "autorisation refusée" : "position introuvable")), { timeout: 15000, maximumAge: 600000 });
  });
}

// L'autorisation de position déjà donnée ? (« granted » : relire la position
// ne demande rien ; « prompt » : le navigateur demandera ; « denied »).
export async function positionPermission() {
  try { return (await navigator.permissions.query({ name: "geolocation" })).state; } catch { return "prompt"; }
}

// Langues officielles d'un pays, celle que l'appareil préfère d'abord.
export function officialOrder(g, country) {
  const official = g.official[country] || [];
  const prefs = deviceLanguages().map((tag) => tag.split("-")[0].toLowerCase());
  return [...prefs.filter((c) => official.includes(c)), ...official.filter((c) => !prefs.includes(c))];
}

// Au démarrage : le pays de l'appareil, puis la langue du panneau qui va
// avec (sauf langue choisie dans les réglages).
export async function startGeo() {
  const g = await geoData();
  const where = detect(g);
  await autoLanguage(await decide(officialOrder(g, where.country)));
  return where;
}
