// Panneau TrendGuard, page « Réglages » : autonomie, réglages du bot, alertes,
// téléphone, installation.

import { $, api, el, fdate, fdur, fmax2, ftime, nf, toast } from "../core.js";
import { refreshStatus, S } from "../etat.js";
import { register } from "../navigation.js";
import { showLogin } from "../connexion.js";
import { renderReport, renderSecurity } from "../rapport.js";

let installEvt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; $("#install-btn").hidden = false; });
$("#install-btn").addEventListener("click", async () => {
  if (!installEvt) return;
  installEvt.prompt();
  await installEvt.userChoice;
  installEvt = null;
  $("#install-btn").hidden = true;
});
// Évolution encadrée : niveau, réglages changés par le bot, essai en cours ;
// le compte rendu du dernier examen est replié dans « Détails ».
function evolutionText(ev) {
  if (!ev.enabled) return "désactivée : réglages fixes";
  const changes = ev.changes.length ? ev.changes.map((c) => `${c.param} ${c.from} → ${c.to}`).join(", ") : "réglages d'origine";
  const trial = ev.probation ? ` · en essai depuis le ${fdate(ev.probation.since)}` : "";
  return `niveau ${ev.level}/${ev.levels} · ${ev.name} · ${changes}${trial}`;
}
// Palier de risque : choisi par l'analyse du bot entre 1 et 2 fois le risque
// par achat du .env ; le compte rendu de la dernière analyse est dans « Détails ».
function riskStepText(S) {
  const r = (S.evolution || {}).risk;
  if (!r) return "désactivé : évolution encadrée arrêtée";
  const trial = r.probation ? ` · en essai depuis le ${fdate(r.probation.since)}` : "";
  return `${fmax2.format(r.pct)} % par achat · ${fmax2.format(r.max_pct)} % au plus, selon son analyse${trial}`;
}
// Lignes libellé / valeur : une valeur longue passe sous son libellé, sur
// toute la largeur ; un complément se replie dans « Détails ».
function dlRows(rows) {
  return rows.flatMap(([k, v, more]) => {
    const wide = more != null || String(v).length > 48;
    const dd = el("dd", wide ? "wide" : "", v);
    if (more) {
      const d = el("details");
      d.append(el("summary", "", "Détails"), el("p", "", more));
      dd.append(d);
    }
    return [el("dt", wide ? "wide" : "", k), dd];
  });
}
async function renderSettings() {
  if (!S) await refreshStatus();
  renderSecurity().catch(() => { /* réessai au prochain rafraîchissement */ });
  renderReport(S.alerts).catch(() => { /* réessai au prochain rafraîchissement */ });
  const au = S.autonomy || {}, sup = au.supervisor || {}, le = sup.last_exit;
  const ut = S.uptime || {}, gap = (ut.events || []).find((e) => !e.requested);
  const pct = (v) => (v == null ? "–" : nf(0).format(v) + " %");
  const sw = $("#s-autostart");
  sw.checked = !!au.autostart;
  sw.disabled = au.autostart == null;
  $("#s-auto-os").textContent = { windows: "Windows", macos: "macOS", linux: "Linux" }[au.os] || "";
  const autoRows = [
    ["Relance après une erreur", sup.running ? "active" : "inactive : cliquez sur AUTO"],
    ["Relances automatiques", sup.running ? String(sup.restarts || 0) : "–"],
    ["Dernier arrêt imprévu", le && le.code !== 0 ? `${fdate(le.at)} · ${le.stalled ? "bot bloqué" : "code " + le.code}` : "aucun"],
    ["Temps de marche (24 h · 7 j)", ut.tracked ? `${pct(ut.day_pct)} · ${pct(ut.week_pct)} (hors arrêts demandés)` : "mesuré dès le prochain cycle du bot"],
    ["Dernier arrêt non demandé (> 1 h)", gap ? `${ftime(gap.start)} → ${gap.ongoing ? "en cours" : ftime(gap.end)} (${fdur(gap.end - gap.start)}) · ${gap.text}` : "aucun"],
    ["Apprentissage libre", (S.learning && S.learning.text) || "premiers relevés dans l'heure"],
    ["Évolution encadrée", evolutionText(S.evolution || {}), (S.evolution || {}).last_text || null],
    ["Mise en veille du PC", au.keep_awake ? "bloquée tant que le bot tourne" : "autorisée"],
    ["Bouton ARRÊTER", "aucune relance, même au démarrage du PC"],
  ];
  $("#s-auto").replaceChildren(...dlRows(autoRows));
  const dl = $("#s-bot");
  const rows = [["Mode", S.demo ? "Démonstration" : S.mode === "live" ? (S.testnet ? "Réel (testnet)" : "Réel") : "Paper (argent fictif)"],
    ["Risque par achat", `${fmax2.format(S.risk_now_pct ?? S.risk_pct)} % à la dernière décision`], ["Positions au plus", String(S.max_positions)],
    ["Palier de risque", riskStepText(S), ((S.evolution || {}).risk || {}).last_text || null],
    ["Risque cumulé au plus", `${nf(0).format(S.max_total_risk_pct)} %`],
    ["Profil prudent", S.dd_throttle.length ? S.dd_throttle.map(([t, m]) => `risque × ${nf(1).format(m)} au-delà de ${nf(0).format(t * 100)} % de baisse`).join(" ; ") : "désactivé"],
    ["Arrêt d'urgence", `baisse de ${nf(0).format(S.kill_drawdown_pct)} %` + (S.kill_resume_days > 0 ? ` · reprise automatique après ${S.kill_resume_days} jours si le marché redevient haussier` : " · levé par la commande resume")],
    ["Cryptos suivies", String(S.universe.length)]];
  dl.replaceChildren(...dlRows(rows));
  const list = $("#s-alerts");
  const chans = S.alerts.length ? S.alerts : [{ name: "demo", label: "Aucun canal (démonstration)", enabled: false }];
  list.replaceChildren(...chans.map((c) => {
    const li = el("li");
    const b = el("button", "btn small ghost", "Tester");
    b.type = "button";
    b.disabled = !c.enabled;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        const r = await api("/api/alerts/test", { body: { channel: c.name } });
        toast(`${c.label} : ${r.message}`, r.ok ? "ok" : "err");
        await refreshStatus();
        await renderSettings();
      } catch (e) { toast(e.message, "err"); }
      b.disabled = false;
    });
    const t = el("span"), r = c.last;
    const state = !c.enabled ? "non configuré" : !r ? "configuré" : r.ok ? `dernier envoi réussi le ${ftime(r.at)}` : `dernier envoi RATÉ le ${ftime(r.at)}`;
    t.append(el("strong", "", c.label), el("span", "sub" + (r && !r.ok ? " down" : ""), " · " + state));
    if (r && !r.ok) {                  // la cause, repliée : la liste reste lisible
      const d = el("details");
      d.append(el("summary", "", "Pourquoi et comment corriger"), el("p", "", r.error || "cause inconnue"));
      t.append(d);
    }
    li.append(t, b);
    return li;
  }));
  const phone = $("#s-phone");
  const parts = [];
  if (S.lan_urls.length) {
    parts.push(el("p", "", "Sur votre téléphone connecté au même Wi-Fi, ouvrez :"));
    S.lan_urls.forEach((u) => parts.push(el("p", "", u)));
  } else {
    parts.push(el("p", "sub", "Accès depuis un téléphone (même Wi-Fi) : ajoutez PANEL_PASSWORD=… dans .env, puis lancez « python trendguard_bot.py panel --host 0.0.0.0 ». L'adresse à ouvrir s'affichera ici."));
  }
  parts.push(el("p", "sub", "Android : menu ⋮ puis « Ajouter à l'écran d'accueil ». iPhone : Partager puis « Sur l'écran d'accueil ». Le panneau s'ouvre alors comme une application."));
  phone.replaceChildren(...parts);
}
$("#s-autostart").addEventListener("change", async (e) => {
  const sw = e.currentTarget;
  sw.disabled = true;
  try {
    const r = await api("/api/autostart", { body: { enabled: sw.checked } });
    toast(r.message, r.ok ? "ok" : "err");
    if (!r.ok) sw.checked = !sw.checked;
  } catch (err) {
    toast(err.message, "err");
    sw.checked = !sw.checked;
  }
  sw.disabled = false;
  await refreshStatus();
});
$("#logout-btn").addEventListener("click", async () => {
  try { await api("/api/logout", { body: {} }); } catch { /* déjà déconnecté */ }
  showLogin();
});

register("settings", renderSettings, 30);
