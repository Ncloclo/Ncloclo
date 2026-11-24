// Panneau TrendGuard, page « Veille » : sentiment, contrats à terme,
// calendrier, IA et noyau de savoir.

import { $, api, countUp, el, fdate, fpct, ftime, nf, table, up } from "../core.js";
import { register } from "../navigation.js";

// Contrats à terme de Binance (trendguard/derives.py) : financement payé
// toutes les 8 h par les acheteurs (positif) ou les vendeurs (négatif), et
// intérêt ouvert de BTC. Information seulement : aucun achat n'en dépend.
function renderFutures(d) {
  const fund = $("#w-fund"), oi = $("#w-oi");
  if (!fund || !oi) return;
  d = d || {};
  const yearly = (r) => r * 3 * 365 * 100;
  if (d.funding_btc == null) { fund.textContent = "—"; $("#w-fund-sub").textContent = ""; }
  else {
    fund.textContent = `${fpct(yearly(d.funding_btc), 1)} / an`;
    fund.className = "num " + (yearly(d.funding_btc) > 30 ? "down" : yearly(d.funding_btc) < 0 ? "up" : "");
    $("#w-fund-sub").textContent = `BTC · moyenne des cryptos ${d.funding_mean == null ? "—" : fpct(yearly(d.funding_mean), 1)} / an`
      + (yearly(d.funding_btc) > 30 ? " · beaucoup d'effet de levier à l'achat" : yearly(d.funding_btc) < 0 ? " · les vendeurs paient" : "");
  }
  if (d.oi_btc_30d == null) { oi.textContent = "—"; $("#w-oi-sub").textContent = ""; }
  else {
    oi.textContent = fpct(d.oi_btc_30d * 100, 1);
    oi.className = "num";
    $("#w-oi-sub").textContent = d.long_share_btc == null ? "contrats en cours sur BTC"
      : `${nf(0).format(d.long_share_btc * 100)} % des comptes sont acheteurs`;
  }
}

async function renderWatch() {
  const w = await api("/api/watch");
  const last = w.last, mood = $("#w-mood");
  mood.textContent = !last ? "—" : last.sentiment > 0.2 ? "Positif" : last.sentiment < -0.2 ? "Négatif" : "Neutre";
  mood.className = !last ? "" : last.sentiment > 0.2 ? "up" : last.sentiment < -0.2 ? "down" : "";
  $("#w-mood-sub").textContent = last ? `${nf(2).format(last.sentiment)} · ${last.providers_total ? `${last.providers}/${last.providers_total} IA` : "mots-clés"} · ${fdate(last.day)}` : "pas encore de rapport";
  countUp($("#w-vetoes"), w.vetoes.length, (v) => String(Math.round(v)));
  const items = [...w.vetoes.map((v) => ["crit", `${v.reason} : achats bloqués jusqu'au ${fdate(v.until)}`]),
    ...((last && last.alerts) || []).map((a) => ["warn", a])];
  if (!items.length) items.push(["ok", "Aucune alerte."]);
  $("#w-list").replaceChildren(...items.map(([k, t]) => el("li", k, t)));
  renderCalendar(w.evenements);
  renderLibrary(w.bibliotheque);
  renderMarkets(w.marches);
  renderLeverage(w.levier);
  renderMacro(w.macro);
  const r = w.report || {}, ai = w.ai || { configured: [], possible: [] };
  const FNG = { "extreme fear": "Peur extrême", fear: "Peur", neutral: "Neutre", greed: "Avidité", "extreme greed": "Avidité extrême" };
  const fng = $("#w-fng");
  fng.textContent = r.fear_greed == null ? "—" : `${r.fear_greed} · ${FNG[String(r.fear_greed_label || "").toLowerCase()] || r.fear_greed_label || ""}`;
  fng.className = r.fear_greed == null ? "" : r.fear_greed >= 55 ? "up" : r.fear_greed <= 45 ? "down" : "";
  const peg = $("#w-peg"), off = r.usdc_usdt == null ? null : Math.abs(r.usdc_usdt - 1);
  peg.textContent = off == null ? "—" : nf(4).format(r.usdc_usdt);
  peg.className = "num " + (off == null ? "" : off > 0.005 ? "down" : "up");
  $("#w-peg-sub").textContent = off == null ? "" : off > 0.005 ? "un stablecoin décroche" : "normale : les deux valent 1 dollar";
  renderFutures(r.derives);
  $("#w-items").textContent = r.items == null ? "—" : String(r.items);
  const made = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(r.generated || "");
  $("#w-items-sub").textContent = made ? `rapport du ${fdate(made[1])} à ${made[2]} UTC` : "";
  const asked = r.providers || [];
  $("#w-ai-sub").textContent = ai.configured.length ? `${ai.configured.length} configurée${ai.configured.length > 1 ? "s" : ""}` : "aucune configurée";
  const rows = asked.map((p) => [p.ok ? "ok" : "warn", p.ok ? `${p.label} : avis reçu` : `${p.label} : pas d'avis (${p.error || "cause inconnue"})`]);
  ai.configured.filter((name) => !asked.some((p) => p.label === name)).forEach((name) => rows.push(["ok", `${name} : consultée au prochain rapport`]));
  // Socle multi-modèles (trendguard/modeles.py) : cycle de vie, banc, santé mesurée ; jamais de chiffre sans mesure.
  ((w.modeles || {}).rows || []).forEach((m) => {
    if (m.lifecycle === "TESTING") { rows.push(["warn", `${m.label} (${m.model}) : pas encore en service, ${m.lifecycle_reason}`]); return; }
    const bench = m.accuracy != null ? `approuvé au banc (justesse ${nf(0).format(m.accuracy * 100)} %)` : "approuvé";
    const used = m.calls ? `, ${m.calls} appel${m.calls > 1 ? "s" : ""} mesuré${m.calls > 1 ? "s" : ""}, ${m.failures} échec${m.failures > 1 ? "s" : ""}${m.p50_ms != null ? `, réponse en ${nf(1).format(m.p50_ms / 1000)} s (médiane)` : ""}` : "";
    rows.push([m.breaker === "CLOSED" ? "ok" : "warn", `${m.label} (${m.model}) : ${bench}${used}${m.breaker === "OPEN" ? " · mis de côté 15 min après 3 échecs de suite" : ""}`]);
  });
  if (last && last.disagreements && last.disagreements.length) rows.push(["warn", `IA en désaccord sur ${last.disagreements.map((a) => (a === "market" ? "le climat" : up(a))).join(", ")} : avis à prendre avec prudence.`]);
  if (!rows.length) rows.push(["ok", "Sans IA, la veille fait déjà l'essentiel : elle lit les annonces officielles de Binance (les seules à bloquer un achat) et repère les mots sensibles dans les actualités."]);
  $("#w-ai").replaceChildren(...rows.map(([k, t]) => el("li", k, t)));
  const free = ai.possible.filter((name) => !ai.configured.includes(name));
  const help = $("#w-ai-help");
  help.replaceChildren();
  if (free.length) help.append("Facultatif : pour ajouter l'avis d'une IA, ", el("code", "", "python trendguard_bot.py watch set-key claude"), ` (au choix : ${free.join(", ")}). La clé se saisit masquée ; une IA conseille, elle ne passe jamais d'ordre.`);
  $("#w-report").textContent = w.report_text || "Aucun rapport : la veille tourne chaque jour avec le bot (python trendguard_bot.py watch pour un rapport immédiat).";
  renderKnowledge(w.savoir);
}
// Calendrier économique (trendguard/evenements.py) : information seulement.
const dCal = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
function renderCalendar(c) {
  $("#w-cal-card").hidden = !c;
  if (!c) return;
  const now = Date.now();
  const rows = (c.events || []).map((e) => {
    const t = Date.parse(e.at), past = t < now, soon = !past && t - now < 48 * 3600e3;
    const nums = [e.forecast && `prévision ${e.forecast}`, e.previous && `précédent ${e.previous}`].filter(Boolean).join(", ");
    return [soon ? "warn" : "ok", `${dCal.format(new Date(t))} UTC — ${e.label}${nums ? ` · ${nums}` : ""}${past ? " · passée" : ""}`];
  });
  if (!rows.length) rows.push(["ok", "Aucune grande annonce américaine prévue d'ici 7 jours."]);
  if (c.reaction && c.reaction.text) rows.push(["ok", `Mesure : ${c.reaction.text}.`]);
  if (c.error) rows.push(["warn", `Dernière lecture ratée : ${c.error} (nouvel essai dans l'heure).`]);
  $("#w-cal").replaceChildren(...rows.map(([k, t]) => el("li", k, t)));
  $("#w-cal-sub").textContent = c.fetched_at ? `lu le ${ftime(Date.parse(c.fetched_at) / 1000)}` : "pas encore lu";
}
// Tous les marchés hors cryptos (trendguard/multi_actifs.py) : état pour la
// règle et portefeuille en argent fictif. Information seulement.
function portfolioLine(title, pf) {
  return pf
    ? `${title} depuis le ${fdate(pf.start)} : ${nf(2).format(pf.capital)} → ${nf(2).format(pf.equity)} USDT (${fpct(pf.return_pct)}), `
      + `${pf.positions.length} position${pf.positions.length > 1 ? "s" : ""}${pf.positions.length ? " : " + pf.positions.map((p) => p.name).join(", ") : ""} ; ${pf.trades} vente${pf.trades > 1 ? "s" : ""}.`
    : `${title} : pas encore lancé (première lecture avec le noyau de savoir).`;
}
function renderMarkets(m) {
  $("#w-mkt-card").hidden = !m;
  if (!m) return;
  $("#w-mkt-pf").textContent = portfolioLine("Portefeuille fictif hors cryptos (coûts réels de chaque marché)", m.portfolio);
  $("#w-mkt-bn").textContent = portfolioLine("Manche Binance fictive : or, euro, actions tokenisées (frais de Binance)", m.binance);
  const rows = (m.rows || []).map((r) => ({
    cells: [r.name + (r.binance ? " · sur Binance" : ""), r.category, nf(r.close < 10 ? 4 : 2).format(r.close),
      r.trend ? "au-dessus de sa moyenne 150" : "sous sa moyenne 150",
      r.signal ? "achat possible" : "—"],
    cls: [null, null, null, r.trend ? "up" : "down", r.signal ? "up" : ""],
  }));
  table($("#w-mkt-table"), ["Marché", "Catégorie", "Cours", "Tendance", "Signal du jour"], rows, null,
    "Pas encore lus : première lecture avec le noyau de savoir.", false);
  const sub = m.fetched_at ? `lus le ${fdate(m.fetched_at)}` : "pas encore lus";
  $("#w-mkt-sub").textContent = sub + (m.error ? ` · dernière lecture ratée : ${m.error}` : "")
    + (m.unread && m.unread.length ? ` · illisibles : ${m.unread.join(", ")}` : "");
}
// Indicateurs de marché (trendguard/multi_actifs.py, docs/MACRO.md), lus
// chaque jour après la clôture de New York. Information seulement : aucun
// achat n'en dépend (aucun filtre n'a passé l'étude).
const pt = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf(2).format(Math.abs(v))} point`;
function renderMacro(m) {
  const vix = m && m.vix, dxy = m && m.dxy, r10 = m && m.rates && m.rates.us10y;
  $("#w-vix").textContent = vix ? nf(1).format(vix.value) : "—";
  $("#w-vix").className = "num " + (!vix ? "" : vix.value >= 30 ? "down" : vix.value < 20 ? "up" : "");
  $("#w-vix-sub").textContent = vix ? `${vix.level} · médiane d'un an ${nf(1).format(vix.median_1y)}` : "pas encore lu";
  $("#w-dxy").textContent = dxy ? nf(2).format(dxy.value) : "—";
  $("#w-dxy-sub").textContent = dxy ? `${dxy.strong ? "au-dessus de" : "sous"} sa moyenne 150 séances (${nf(2).format(dxy.sma)})` : "pas encore lu";
  $("#w-rate").textContent = r10 ? `${nf(2).format(r10.value)} %` : "—";
  $("#w-rate-sub").textContent = !r10 ? "pas encore lu"
    : [r10.change_60 == null ? "" : `${pt(r10.change_60)} en 60 séances`,
      m.curve == null ? "" : `courbe 10 ans − 3 mois ${pt(m.curve)}${m.curve < 0 ? " (inversée)" : ""}`].filter(Boolean).join(" · ");
}
// Levier (trendguard/levier.py) : étude refaite chaque semaine par le bot et
// verdict de sa règle fixée d'avance. Information seulement : il trade au
// comptant, sans emprunt.
function renderLeverage(l) {
  $("#w-lev-card").hidden = !l;
  if (!l) return;
  const years = (l.periods || []).map(([a, b]) => `${String(a).slice(0, 4)}-${String(b).slice(0, 4)}`);
  const cell = (p) => `${fpct(p.cagr_pct, 1)} par an · ${fpct(p.max_dd_pct, 1)} · ${nf(2).format(p.calmar)}`;
  const rows = (l.rows || []).map((r, i) => ({
    cells: [r.name, ...years.map((_y, k) => (r.periods[k] ? cell(r.periods[k]) : "–")),
      i === 0 ? "référence" : r.why ? `refusé : ${r.why}` : "permis"],
    cls: [null, ...years.map(() => null), i === 0 ? "" : r.why ? "down" : "up"],
  }));
  table($("#w-lev-table"), ["Politique", ...years.map((y) => `${y} : rendement · pire baisse · Calmar`), "Verdict de la règle"],
    rows, null, "Pas encore étudié : première étude dans la semaine.", false);
  $("#w-lev-verdict").textContent = l.text ? `Verdict du bot : ${l.text}.` : "Pas encore étudié : aucun levier.";
  $("#w-lev-sub").textContent = l.day
    ? `étude du ${fdate(l.day)} · emprunt compté à ${nf(0).format((l.rate || 0) * 100)} % par an` : "pas encore étudié";
}
// Bibliothèque de recherche (trendguard/bibliotheque.py) : pistes, jamais des règles.
function renderLibrary(b) {
  $("#w-lib-card").hidden = !b;
  if (!b) return;
  const rows = (b.pistes || []).map((p) => {
    const li = el("li", "ok");
    const a = el("a", "", p.title);
    if (/^https:\/\/arxiv\.org\/abs\//.test(p.link || "")) a.href = p.link;   // donnée lue : jamais une autre adresse
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.append(a, el("span", "sub", ` · ${fdate(p.published)} · ${p.themes.join(", ")}`),
      el("div", "sub", `${p.part} : ${p.question}`));
    if (p.verdict) {
      li.className = p.status === "a_etudier" ? "warn" : "ok";
      li.append(el("div", "sub", `Test du bot : ${p.verdict}`));
    }
    return li;
  });
  if (!rows.length) rows.push(el("li", "ok", b.fetched_at ? "Aucun travail récent ne touche aux règles du bot." : "Pas encore lue : première lecture avec le noyau de savoir."));
  if (b.error) rows.push(el("li", "warn", `Dernière lecture ratée : ${b.error} (nouvel essai dans 6 h).`));
  $("#w-lib").replaceChildren(...rows);
  $("#w-lib-sub").textContent = (b.fetched_at ? `${b.kept} travaux gardés · lue le ${fdate(b.fetched_at)}` : "pas encore lue")
    + (b.tested_on ? ` · pistes testées le ${fdate(b.tested_on)}` : "");
}
// Noyau de savoir : ce que le bot a lu, qui a raison, son avis.
const VERDICT = { fiable: ["up", "Fiable"], trompeuse: ["down", "Trompeuse (à lire à l'envers)"], hasard: ["", "Pas mieux que le hasard"], observation: ["", "En observation"] };
function renderKnowledge(k) {
  $("#w-know-card").hidden = !k;
  if (!k) return;
  const c = k.counts || {}, last = k.last_run || {}, b = k.bilan || {};
  $("#w-know-sub").textContent = `${nf(0).format(c.docs || 0)} connaissances · ${nf(1).format(c.mb || 0)} Mo`;
  const rows = [["ok", k.text]];
  const kinds = Object.entries(k.kinds || {}).map(([label, n]) => `${nf(0).format(n)} ${label}`);
  if (kinds.length) rows.push(["ok", `Lu aujourd'hui : ${kinds.join(", ")}.`]);
  const read = Object.entries(last.read || {}).map(([s, n]) => `${s} ${n}`);
  if (last.at) rows.push(["ok", `Dernière lecture : ${ftime(Date.parse(last.at) / 1000)} — ${read.join(", ") || "rien de nouveau"}.`]);
  Object.entries(last.errors || {}).forEach(([s, e]) => rows.push(["warn", `${s} : ${e} (nouvel essai à la prochaine lecture)`]));
  Object.entries(b.opinion || {}).forEach(([a, o]) => rows.push([o.value <= -0.5 ? "warn" : "ok", `Avis du bot sur ${up(a)} : ${nf(2).format(o.value)} (${o.sources.join(", ")})`]));
  const free = k.libre || {};
  $("#w-free").hidden = !free.text;
  if (free.text) {
    const items = [["ok", free.text + "."]];
    if (free.main_pct != null) items.push([free.pct >= free.main_pct ? "ok" : "warn", `Bot libre ${nf(1).format(free.pct)} % contre ${nf(1).format(free.main_pct)} % pour le bot principal, depuis leur départ.`]);
    (free.notes || []).forEach((n) => items.push(["ok", `Dernière décision : ${n}.`]));
    if ((free.top_sources || []).length) items.push(["ok", `Sources qu'il écoute le plus : ${free.top_sources.map(([s, w]) => `${s} (× ${nf(1).format(w)})`).join(", ")}.`]);
    $("#w-free-list").replaceChildren(...items.map(([cls, t]) => el("li", cls, t)));
  }
  const rec = b.record || {};
  if (rec.checked) rows.push(["ok", `Reports d'achat vérifiés : ${rec.checked}, utiles ${rec.helped} (la crypto a baissé) ; variation moyenne ${nf(1).format(rec.avg_pct)} %.`]);
  $("#w-know").replaceChildren(...rows.map(([cls, t]) => el("li", cls, t)));
  table($("#w-know-table"), ["Source", "Semaines vérifiées", "Réussite", "Hasard", "Verdict"],
    (b.scores || []).map((s) => {
      const v = VERDICT[s.verdict] || ["", s.verdict];
      const early = s.weeks < 5;                                    // trop tôt pour un taux
      return { cells: [s.source, `${s.weeks}${s.verdict === "observation" ? " / 20" : ""}`, early ? "–" : `${nf(0).format(s.rate * 100)} %`, early ? "–" : `${nf(0).format(s.chance * 100)} %`, v[1]],
        cls: [null, null, null, null, v[0]] };
    }), null, "Pas encore de bilan : il se fait à chaque décision quotidienne (00:02 UTC).");
}

register("watch", renderWatch, 60);
