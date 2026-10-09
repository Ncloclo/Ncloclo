// Panneau TrendGuard : Rachelle, la fenêtre de dialogue. Garde-fou côté
// navigateur : un secret collé ne quitte jamais la page.

import { $, api, el, reduceMotion, sleep, waitBase } from "./core.js";

// ---------- Assistant (fenêtre de dialogue) ----------
// Garde-fou côté navigateur : une clé, un mot de passe ou un code collés ne
// quittent jamais cette page (le serveur applique le même contrôle).
const CHAT = { history: [], loaded: false, busy: false };
const MASK_TEXT = "🔒 Je suis désolée, j'ai masqué votre message : il semblait contenir une information secrète (clé, mot de passe ou code). Il n'a pas été envoyé. Si c'était une vraie clé ou un vrai mot de passe, je vous conseille de le révoquer : sur Binance, « Gestion des API » → supprimer la clé, puis créez-en une nouvelle avec la saisie masquée.";
// Réflexion de Rachelle : au moins le temps réglé (3 s), plus si la question
// et la réponse sont longues (jusqu'à 3 s de plus).
const thinkMs = (q, a) => {
  const base = waitBase();
  if (!base) return 0;
  const words = q.trim().split(/\s+/).length;
  return base + Math.min(3000, words * 100 + (a || "").length * 1.2);
};
const greeting = () => (new Date().getHours() >= 18 || new Date().getHours() < 5 ? "Bonsoir" : "Bonjour");
const SECRET_RX = [
  /\bsk-[A-Za-z0-9_-]{16,}/,
  /\b(mot de passe|password|passwd|mdp|pin)\s*(est|=|:|is|c'est|c’est)\s*(?=\S*[\d!@#$%^&*_+=?])\S{4,}/i,
  /\b(code|2fa|a2f|otp)\b\D{0,20}\b\d{6}\b/i,
];
const CHAT_STOP = new Set("le la les de du des un une et pour mon ma mes je tu il que qui comment est sur dans au avec pas ne ce on en the to my how is and of for what you it in with do can a i bot panneau".split(" "));
function looksSecret(t) {
  if (SECRET_RX.some((rx) => rx.test(t))) return true;
  const long = t.split(/\s+/).map((w) => w.replace(/^["'`.,;:()[\]{}<>]+|["'`.,;:()[\]{}<>]+$/g, ""))
    .some((w) => w.length >= 32 && !w.includes("/") && !w.includes("www.") && /^[A-Za-z0-9_\-+=]+$/.test(w) && /\d/.test(w) && /[A-Za-z]/.test(w));
  if (long) return true;
  const words = t.trim().split(/\s+/);
  return [12, 15, 18, 21, 24].includes(words.length) && words.every((w) => /^[a-z]{3,8}$/.test(w)) && !words.some((w) => CHAT_STOP.has(w));
}
function inlineRich(node, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el("strong", "", part.slice(2, -2)));
    else if (part) node.append(document.createTextNode(part));
  });
  return node;
}
function richText(node, text) {
  let ul = null;
  text.split("\n").forEach((line) => {
    if (/^- /.test(line)) {
      if (!ul) { ul = el("ul"); node.append(ul); }
      ul.append(inlineRich(el("li"), line.slice(2)));
    } else {
      ul = null;
      if (line.trim()) node.append(inlineRich(el("p"), line));
    }
  });
}
function chatScroll() {
  const log = $("#chat-log");
  log.scrollTo({ top: log.scrollHeight, behavior: reduceMotion.matches ? "auto" : "smooth" });
}
// Voix (étape 21) : la réponse lue à voix haute par le navigateur, sur ce PC ;
// un second clic arrête la lecture.
function speak(text, btn) {
  if (!("speechSynthesis" in window)) return;
  if (window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
    btn.setAttribute("aria-pressed", "false");
    return;
  }
  const u = new SpeechSynthesisUtterance(text.replace(/[*_`#›]/g, ""));
  u.lang = "fr-FR";
  u.onend = () => btn.setAttribute("aria-pressed", "false");
  btn.setAttribute("aria-pressed", "true");
  window.speechSynthesis.speak(u);
}
function chatMessage(role, text, extra = {}) {
  const li = el("li", `msg ${role}${extra.refused ? " refused" : ""}`);
  const bubble = el("div", "bubble");
  richText(bubble, text);
  li.append(bubble);
  if (extra.actions && extra.actions.length) {
    const acts = el("div", "msg-actions");
    extra.actions.forEach((a) => {
      const b = el("button", "chip watch");
      b.type = "button";
      b.append(el("span", "", a.label + " ›"));
      b.addEventListener("click", () => {
        location.hash = a.href;
        if (matchMedia("(max-width: 860px)").matches) closeChat();
      });
      acts.append(b);
    });
    li.append(acts);
  }
  if (role === "bot" && extra.why && extra.why.length) {          // « Pourquoi ? » : d'où vient la réponse
    const why = el("details", "msg-why");
    why.append(el("summary", "", "Pourquoi ?"));
    const ul = el("ul");
    extra.why.forEach((w) => ul.append(el("li", "", w)));
    why.append(ul);
    li.append(why);
  }
  if (role === "bot" && !extra.refused && "speechSynthesis" in window) {
    const b = el("button", "chip speak", "🔊");
    b.type = "button";
    b.setAttribute("aria-label", "Lire la réponse à voix haute");
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => speak(text, b));
    li.append(b);
  }
  $("#chat-log").append(li);
  chatScroll();
  return li;
}
function chatSuggestions(list) {
  $("#chat-sugg").replaceChildren(...(list || []).map((q) => {
    const b = el("button", "chip", q);
    b.type = "button";
    b.addEventListener("click", () => sendChat(q));
    return b;
  }));
}
async function openChat() {
  const box = $("#chat");
  box.hidden = false;
  $("#chat-fab").setAttribute("aria-expanded", "true");
  if (!CHAT.loaded) {
    CHAT.loaded = true;
    try {
      const info = await api("/api/assistant");
      CHAT.info = info;
      chatMessage("bot", `${greeting()} ! ${info.welcome}`);
      chatSuggestions(info.suggestions);
    } catch (e) {
      CHAT.loaded = false;
      chatMessage("bot", `Je suis désolée, je ne peux pas vous répondre pour le moment (${e.message}). Réessayez dans un instant.`);
    }
  }
  $("#chat-input").focus();
}
export function closeChat() {
  $("#chat").hidden = true;
  $("#chat-fab").setAttribute("aria-expanded", "false");
  $("#chat-fab").focus();
}
async function sendChat(text) {
  text = (text || "").trim();
  if (!text || CHAT.busy) return;
  const input = $("#chat-input");
  input.value = "";
  input.style.height = "";
  const secret = looksSecret(text);                // rien n'est envoyé
  const userLi = chatMessage("user", secret ? "🔒 •••••• (message masqué)" : text, { refused: secret });
  const typing = el("li", "msg bot typing");
  typing.setAttribute("role", "status");
  const thinking = el("div", "bubble thinking");
  thinking.append(el("span", "spin"), el("span", "", "Rachelle réfléchit…"));
  typing.append(thinking);
  $("#chat-log").append(typing);
  chatScroll();
  CHAT.busy = true;
  $("#chat-send").disabled = true;
  const t0 = performance.now();
  const think = async (answer) => sleep(Math.max(0, thinkMs(secret ? "" : text, answer) - (performance.now() - t0)));
  if (secret) {
    await think("");
    typing.remove();
    chatMessage("bot", MASK_TEXT, { refused: true });
    CHAT.busy = false;
    $("#chat-send").disabled = false;
    return;
  }
  try {
    const r = await api("/api/assistant", { body: { message: text, history: CHAT.history.slice(-6) } });
    await think(r.answer);
    typing.remove();
    if (r.masked) {
      userLi.classList.add("refused");
      userLi.querySelector(".bubble").replaceChildren(el("p", "", "🔒 •••••• (message masqué)"));
    }
    chatMessage("bot", r.answer, { refused: r.refused, actions: r.actions, source: r.source, why: r.why });
    if (!r.refused) {
      CHAT.history.push({ role: "user", text }, { role: "assistant", text: r.answer });
      CHAT.history = CHAT.history.slice(-12);
    }
    if (r.suggestions && r.suggestions.length) chatSuggestions(r.suggestions);
  } catch (e) {
    await think("");
    typing.remove();
    chatMessage("bot", e.message === "connexion requise" ? "Votre session a expiré : reconnectez-vous au panneau, puis reposez-moi la question."
      : `Je suis désolée, je n'ai pas pu vous répondre (${e.message}). Réessayez dans un instant.`);
  } finally {
    CHAT.busy = false;
    $("#chat-send").disabled = false;
  }
}
$("#chat-fab").addEventListener("click", openChat);
$("#chat-close").addEventListener("click", closeChat);
$("#chat-clear").addEventListener("click", () => {
  CHAT.history = [];
  $("#chat-log").replaceChildren();
  if (CHAT.info) {
    chatMessage("bot", `${greeting()} ! ${CHAT.info.welcome}`);
    chatSuggestions(CHAT.info.suggestions);
  }
});
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("#chat-input").value); });
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(e.currentTarget.value); }
});
$("#chat-input").addEventListener("input", (e) => {
  const t = e.currentTarget;
  t.style.height = "";
  t.style.height = Math.min(t.scrollHeight, 120) + "px";
});
