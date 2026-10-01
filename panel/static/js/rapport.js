// Onglet Réglages : rapport de sécurité (en direct, et analyse de la nuit)
// et rapport quotidien (sécurité et diagnostic, 00:30 UTC et après chaque
// compétence acquise), avec sa fenêtre détaillée et son envoi.
import { $, api, el, toast } from "./core.js";

let REPORT = null;
let ALERTS = [];                       // canaux d'alerte (état du bot), pour l'envoi

// Une ligne ✓ / ! / i : centre de sécurité et rapport quotidien.
function checkRow(c) {
  const li = el("li", c.ok === true ? "ok" : c.ok === false ? "warn" : "info");
  li.append(el("span", "sec-ico", c.ok === true ? "✓" : c.ok === false ? "!" : "i"));
  const t = el("span");
  t.append(el("strong", "", c.label), el("span", "sub", " · " + c.detail));
  li.append(t);
  return li;
}
export async function renderSecurity() {
  const s = await api("/api/security");
  $("#s-sec-score").textContent = `${s.ok} / ${s.total}`;
  $("#s-sec").replaceChildren(...s.checks.map(checkRow));
}

const repWhen = (r) => new Date(r.generated_at).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
function deliveryText(r) {
  const names = { email: "e-mail", whatsapp: "WhatsApp", telegram: "Telegram" };
  const d = Object.entries(r.delivery || {});
  if (!d.length) return "pas encore envoyé";
  return d.map(([k, v]) => `${names[k] || k} ${v.ok ? "✓" : "✗"}`).join(" · ");
}
// Envoi du rapport, canal par canal : ✓ reçu, ✗ refusé, ou pas encore configuré.
function deliveryChips(r) {
  const configured = new Set(ALERTS.filter((c) => c.enabled).map((c) => c.name));
  const d = r.delivery || {};
  return [["email", "E-mail"], ["whatsapp", "WhatsApp"], ["telegram", "Telegram"]]
    .filter(([k]) => k !== "telegram" || configured.has(k) || d[k])
    .map(([k, label]) => {
      if (d[k]) return { k, label, ok: d[k].ok, text: `${label} ${d[k].ok ? "✓ reçu" : "✗ refusé"}` };
      if (configured.has(k)) return { k, label, ok: null, text: `${label} : au prochain rapport` };
      return { k, label, ok: false, text: `${label} : non configuré` };
    });
}
// Carte du rapport : note, verdict, envoi, trois premières recommandations,
// tendance et améliorations du code en attente. `alerts` : canaux d'alerte
// de l'état du bot (les derniers connus si absent).
export async function renderReport(alerts) {
  if (alerts) ALERTS = alerts;
  const r = await api("/api/report");
  REPORT = r.ready ? r : null;
  $("#s-rep-open").disabled = !REPORT;
  $("#s-rep-run").disabled = !!r.running;
  const verdict = $("#s-rep-verdict");
  if (!r.ready) {
    $("#s-rep-score").textContent = "—";
    verdict.className = "rep-verdict";
    verdict.textContent = r.running ? "Analyse en cours…" : "Premier rapport cette nuit à 00:30 UTC";
    $("#s-rep-when").textContent = "Ou tout de suite avec « Générer maintenant ». Envoyé par e-mail (complet) et WhatsApp (résumé).";
    [$("#s-rep-chips"), $("#s-rep-todo")].forEach((n) => n.replaceChildren());
    $("#s-rep-help").hidden = true;
    $("#s-rep-trend").textContent = "";
    return;
  }
  renderNightSecurity(r);
  $("#s-rep-score").textContent = `${r.score.ok} / ${r.score.total}`;
  verdict.className = "rep-verdict " + (r.score.warn ? "bad" : "good");
  verdict.textContent = r.score.warn ? `${r.score.warn} point(s) à corriger` : "✓ Tout est en ordre";
  $("#s-rep-when").textContent = `${repWhen(r)} UTC · ${r.score.ok} contrôles conformes sur ${r.score.total}${r.running ? " · nouvelle analyse en cours" : ""}`;
  const chips = deliveryChips(r);
  $("#s-rep-chips").replaceChildren(...chips.map((c) => el("li", "chip " + (c.ok === true ? "ok" : c.ok === false ? "bad" : ""), c.text)));
  // Le rapport ne vous parvient pas : on le dit, et on dit comment y remédier.
  const missing = chips.filter((c) => c.ok === false && c.k !== "telegram");
  const help = $("#s-rep-help");
  help.hidden = !missing.length;
  if (missing.length) {
    const how = { email: "mot de passe d'application Gmail (myaccount.google.com/apppasswords)", whatsapp: "WhatsApp gratuit avec CallMeBot (callmebot.com)" };
    help.replaceChildren(`Le rapport ne vous parvient pas encore par ${missing.map((c) => c.label).join(" ni par ")}. Pour le recevoir : lancez la tâche « Alertes — configurer » (ou `,
      el("code", "", "python trendguard_bot.py alerts configurer"),
      ") : " + missing.map((c) => how[c.k]).join(", puis ") + ".");
  }
  $("#s-rep-todo").replaceChildren(...r.recommendations.slice(0, 3).map((t) => el("li", "", t)));
  const trend = $("#s-rep-trend");
  const parts = [];
  const hist = (r.history || []).slice(-5);
  if (hist.length > 1) parts.push("Tendance : " + hist.map((h) => `${h.ok}/${h.total}`).join(" → "));
  const pending = r.proposals || [];
  trend.replaceChildren(parts.join(""));
  if (pending.length) {
    const url = (pending[0].split(" — ")[1] || "").trim();
    trend.append(parts.length ? " · " : "", `${pending.length} amélioration(s) du code attend(ent) votre validation `);
    if (/^https:\/\/github\.com\//.test(url)) {
      const a = el("a", "", "sur GitHub");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      trend.append(a);
    } else trend.append("sur GitHub");
    trend.append(" (installée seule la nuit suivante).");
  }
}
// Rapport de sécurité : la section « Sécurité » de la dernière analyse (secrets,
// clé Binance, sauvegarde, Windows…) et ce qui a changé depuis la précédente.
function renderNightSecurity(r) {
  const sec = (r.sections || []).find((s) => s.title === "Sécurité");
  $("#s-sec-night-box").hidden = !sec;
  if (!sec) return;
  const score = r.security ? ` · ${r.security.ok}/${r.security.total} conformes` : "";
  $("#s-sec-night-when").textContent = `· ${repWhen(r)} UTC${score}`;
  $("#s-sec-night").replaceChildren(...sec.checks.map(checkRow));
  $("#s-sec-changes").replaceChildren(...(r.changes || []).slice(0, 6).map((t) => el("li", "", t)));
}
// Fenêtre du rapport complet : pourquoi, changements, actions faites seul,
// recommandations, contrôles par section, propositions et règles de sagesse.
function openReport() {
  const r = REPORT;
  if (!r) return;
  $("#report-when").textContent = `${repWhen(r)} UTC · mode ${r.mode} · envoi : ${deliveryText(r)}`;
  const body = [];
  const verdict = el("p", "report-verdict " + (r.score.warn ? "bad" : "good"), `${r.verdict} : ${r.score.ok} contrôles conformes, ${r.score.warn} à corriger, ${r.score.info} informations, sur ${r.score.total}.`);
  body.push(verdict);
  if (r.motif) body.push(el("p", "sub", `Pourquoi ce rapport : ${r.motif}.`));
  const block = (title, node) => { const d = el("div"); d.append(el("h3", "", title), node); body.push(d); };
  const list = (items, ordered) => { const l = el(ordered ? "ol" : "ul", ordered ? "" : "plain"); items.forEach((t) => l.append(el("li", "", t))); return l; };
  if ((r.changes || []).length) block("Depuis le dernier rapport", list(r.changes, false));
  block("Ce que le bot a fait seul", list(r.actions.length ? r.actions : ["rien à corriger automatiquement"], false));
  block("À faire, par ordre d'importance", list(r.recommendations.length ? r.recommendations : ["rien : tout est en ordre"], true));
  r.sections.forEach((s) => { const ul = el("ul", "sec-list"); ul.append(...s.checks.map(checkRow)); block(s.title, ul); });
  if (r.proposals.length) block("Propositions d'amélioration à valider (GitHub)", list(r.proposals, false));
  block("Règles de sagesse", el("p", "sub", "Le bot applique seul les protections sûres et réversibles (sauvegarde, droits du fichier des secrets, secrets masqués dans les journaux). Il ne touche jamais aux règles, au risque, aux clés, au mode réel, au code ni aux réglages de Windows : ces points restent des recommandations. Aucun secret ne figure dans ce rapport."));
  $("#report-body").replaceChildren(...body);
  $("#report").showModal();
}
$("#s-rep-open").addEventListener("click", openReport);
$("#report-close").addEventListener("click", () => $("#report").close());
// Envoi du dernier rapport par e-mail (complet) et WhatsApp (résumé).
$("#s-sec-send").addEventListener("click", async (ev) => {
  const b = ev.currentTarget;
  b.disabled = true;
  try {
    const r = await api("/api/report/send", { body: {} });
    toast(r.message, r.ok ? "ok" : "err");
  } catch (e) {
    toast(e.message, "err");
  }
  setTimeout(() => { b.disabled = false; renderReport().catch(() => { /* réessai au prochain rafraîchissement */ }); }, 4000);
});
$("#s-rep-run").addEventListener("click", async (ev) => {
  const b = ev.currentTarget;
  b.disabled = true;
  try {
    const r = await api("/api/report/run", { body: {} });
    toast(r.message, r.ok ? "ok" : "err");
  } catch (e) {
    toast(e.message, "err");
  }
  renderReport().catch(() => { b.disabled = false; });
});
