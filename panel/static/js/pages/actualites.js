// Panneau TrendGuard, page « Actualités » : bandeau défilant, marchés, cryptos
// qui bougent, articles filtrés.

import { $, $$, api, countUp, el, fpct, fpx, nf, reduceMotion, up, withLoader } from "../core.js";
import { fillSpark } from "../charts.js";
import { currentTab, refreshTab, register } from "../navigation.js";
import { openDetail } from "../detail.js";

let NEWS = null, newsAt = 0, tickerKey = "", listKey = "", newsFilter = "all", pendingNews = null;
const NEWS_PAGE = 30;                   // articles affichés d'un coup
let newsShown = NEWS_PAGE, newsView = "";
const CAT = { crypto: "Crypto", finance: "Finance" };
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const fago = (iso) => {
  const t = iso ? Date.parse(iso) : NaN;
  if (!isFinite(t)) return "";
  const sec = Math.max(0, (Date.now() - t) / 1000);
  return sec < 3600 ? `il y a ${Math.max(1, Math.round(sec / 60))} min` : sec < 86400 ? `il y a ${Math.round(sec / 3600)} h` : `il y a ${Math.round(sec / 86400)} j`;
};
const fbig = (v) => (v == null || !isFinite(v) ? "–" : nf(0).format(v / 1e9) + " Md$");
export async function loadNews(maxAge = 60000) {
  if (!NEWS || NEWS.loading || Date.now() - newsAt > maxAge) {
    NEWS = await api("/api/news");
    newsAt = Date.now();
  }
  return NEWS;
}
export function renderTicker(n) {
  const items = n.items.slice(0, 30);
  const key = items.map((i) => i.url).join("|") + (n.loading ? "~" : "");
  if (key === tickerKey) return;                 // pas de saut du défilement
  tickerKey = key;
  const group = () => {
    const g = el("span", "ticker-group");
    if (!items.length) g.append(el("span", "t-item", n.loading ? "Chargement des actualités…" : "Actualités momentanément indisponibles."));
    items.forEach((it) => {
      const t = el("span", "t-item" + (it.alert ? " alert" : ""));
      t.dataset.url = it.url;
      t.append(el("b", "t-cat " + it.category, CAT[it.category] || it.category),
        el("span", "t-src", `${it.source} · ${fago(it.published)}`),
        el("span", "t-title", (it.alert ? "⚠ " : "") + it.title));
      g.append(t);
    });
    return g;
  };
  const first = group(), copy = group();
  copy.setAttribute("aria-hidden", "true");      // copie pour une boucle sans à-coup
  const track = $("#d-ticker-track");
  track.replaceChildren(first, copy);
  requestAnimationFrame(() => track.style.setProperty("--dur", Math.max(40, first.scrollWidth / 40) + "s"));
}
$("#d-ticker").addEventListener("click", (e) => {
  const it = e.target.closest(".t-item");
  pendingNews = it && it.dataset.url ? it.dataset.url : null;
  if (pendingNews) {                             // l'article cliqué doit être visible
    newsFilter = "all";
    $$("[data-news]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.news === "all")));
    $("#news-lang").value = "all";
    $("#news-search").value = "";
    listKey = "";
  }
});
async function renderNews() {
  const n = await loadNews(30000);
  renderTicker(n);
  const m = n.markets || {}, c = m.crypto, f = m.fear_greed;
  countUp($("#n-cap"), c ? c.market_cap_usd : null, fbig);
  const capSub = $("#n-cap-sub");
  capSub.textContent = c ? `${fpct(c.market_cap_change_24h_pct)} en 24 h` : "CoinGecko indisponible";
  capSub.className = "sub " + (c && c.market_cap_change_24h_pct >= 0 ? "up" : c ? "down" : "");
  countUp($("#n-dom"), c ? c.btc_dominance_pct : null, (v) => fpct(v, 1).replace("+", ""));
  $("#n-dom-sub").textContent = c ? `ether ${nf(1).format(c.eth_dominance_pct)} % du marché` : "";
  const fng = $("#n-fng");
  fng.textContent = f ? `${f.value} · ${f.label}` : "—";
  fng.className = f ? (f.value >= 55 ? "up" : f.value <= 45 ? "down" : "") : "";
  $("#n-gauge").style.setProperty("--v", f ? f.value + "%" : "50%");
  const hist = (f && f.history) || [];
  $("#n-fng-sub").textContent = hist.length > 7 ? `il y a 7 jours : ${hist[hist.length - 8]} · 0 = peur, 100 = euphorie` : "0 = peur extrême, 100 = euphorie";
  countUp($("#n-vol"), c ? c.volume_24h_usd : null, fbig);
  $("#n-vol-sub").textContent = c ? `${nf(0).format(c.active_cryptos)} cryptos cotées` : "";

  const quotes = m.quotes || [];
  $("#n-q-sub").textContent = quotes.length ? `variation du jour · courbe sur 1 mois · ${quotes[0].source}` : "cours indisponibles";
  $("#n-quotes").replaceChildren(...quotes.map((q, k) => {
    const card = el("div", "quote");
    card.style.animationDelay = k * 40 + "ms";
    const a = Math.abs(q.price || 0), d = q.unit === "%" || (a >= 10 && a < 1000) ? 2 : a >= 1000 ? 0 : 4;
    const head = el("div", "row");
    head.append(el("span", "q-name", q.name), el("span", "chg " + ((q.change_pct || 0) >= 0 ? "up" : "down"), q.change_pct == null ? "–" : fpct(q.change_pct)));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 200 42");
    svg.setAttribute("preserveAspectRatio", "none");
    fillSpark(svg, q.closes || []);
    card.append(head, el("strong", "q-px", q.price == null ? "–" : nf(d).format(q.price) + (q.unit === "%" ? " %" : q.unit === "$" ? " $" : "")), svg);
    return card;
  }));

  const mover = (r) => {
    const li = el("li");
    const b = el("button", "mover");
    b.type = "button";
    b.append(el("strong", "", up(r.asset)), el("span", "px", fpx(r.price)), el("span", "chg " + (r.change_pct >= 0 ? "up" : "down"), fpct(r.change_pct)));
    b.addEventListener("click", () => openDetail({ kind: "asset", asset: r.asset }));
    li.append(b);
    return li;
  };
  const mv = n.movers || { up: [], down: [] };
  $("#n-up").replaceChildren(...mv.up.map(mover));
  $("#n-down").replaceChildren(...mv.down.map(mover));
  renderNewsList();
  const ok = n.sources.filter((x) => x.ok).length, bad = n.sources.filter((x) => !x.ok);
  $("#n-sources").textContent = n.loading ? "Première lecture des sources (jusqu'à 30 s sur une connexion lente)…"
    : `${ok} source${ok > 1 ? "s" : ""} lue${ok > 1 ? "s" : ""}${n.updated ? " · mise à jour " + fago(n.updated) : ""}`
      + (bad.length ? ` · indisponibles : ${bad.map((x) => x.name).join(", ")}` : "")
      + " · liens vers les articles d'origine, aucune donnée envoyée";
  if (n.loading) setTimeout(() => { if (currentTab() === "news") refreshTab(true); }, 4000);
}
function renderNewsList() {
  if (!NEWS) return;
  const q = $("#news-search").value.trim().toLowerCase(), lang = $("#news-lang").value;
  const view = [newsFilter, lang, q].join("|");
  if (view !== newsView) { newsView = view; newsShown = NEWS_PAGE; }     // autre sélection : retour au début
  const key = [newsTag(), view, newsShown].join("|");
  if (key === listKey && !pendingNews) return;   // rien de nouveau : pas de réaffichage
  listKey = key;
  const held = new Set(NEWS.held || []);
  const rows = NEWS.items.filter((it) => (newsFilter === "all" || (newsFilter === "bot" ? it.assets.length : it.category === newsFilter))
    && (lang === "all" || it.lang === lang)
    && (!q || `${it.title} ${it.summary} ${it.source}`.toLowerCase().includes(q)));
  $("#n-sub").textContent = `${rows.length} article${rows.length > 1 ? "s" : ""} · 48 dernières heures`;
  const list = $("#n-list");
  if (!rows.length) {
    list.replaceChildren(el("li", "empty", NEWS.loading ? "Chargement des actualités…" : "Aucun article ne correspond."));
    return;
  }
  if (pendingNews) {                             // l'article cliqué dans le bandeau doit être affiché
    const at = rows.findIndex((it) => it.url === pendingNews);
    if (at >= newsShown) newsShown = Math.ceil((at + 1) / NEWS_PAGE) * NEWS_PAGE;
  }
  list.replaceChildren(...rows.slice(0, newsShown).map((it, k) => {
    const li = el("li", "news" + (it.alert ? " alert" : ""));
    li.dataset.url = it.url;
    li.style.animationDelay = Math.min(k, 20) * 30 + "ms";
    const meta = el("div", "news-meta");
    meta.append(el("b", "t-cat " + it.category, CAT[it.category] || it.category), el("span", "", it.source),
      el("span", "", fago(it.published)), el("span", "lang", up(it.lang)));
    if (it.alert) meta.append(el("span", "tag vetoed", "Concerne le bot"));
    const a = el("a", "news-title", it.title);
    a.href = safeUrl(it.url);
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.append(meta, a);
    if (it.summary) li.append(el("p", "news-sum", it.summary));
    const tags = el("div", "news-tags");
    it.assets.forEach((x) => {
      const b = el("button", "chip " + (held.has(x) ? "warn" : "watch"));
      b.type = "button";
      b.title = "Ouvrir le graphique";
      b.append(el("strong", "", up(x)), el("span", "", held.has(x) ? "détenue par le bot" : "suivie par le bot"));
      b.addEventListener("click", () => openDetail({ kind: "asset", asset: x }));
      tags.append(b);
    });
    it.topics.forEach((t) => tags.append(el("span", "tag muted", t)));
    if (tags.childNodes.length) li.append(tags);
    return li;
  }));
  if (rows.length > newsShown) {
    const more = el("li", "more"), b = el("button", "btn ghost", `Afficher ${Math.min(NEWS_PAGE, rows.length - newsShown)} articles de plus (${rows.length - newsShown} restants)`);
    b.type = "button";
    b.addEventListener("click", () => { newsShown += NEWS_PAGE; renderNewsList(); });
    more.append(b);
    list.append(more);
  }
  if (pendingNews) {
    const hit = $$("#n-list li").find((li) => li.dataset.url === pendingNews);
    pendingNews = null;
    if (hit) {
      hit.classList.add("focus");
      hit.scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" });
    }
  }
}
const newsTag = () => (NEWS ? NEWS.items.map((i) => i.url).join("|") : "");
$$("[data-news]").forEach((b) => b.addEventListener("click", () => {
  newsFilter = b.dataset.news;
  $$("[data-news]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  withLoader(`Sélection : ${b.textContent}…`, renderNewsList);
}));
$("#news-lang").addEventListener("change", (e) => {
  withLoader(`Langue : ${e.currentTarget.selectedOptions[0].textContent}…`, renderNewsList);
});
$("#news-search").addEventListener("input", () => renderNewsList());

register("news", renderNews, 60);
