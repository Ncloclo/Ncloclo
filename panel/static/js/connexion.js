// Panneau TrendGuard : connexion quand un mot de passe protège le panneau.

import { $, api } from "./core.js";
import { refreshStatus } from "./etat.js";
import { refreshTab } from "./navigation.js";

export function showLogin() {
  $("#login").hidden = false;
  $("#login-pass").focus();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-err").textContent = "";
  try {
    const r = await api("/api/login", { body: { password: $("#login-pass").value } });
    if (r.ok) {
      $("#login").hidden = true;
      $("#login-pass").value = "";
      await refreshStatus();
      refreshTab(true);
    }
  } catch (err) {
    $("#login-err").textContent = err.message;
  }
});
