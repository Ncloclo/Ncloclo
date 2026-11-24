// Panneau TrendGuard, page « Journal » : dernières lignes du journal du bot,
// filtrées.

import { $, api, el, withLoader } from "../core.js";
import { register } from "../navigation.js";

async function renderLog() {
  const d = await api("/api/log?lines=500");
  const lvl = $("#log-level").value;
  const lines = d.lines.filter((l) => lvl === "all" ? true : lvl === "warn" ? /\[(WARNING|ERROR|CRITICAL)\]/.test(l) : /\[(ERROR|CRITICAL)\]/.test(l));
  const log = $("#log");
  log.replaceChildren(...(lines.length ? lines : ["Journal vide."]).map((l) => {
    const cls = /\[(ERROR|CRITICAL)\]/.test(l) ? "error" : /\[WARNING\]/.test(l) ? "warn" : /\[(DAILY|TRADE|ENTRY|VEILLE)\]/.test(l) ? "info-tag" : "";
    return el("li", cls, l);
  }));
  if ($("#log-follow").checked) log.scrollTop = log.scrollHeight;
}
$("#log-level").addEventListener("change", (e) => {
  withLoader(`Journal : ${e.currentTarget.selectedOptions[0].textContent}…`, () => renderLog().catch(() => {}));
});

register("log", renderLog, 5);
