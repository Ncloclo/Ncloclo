#!/usr/bin/env python3
"""
Alertes TrendGuard : Telegram, e-mail et WhatsApp.

Les notifications du bot (arrêt d'urgence, retrait officiel d'une crypto
détenue, alerte de la veille, décision bloquée…) partent sur Telegram comme
avant, et aussi par e-mail et WhatsApp selon ALERT_LEVEL :
  critical (défaut) : alertes critiques seulement ;
  all               : aussi le résumé quotidien et les autres messages.
L'envoi se fait dans un fil dédié : un serveur de messagerie lent ne
retarde jamais une action de trading, et une panne d'un canal n'empêche
pas les autres.

  python alerts.py configurer     # saisie guidée (mots de passe masqués)
  python alerts.py tester         # message de test sur chaque canal

Variables (.env) :
  E-mail   : SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = SSL), SMTP_USER,
             SMTP_PASSWORD, ALERT_EMAIL_TO
  WhatsApp : WHATSAPP_PROVIDER=callmebot (gratuit, usage personnel) avec
             WHATSAPP_PHONE et CALLMEBOT_APIKEY ; ou WHATSAPP_PROVIDER=twilio
             avec WHATSAPP_PHONE, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
             TWILIO_WHATSAPP_FROM
"""

from __future__ import annotations

import base64
import os
import queue
import smtplib
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29

WHATSAPP_MAX = 1500          # caractères par message WhatsApp


def _env(env: Dict[str, str], name: str, default: str = "") -> str:
    return (env.get(name) or default).strip()


# ══════════════════════════════════════════════════════════════════════
# CANAUX
# ══════════════════════════════════════════════════════════════════════

class EmailChannel:
    name, label = "email", "E-mail"

    def __init__(self, host: str, port: int, user: str, password: str, to: str,
                 smtp_factory: Optional[Callable[..., Any]] = None):
        self.host, self.port, self.user = host, port, user
        self.password, self.to = password, to
        self.smtp_factory = smtp_factory

    @classmethod
    def from_env(cls, env: Dict[str, str]) -> "EmailChannel":
        try:
            port = int(_env(env, "SMTP_PORT", "587"))
        except ValueError:
            port = 587
        return cls(_env(env, "SMTP_HOST"), port, _env(env, "SMTP_USER"),
                   env.get("SMTP_PASSWORD", ""), _env(env, "ALERT_EMAIL_TO"))

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.to)

    @property
    def secrets(self) -> List[str]:
        return [self.password]

    def send(self, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.user or self.to
        msg["To"] = self.to
        msg.set_content(text)
        context = ssl.create_default_context()
        if self.smtp_factory is not None:
            smtp = self.smtp_factory(self.host, self.port)
        elif self.port == 465:
            smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=20)
        with smtp:
            if self.port != 465:
                smtp.starttls(context=context)
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


class WhatsAppCallMeBot:
    """WhatsApp personnel gratuit (callmebot.com) : une fois, ajouter leur
    numéro à ses contacts et leur envoyer « I allow callmebot to send me
    messages » pour recevoir sa clé (CALLMEBOT_APIKEY)."""
    name, label = "whatsapp", "WhatsApp (CallMeBot)"

    def __init__(self, phone: str, apikey: str,
                 get: Optional[Callable[[str], Tuple[int, str]]] = None):
        self.phone, self.apikey, self._get = phone, apikey, get

    @property
    def enabled(self) -> bool:
        return bool(self.phone and self.apikey)

    @property
    def secrets(self) -> List[str]:
        return [self.apikey]

    def send(self, subject: str, text: str) -> None:
        body = f"{subject}\n{text}"[:WHATSAPP_MAX]
        url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": self.phone, "text": body, "apikey": self.apikey})
        status, reply = (self._get or _http_get)(url)
        if status != 200 or "error" in reply.lower()[:200]:
            raise RuntimeError(f"CallMeBot a refusé l'envoi (HTTP {status})")


class WhatsAppTwilio:
    """WhatsApp via Twilio (compte Twilio, numéro WhatsApp validé)."""
    name, label = "whatsapp", "WhatsApp (Twilio)"

    def __init__(self, phone: str, sid: str, token: str, sender: str,
                 post: Optional[Callable[..., Tuple[int, str]]] = None):
        self.phone, self.sid, self.token, self.sender = phone, sid, token, sender
        self._post = post

    @property
    def enabled(self) -> bool:
        return bool(self.phone and self.sid and self.token and self.sender)

    @property
    def secrets(self) -> List[str]:
        return [self.token]

    def send(self, subject: str, text: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        form = {"From": f"whatsapp:{self.sender}", "To": f"whatsapp:{self.phone}",
                "Body": f"{subject}\n{text}"[:WHATSAPP_MAX]}
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        status, _ = (self._post or _http_post_form)(url, form, auth)
        if status not in (200, 201):
            raise RuntimeError(f"Twilio a refusé l'envoi (HTTP {status})")


def _http_get(url: str) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "TrendGuard-alertes"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _http_post_form(url: str, form: Dict[str, str], basic_auth: str) -> Tuple[int, str]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Authorization": f"Basic {basic_auth}",
                                          "User-Agent": "TrendGuard-alertes"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def whatsapp_from_env(env: Dict[str, str]) -> Optional[Any]:
    provider = _env(env, "WHATSAPP_PROVIDER", "callmebot").lower()
    phone = _env(env, "WHATSAPP_PHONE")
    if provider == "twilio":
        return WhatsAppTwilio(phone, _env(env, "TWILIO_ACCOUNT_SID"),
                              env.get("TWILIO_AUTH_TOKEN", ""), _env(env, "TWILIO_WHATSAPP_FROM"))
    return WhatsAppCallMeBot(phone, env.get("CALLMEBOT_APIKEY", ""))


# ══════════════════════════════════════════════════════════════════════
# DIFFUSION
# ══════════════════════════════════════════════════════════════════════

class AlertHub:
    """Remplace v29.Notifier auprès du bot (même appel) : Telegram, puis
    e-mail et WhatsApp selon le niveau, dans un fil d'envoi dédié."""

    def __init__(self, telegram: Any, channels: List[Any], level: str = "critical",
                 logger: Any = None, async_mode: bool = True, dedup_sec: int = 600):
        self.telegram = telegram
        self.channels = [c for c in channels if c is not None and c.enabled]
        self.level = level if level in ("critical", "all") else "critical"
        self.logger = logger
        self.dedup_sec = dedup_sec
        self._sent: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._q: "queue.Queue" = queue.Queue(maxsize=200)
        self._thread: Optional[threading.Thread] = None
        if async_mode and self.channels:
            self._thread = threading.Thread(target=self._worker, name="alertes", daemon=True)
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.telegram, "enabled", False) or self.channels)

    def __call__(self, msg: str, dedup_key: Optional[str] = None,
                 critical: bool = False, sync: bool = False) -> bool:
        ok = self.telegram(msg, dedup_key=dedup_key, critical=critical, sync=sync) \
            if self.telegram is not None else True
        if self.channels and (critical or self.level == "all"):
            key = dedup_key or msg[:200]
            now = time.time()
            with self._lock:
                if now - self._sent.get(key, 0) < self.dedup_sec:
                    return ok
                self._sent[key] = now
            if self._thread is None or sync:
                self._deliver(msg, critical)
            else:
                try:
                    self._q.put_nowait((msg, critical))
                except queue.Full:
                    self._log("warning", "[ALERTES] file pleine, message ignoré")
        return ok

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            self._deliver(*item)

    def _deliver(self, msg: str, critical: bool) -> Dict[str, Optional[str]]:
        subject = ("🛑 TrendGuard : alerte critique" if critical
                   else "TrendGuard : notification")
        results: Dict[str, Optional[str]] = {}
        for ch in self.channels:
            try:
                ch.send(subject, msg)
                results[ch.name] = None
            except Exception as e:
                err = v29.scrub_secrets(f"{type(e).__name__}: {e}", ch.secrets)[:200]
                results[ch.name] = err
                self._log("warning", f"[ALERTES] {ch.label} : envoi impossible ({err})")
        return results

    def _log(self, level: str, text: str) -> None:
        if self.logger is not None:
            getattr(self.logger, level)(text)

    def status(self) -> List[Dict[str, Any]]:
        out = [{"name": "telegram", "label": "Telegram",
                "enabled": bool(getattr(self.telegram, "enabled", False))}]
        out += [{"name": c.name, "label": c.label, "enabled": True} for c in self.channels]
        return out

    def test(self, name: str) -> Tuple[bool, str]:
        """Message de test sur un canal (depuis le panneau ou la ligne de commande)."""
        text = ("Message de test de TrendGuard : les alertes arrivent bien sur ce canal. "
                "Aucune action n'est nécessaire.")
        if name == "telegram":
            if not getattr(self.telegram, "enabled", False):
                return False, "Telegram n'est pas configuré (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)"
            return bool(self.telegram(text, dedup_key=f"test-{time.time()}", sync=True)), ""
        for ch in self.channels:
            if ch.name == name:
                try:
                    ch.send("TrendGuard : test des alertes", text)
                    return True, ""
                except Exception as e:
                    return False, v29.scrub_secrets(f"{type(e).__name__}: {e}", ch.secrets)[:200]
        return False, f"{name} n'est pas configuré (python alerts.py configurer)"

    def close(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout)
            self._thread = None
        if self.telegram is not None and hasattr(self.telegram, "close"):
            self.telegram.close()


def build_notifier(logger: Any = None, env: Optional[Dict[str, str]] = None) -> AlertHub:
    env = os.environ if env is None else env
    telegram = v29.Notifier(_env(env, "TELEGRAM_TOKEN"), _env(env, "TELEGRAM_CHAT_ID"), logger=logger)
    return AlertHub(telegram, [EmailChannel.from_env(env), whatsapp_from_env(env)],
                    _env(env, "ALERT_LEVEL", "critical").lower(), logger)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def cmd_configure(env_path: Optional[str] = None, read: Callable[[str], str] = input,
                  secret: Optional[Callable[[str], str]] = None, out=None) -> int:
    """Saisie guidée ; les mots de passe et clés ne s'affichent pas."""
    import getpass
    import trendguard_bot as tg
    out = out or sys.stdout
    secret = secret or getpass.getpass
    path = env_path or tg.ENV_FILE
    say = lambda m="": print(m, file=out)       # noqa: E731
    values: Dict[str, str] = {}
    try:
        say("── Alertes par e-mail (laisser vide pour passer)")
        to = read("Adresse qui reçoit les alertes : ").strip()
        if to:
            values["ALERT_EMAIL_TO"] = to
            values["SMTP_HOST"] = read("Serveur SMTP [smtp.gmail.com] : ").strip() or "smtp.gmail.com"
            values["SMTP_PORT"] = read("Port [587] : ").strip() or "587"
            values["SMTP_USER"] = read(f"Identifiant SMTP [{to}] : ").strip() or to
            values["SMTP_PASSWORD"] = secret("Mot de passe SMTP (Gmail : mot de passe "
                                             "d'application) : ").strip()
        say("── Alertes WhatsApp (laisser vide pour passer)")
        phone = read("Numéro WhatsApp au format international (ex. +2250700000000) : ").strip()
        if phone:
            values["WHATSAPP_PHONE"] = phone
            provider = (read("Service : 1 = CallMeBot (gratuit), 2 = Twilio [1] : ").strip() or "1")
            if provider == "2":
                values["WHATSAPP_PROVIDER"] = "twilio"
                values["TWILIO_ACCOUNT_SID"] = read("Twilio Account SID : ").strip()
                values["TWILIO_AUTH_TOKEN"] = secret("Twilio Auth Token : ").strip()
                values["TWILIO_WHATSAPP_FROM"] = read("Numéro WhatsApp Twilio (ex. +14155238886) : ").strip()
            else:
                values["WHATSAPP_PROVIDER"] = "callmebot"
                say("   CallMeBot : ajoutez leur numéro à vos contacts et envoyez-leur "
                    "« I allow callmebot to send me messages » (voir callmebot.com).")
                values["CALLMEBOT_APIKEY"] = secret("Clé CallMeBot (apikey) : ").strip()
        level = read("Niveau : 1 = alertes critiques seulement, 2 = tout (dont le "
                     "résumé quotidien) [1] : ").strip()
        values["ALERT_LEVEL"] = "all" if level == "2" else "critical"
    except (EOFError, KeyboardInterrupt):
        say("\nAnnulé. Rien n'a été modifié.")
        return 1
    for k, v in values.items():
        tg.set_env_var(path, k, v)
        os.environ[k] = v
    say(f"✅ Réglages enregistrés dans {path}. Test : python alerts.py tester")
    return 0


def cmd_test(env: Optional[Dict[str, str]] = None, out=None) -> int:
    out = out or sys.stdout
    hub = build_notifier(env=env)
    ok_all = True
    for ch in hub.status():
        if not ch["enabled"]:
            print(f"  –  {ch['label']} : non configuré", file=out)
            continue
        ok, err = hub.test(ch["name"])
        ok_all &= ok
        print(f"  {'✓' if ok else '✗'} {ch['label']}" + ("" if ok else f" : {err}"), file=out)
    hub.close()
    return 0 if ok_all else 1


def main(argv: Optional[List[str]] = None) -> int:
    v29.ensure_utf8_stdio()
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "tester"
    if cmd == "configurer":
        return cmd_configure()
    if cmd == "tester":
        return cmd_test()
    print("Usage : python alerts.py configurer | tester")
    return 2


if __name__ == "__main__":
    sys.exit(main())
