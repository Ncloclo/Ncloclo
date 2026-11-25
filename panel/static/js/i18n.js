// Panneau TrendGuard : la langue du panneau, celle du pays de l'appareil
// (geo.js, trendguard/profil.py). Chaque texte est écrit en français ; un
// dictionnaire par langue (i18n/*.js) le traduit à mesure que les pages
// l'affichent : l'anglais en entier ; l'allemand, l'espagnol, le portugais,
// le russe, le chinois et l'arabe pour l'essentiel (menu, titres, boutons,
// réglages, paiement). Ce qu'aucun dictionnaire ne couvre, et toute autre
// langue officielle, passe par la traduction de l'appareil quand il l'a
// (Chrome sur ordinateur : la traduction se fait sur l'appareil, rien n'est
// envoyé), à partir de l'anglais ; sinon le texte reste en anglais. Choix
// manuel : Réglages ▸ Affichage.

const LANG_KEY = "tg:langue";              // choix de la personne ("" : automatique)
const AUTO_KEY = "tg:langue-auto";         // dernière langue trouvée pour le pays de l'appareil
const CACHE_PREFIX = "tg:trad:";           // traductions de l'appareil gardées, par langue
const CACHE_MAX = 4000;
// Langues écrites par le panneau : en entier ou pour l'essentiel.
export const WRITTEN = { en: "full", de: "core", es: "core", pt: "core", ru: "core", zh: "core", ar: "core" };
const RTL = new Set(["ar", "he", "fa", "ur", "ps", "dv", "yi", "ckb"]);
const ATTRS = ["title", "placeholder", "aria-label", "data-title", "alt"];
const SKIP = "script, style, code, pre, textarea, [translate='no'], .notranslate";
const FRENCH = /[èêàùçôîûëïœ]|\b(?:le|les|des|du|une|et|est|sont|pour|dans|sur|avec|sans|pas|vous|votre|vos|aux|cette|ces|qui|elle|ou|par|plus|chaque|aucun|aucune|depuis|selon|jour|jours|été|être)\b/i;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) {
    try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* stockage bloqué : valable pour cette visite */ }
  },
};
const clean = (code) => (/^[a-z]{2,3}$/.test(code || "") ? code : "");

const chosen = clean(store.get(LANG_KEY));
let target = chosen || clean(store.get(AUTO_KEY)) || "fr";
let DICT = null, PIVOT = null, active = false;
let machineState = "none";                  // traduction de l'appareil : none, ready, downloadable, downloading, unavailable
const TR = {};                              // traducteurs de l'appareil, par langue source
let CACHE = new Map(), cacheDirty = false;
const MINE = new Set();                     // textes déjà traduits (à ne pas retraduire)

export const lang = () => target;           // langue affichée
export const choice = () => chosen;         // "" : automatique
export const machine = () => machineState;
export function locale() {
  if (target === "fr") return "fr-FR";
  if (target === "en") return "en-GB";
  return target + "-u-nu-latn";            // chiffres latins partout (cours, pourcentages)
}

// ---------- Dictionnaires ----------
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function compile(dict) {
  const exact = new Map(), buckets = new Map(), loose = [];
  for (const [fr, out] of Object.entries(dict)) {
    if (!/\{\d+\}/.test(fr)) { exact.set(fr, out); continue; }
    const order = [];
    const src = fr.split(/(\{\d+\})/).map((part) => {
      const m = /^\{(\d+)\}$/.exec(part);
      if (!m) return esc(part);
      order.push(Number(m[1]));
      return "(.*?)";
    }).join("");
    const literal = fr.replace(/\{\d+\}/g, "").length;
    const p = { rx: new RegExp("^" + src + "$", "s"), order, out, literal };
    const head = fr.split(/\{\d+\}/)[0];
    if (head.length >= 2) {
      const k = head.slice(0, 2);
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k).push(p);
    } else loose.push(p);
  }
  return { exact, buckets, loose };
}
// Un modèle ({0}…) ne vaut que pour un texte où sa partie fixe pèse : jamais
// pour une phrase venue des données où il ne remplacerait qu'un mot (« aucun
// achat tant que… »), ni quand une partie variable est une phrase française
// que le dictionnaire ne connaît pas.
function lookup(c, text) {
  const hit = c.exact.get(text);
  if (hit !== undefined) return hit;
  for (const p of [...(c.buckets.get(text.slice(0, 2)) || []), ...c.loose]) {
    const m = p.rx.exec(text);
    if (!m) continue;
    const parts = m.slice(1), share = p.literal / text.length;
    const raw = parts.some((v) => !c.exact.has(v.trim()) && FRENCH.test(v));
    if (share < 0.2 || (raw && share < 0.5)) continue;
    return p.out.replace(/\{(\d+)\}/g, (_, n) => {
      const v = parts[p.order.indexOf(Number(n))] ?? "";
      return c.exact.get(v.trim()) ?? v;
    });
  }
  return null;
}
async function load(code) {
  if (!clean(code)) return null;
  try { return (await import(`./i18n/${code}.js`)).default; } catch { return null; }
}

// ---------- Traduction de l'appareil (Chrome : Translator) ----------
const hasMachine = () => typeof self.Translator !== "undefined";
function cacheKey() { return CACHE_PREFIX + target; }
function loadCache() {
  try { CACHE = new Map(JSON.parse(localStorage.getItem(cacheKey()) || "[]")); } catch { CACHE = new Map(); }
}
function saveCache() {
  if (!cacheDirty) return;
  cacheDirty = false;
  const rows = [...CACHE].slice(-CACHE_MAX);
  try { localStorage.setItem(cacheKey(), JSON.stringify(rows)); } catch { /* stockage plein : gardé pour cette visite */ }
}
setInterval(saveCache, 3000);
const cacheGet = (from, text) => CACHE.get(from + "|" + text);
function cacheSet(from, text, out) {
  CACHE.delete(from + "|" + text);
  CACHE.set(from + "|" + text, out);
  if (CACHE.size > CACHE_MAX) CACHE.delete(CACHE.keys().next().value);
  cacheDirty = true;
}
async function availability(from) {
  if (!hasMachine() || from === target) return "unavailable";
  try { return await self.Translator.availability({ sourceLanguage: from, targetLanguage: target }); } catch { return "unavailable"; }
}
function translator(from) {
  if (!TR[from]) {
    TR[from] = (async () => {
      const a = await availability(from);
      if (a !== "available") {
        if (from === "en" && machineState !== "ready") { machineState = a; announce(); }
        return null;
      }
      const t = await self.Translator.create({ sourceLanguage: from, targetLanguage: target });
      if (from === "en") { machineState = "ready"; announce(); }
      return t;
    })().catch(() => null);
  }
  return TR[from];
}
const pending = new Map();
function machineTranslate(from, text) {
  const key = from + "|" + text;
  if (!pending.has(key)) {
    pending.set(key, (async () => {
      let tr = await translator(from);
      let src = text;
      if (!tr && from === "fr") {                   // français → anglais → langue voulue
        const toEn = hasMachine() && target !== "en" ? await enTranslator() : null;
        tr = await translator("en");
        if (!toEn || !tr) return null;
        src = await toEn.translate(text);
      }
      if (!tr) return null;
      const out = await tr.translate(src);
      cacheSet(from, text, out);
      return out;
    })().catch(() => null).finally(() => setTimeout(() => pending.delete(key), 0)));
  }
  return pending.get(key);
}
let EN_TR = null;
function enTranslator() {
  if (!EN_TR) {
    EN_TR = (async () => {
      const a = await self.Translator.availability({ sourceLanguage: "fr", targetLanguage: "en" });
      return a === "available" ? self.Translator.create({ sourceLanguage: "fr", targetLanguage: "en" }) : null;
    })().catch(() => null);
  }
  return EN_TR;
}

// La question écrite dans une autre langue, traduite en anglais par
// l'appareil (Rachelle y reconnaît mieux le sujet) ; null sinon.
let TO_EN = null;
export async function toEnglish(text) {
  if (target === "fr" || target === "en" || !hasMachine()) return null;
  if (!TO_EN) {
    TO_EN = (async () => {
      const opts = { sourceLanguage: target, targetLanguage: "en" };
      return (await self.Translator.availability(opts)) === "available" ? self.Translator.create(opts) : null;
    })().catch(() => null);
  }
  const tr = await TO_EN;
  if (!tr) return null;
  try { return await tr.translate(text); } catch { return null; }
}

// Télécharger la langue sur l'appareil : à appeler d'un clic (le navigateur
// le demande). `progress` reçoit l'avancement, de 0 à 1.
export async function downloadLanguage(progress) {
  if (!hasMachine() || target === "fr") return false;
  machineState = "downloading";
  announce();
  const monitor = (m) => m.addEventListener("downloadprogress", (e) => progress && progress(e.loaded));
  try {
    const t = await self.Translator.create({ sourceLanguage: "en", targetLanguage: target, monitor });
    TR.en = Promise.resolve(t);
    try {
      TR.fr = Promise.resolve(await self.Translator.create({ sourceLanguage: "fr", targetLanguage: target }));
    } catch { TR.fr = Promise.resolve(null); }   // le français passera par l'anglais
    machineState = "ready";
    announce();
    retranslate();
    return true;
  } catch {
    machineState = "downloadable";
    announce();
    return false;
  }
}

// ---------- Textes affichés ----------
// Le texte à montrer pour un texte écrit en français : son dictionnaire, puis
// les traductions de l'appareil déjà faites ; sinon l'anglais (ou le texte),
// en attendant la traduction de l'appareil (`from`). `hint` : la langue d'un
// texte venu des données (attribut data-lang, « en » pour un texte anglais).
const keep = (src, key, out) => src.replace(key, () => out);
function immediate(src, hint = "") {
  const key = src.trim();
  if (!key || hint === target) return null;
  if (hint === "en") {
    const done = cacheGet("en", key);
    return done != null ? { text: keep(src, key, done) } : { text: src, from: "en", base: key };
  }
  const own = DICT && lookup(DICT, key);
  if (own != null) return { text: keep(src, key, own) };
  const en = target === "en" ? null : PIVOT && lookup(PIVOT, key);
  const from = en != null ? "en" : "fr", base = en ?? key;
  if (from === "fr" && !FRENCH.test(key)) return null;           // pas du français (nom, nombre, autre langue)
  const done = cacheGet(from, base);
  if (done != null) return { text: keep(src, key, done) };
  return { text: keep(src, key, base), from, base };
}
export function t(fr) {
  if (target === "fr" || !active || fr == null) return fr;
  const r = immediate(String(fr));
  return r ? r.text : fr;
}
function mine(text) {
  if (MINE.size > 20000) MINE.clear();
  MINE.add(text.trim());
}

const SRC = new WeakMap();                 // nœud texte → { src, shown }
const ASRC = new WeakMap();                // élément → Map(attribut → { src, shown })
const skipped = (el) => !el || !!el.closest(SKIP);
const hintOf = (el) => (el && el.closest("[data-lang]")?.dataset.lang) || "";

function show(src, r, write, still) {
  write(r.text);
  if (!r.from || !hasMachine() || machineState === "unavailable") return;
  machineTranslate(r.from, r.base).then((out) => {
    if (out && still(r.text)) write(keep(src, src.trim(), out));
  });
}
function onText(node) {
  const v = node.nodeValue;
  const rec = SRC.get(node);
  if (rec && rec.shown === v) return;
  if (!v || !/\p{L}/u.test(v) || MINE.has(v.trim()) || skipped(node.parentElement)) return;
  const r = immediate(v, hintOf(node.parentElement));
  if (!r) { SRC.set(node, { src: v, shown: v }); return; }
  const write = (text) => { SRC.set(node, { src: v, shown: text }); mine(text); if (node.nodeValue !== text) node.nodeValue = text; };
  show(v, r, write, (shown) => SRC.get(node)?.shown === shown);
}
function onAttr(el, name) {
  const v = el.getAttribute(name);
  if (v == null || skipped(el)) return;
  if (!ASRC.has(el)) ASRC.set(el, new Map());
  const recs = ASRC.get(el), rec = recs.get(name);
  if (rec && rec.shown === v) return;
  if (!/\p{L}/u.test(v) || MINE.has(v.trim())) return;
  const r = immediate(v, hintOf(el));
  if (!r) { recs.set(name, { src: v, shown: v }); return; }
  const write = (text) => { recs.set(name, { src: v, shown: text }); mine(text); if (el.getAttribute(name) !== text) el.setAttribute(name, text); };
  show(v, r, write, (shown) => recs.get(name)?.shown === shown);
}
function walk(root) {
  if (root.nodeType === 3) { onText(root); return; }
  if (root.nodeType !== 1 || skipped(root)) return;
  ATTRS.forEach((a) => root.hasAttribute(a) && onAttr(root, a));
  root.querySelectorAll("[title], [placeholder], [aria-label], [data-title], [alt]").forEach((el) => {
    ATTRS.forEach((a) => el.hasAttribute(a) && onAttr(el, a));
  });
  const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = it.nextNode(); n; n = it.nextNode()) onText(n);
}
// Tout retraduire depuis les textes d'origine (traduction de l'appareil prête).
function retranslate() {
  const it = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = it.nextNode(); n; n = it.nextNode()) {
    const rec = SRC.get(n);
    if (rec && rec.shown === n.nodeValue && rec.src !== rec.shown && !FRENCH.test(rec.src)) continue;
    if (rec) { SRC.delete(n); MINE.delete(n.nodeValue.trim()); n.nodeValue = rec.src; }
    onText(n);
  }
}
const observer = new MutationObserver((list) => {
  for (const m of list) {
    if (m.type === "childList") m.addedNodes.forEach(walk);
    else if (m.type === "characterData") onText(m.target);
    else if (m.type === "attributes") onAttr(m.target, m.attributeName);
  }
});

function announce() {
  document.dispatchEvent(new CustomEvent("tg:langue", { detail: { lang: target, machine: machineState } }));
}

async function activate() {
  const [own, en] = await Promise.all([WRITTEN[target] ? load(target) : null, target === "en" ? null : load("en")]);
  DICT = own ? compile(own) : null;
  PIVOT = target === "en" ? DICT : en ? compile(en) : null;
  loadCache();
  document.documentElement.lang = target;
  document.documentElement.dir = RTL.has(target) ? "rtl" : "ltr";
  active = true;
  if (WRITTEN[target] !== "full") translator("en");          // l'appareil sait-il traduire ? (état, bouton)
  walk(document.body);
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  announce();
}

// La personne choisit sa langue ("" : celle du pays de l'appareil) : la page
// se recharge dans la nouvelle langue.
export function setLanguage(code) {
  store.set(LANG_KEY, clean(code));
  location.reload();
}

// La langue trouvée pour le pays de l'appareil (geo.js) : appliquée tout de
// suite si rien n'est choisi (un rechargement si une autre langue est déjà
// affichée).
export async function autoLanguage(code) {
  code = clean(code) || "en";
  store.set(AUTO_KEY, code);
  if (chosen || code === target) return;
  if (target !== "fr") { location.reload(); return; }
  target = code;
  await activate();
}

// La langue du panneau pour un pays : parmi ses langues officielles (celle
// que l'appareil préfère d'abord), la première que le panneau écrit ou que
// l'appareil sait traduire ; sinon l'anglais.
export async function decide(order) {
  for (const code of order) {
    if (code === "fr" || WRITTEN[code]) return code;
    if (hasMachine()) {
      try {
        if ((await self.Translator.availability({ sourceLanguage: "en", targetLanguage: code })) !== "unavailable") return code;
      } catch { /* langue inconnue du traducteur */ }
    }
  }
  return "en";
}

if (target !== "fr") await activate();
