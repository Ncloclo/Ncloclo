// Panneau TrendGuard : état du bot (lu toutes les 5 s), barre du haut,
// bouton AUTO et achat en temps réel (événement « tg:achat »).

import { $, api, fpx, fusd, ripple, toast, up } from "./core.js";

export let S = null, lastOk = 0, nextDecisionAt = 0, statusErr = false;
export async function refreshStatus() {
  try {
    S = await api("/api/status");
    lastOk = Date.now();
    statusErr = false;
    nextDecisionAt = Date.now() + S.next_decision_s * 1000;
    renderStatus();
    checkNewBuy();
  } catch (e) {
    statusErr = true;
    if (e.message !== "connexion requise") $("#updated").textContent = "Panneau injoignable";
  }
}
// Achat en temps réel : annonce, puis graphiques redessinés avec sa flèche.
let lastBuyKey = null;
function checkNewBuy() {
  const b = S.last_buy, key = b ? `${b.asset}|${b.date}` : "";
  if (lastBuyKey !== null && key && key !== lastBuyKey) {
    toast(`🟢 Achat en temps réel : ${up(b.asset)} à ${fpx(b.price)} USDT${b.cost ? ` (${fusd(b.cost)})` : ""}`, "ok");
    // Les pages concernées se redessinent (app.js) : l'état ne les connaît pas.
    document.dispatchEvent(new CustomEvent("tg:achat", { detail: b }));
  }
  lastBuyKey = key;
}
function renderStatus() {
  const pill = $("#st-pill"), btn = $("#auto-btn");
  const label = { running: "En marche", stopped: "Arrêté", starting: "Démarrage…", stopping: "Arrêt en cours…", restarting: "Relance automatique…" }[S.state] || S.state;
  pill.className = "pill " + (S.state === "running" ? "running" : S.state === "stopped" ? "stopped" : "pending");
  pill.querySelector("span").textContent = S.halted ? "Arrêt d'urgence" : label;
  const mode = $("#mode-badge");
  mode.textContent = S.demo ? "DÉMO" : S.mode === "live" ? (S.testnet ? "RÉEL · TESTNET" : "RÉEL") : "PAPER";
  mode.className = "badge " + (S.mode === "live" ? "live" : "paper");
  $("#brand-mode").textContent = S.demo ? "Démonstration" : S.mode === "live" ? "Mode réel" : "Mode paper (argent fictif)";
  const running = S.state === "running" || S.state === "restarting", busy = S.state === "starting" || S.state === "stopping";
  btn.disabled = busy;
  btn.classList.toggle("is-running", running);
  btn.querySelector("use").setAttribute("href", running ? "#i-stop" : "#i-play");
  $("#auto-label").textContent = running ? "ARRÊTER" : "AUTO";
  $("#auto-sub").textContent = busy ? label : S.state === "restarting" ? "Relance après une erreur" : running ? "Automatisation en marche" : "Démarrer l'automatisation";
  btn.setAttribute("aria-label", running ? "Arrêter l'automatisation du bot" : "Démarrer l'automatisation du bot");
  $("#logout-btn").hidden = !S.password;
  // Onglet Positions : nombre de positions ouvertes.
  const badge = $("#tab-pos");
  badge.textContent = String(S.positions || 0);
  badge.hidden = !S.positions;
  badge.title = `${S.positions || 0} position${S.positions > 1 ? "s" : ""} ouverte${S.positions > 1 ? "s" : ""}`;
}
function confirmLive() {
  const dlg = $("#confirm");
  return new Promise((resolve) => {
    const done = (v) => { dlg.close(); resolve(v); };
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}
$("#auto-btn").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  ripple(ev, btn);
  if (!S) return;
  const running = S.state === "running" || S.state === "restarting";
  if (!running && S.mode === "live" && !S.demo && !(await confirmLive())) return;
  btn.disabled = true;
  try {
    const r = await api(running ? "/api/bot/stop" : "/api/bot/start", { body: {} });
    toast(r.message, r.ok ? "ok" : "err");
  } catch (e) {
    toast(e.message, "err");
  }
  await refreshStatus();
});
