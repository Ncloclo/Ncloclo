"""Centre de sécurité du panneau : état des protections en direct, sans
jamais afficher une clé ni un mot de passe (seulement leur présence).

Accès au panneau, clés et fichier des secrets, arrêt d'urgence, relance
automatique, disponibilité, alimentation, disque et mémoire du PC, évolution,
rapport quotidien, alertes. Les constats partagés avec le rapport quotidien
(alimentation, disque, mémoire) viennent de lui : mêmes seuils, mêmes mots.

`SecurityCenter` est la partie « sécurité » de `PanelApp` (server.py), qui en
hérite : elle lit son état (configuration, base du bot, contrôle, alertes).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import v29
from trendguard import evolution, report, report_health, report_security, uptime
from trendguard.config import exposed_fingerprints, is_exposed
from trendguard.texte import fr


class SecurityCenter:
    # Fournis par PanelApp.
    g: Any
    demo: bool
    data: Any
    control: Any
    hub: Any
    password: str
    loopback: bool

    @staticmethod
    def _check(label: str, ok: Optional[bool], detail: str) -> Dict[str, Any]:
        """Une ligne du centre de sécurité : ok = True (vert), False (à
        corriger) ou None (information)."""
        return {"label": label, "ok": ok, "detail": detail}

    def _access_checks(self) -> List[Dict[str, Any]]:
        """Accès au panneau et essais de mot de passe ratés."""
        now = time.time()
        with self._lock:
            fails = len([t for t in self._login_failed_total if now - t < 86400])
        blocked = sum(1 for ip in list(self._login_fails) if self.login_blocked(ip))
        if self.loopback:
            access = self._check("Accès au panneau", True, "ce PC uniquement")
        else:
            access = self._check("Accès au panneau", bool(self.password),
                                 "Wi-Fi, protégé par mot de passe" if self.password
                                 else "Wi-Fi SANS mot de passe")
        return [access, self._check(
            "Essais de mot de passe ratés (24 h)", fails == 0,
            f"{fails} essai(s) raté(s)" + (f", {blocked} adresse(s) bloquée(s) 5 min" if blocked else "")
            + " ; blocage automatique après 5 échecs")]

    def _key_checks(self) -> List[Dict[str, Any]]:
        """Mode, clés Binance (présence seulement) et fichier des secrets."""
        live = self.g.run_mode == "live"
        has_keys = bool(os.environ.get("BINANCE_API_KEY")) and bool(os.environ.get("BINANCE_API_SECRET"))
        if has_keys:
            keys = "enregistrées dans le fichier privé .env"
        else:
            keys = "absentes : le mode réel ne peut pas démarrer" if live else "absentes (normal en paper)"
        try:
            gi = os.path.join(v29.APP_DIR, ".gitignore")
            env_ok = ".env" in open(gi, encoding="utf-8").read().split()
        except OSError:
            env_ok = False
        return [
            self._check("Mode", None if live else True,
                        "RÉEL : de vrais ordres sont passés" if live else "paper : aucun argent réel en jeu"),
            self._check("Clés API Binance", has_keys or not live, keys),
            self._check("Droit de retrait de la clé", None,
                        "doit rester désactivé : python trendguard_bot.py verify le contrôle auprès de Binance"),
            self._exposed_check(),
            self._check("Fichier des secrets (.env)", True if env_ok else None, "privé, exclu de GitHub"),
        ]

    def _exposed_check(self) -> Dict[str, Any]:
        """Clé partagée par erreur : la clé en service est-elle l'une des clés
        exposées connues (empreintes, jamais les clés) ?"""
        label = "Clé partagée par erreur"
        if not exposed_fingerprints():
            return self._check(label, None, "une clé montrée dans une conversation ou une capture doit "
                                            "être supprimée sur Binance")
        if any(is_exposed(os.environ.get(n, "")) for n in ("BINANCE_API_KEY", "BINANCE_API_SECRET")):
            return self._check(label, False, "LA CLÉ EN SERVICE A ÉTÉ MONTRÉE dans une conversation : créez-"
                                             "en une neuve sur Binance, enregistrez-la avec set-keys, puis "
                                             "supprimez celle-ci")
        return self._check(label, True, "la clé en service n'a jamais été montrée ; les clés exposées sont "
                                        "retirées de ce PC (à supprimer aussi sur Binance si ce n'est pas "
                                        "fait) et refusées par set-keys")

    @staticmethod
    def _when(ts: Any) -> str:
        try:
            return time.strftime("%d/%m à %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError, OverflowError, OSError):
            return "?"

    def _alert_channels(self, st: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Canaux d'alerte et résultat de leur dernier envoi : envoi du bot
        (son état), rapport quotidien ou test depuis ce panneau, le plus
        récent l'emporte."""
        if self.hub is None:
            return []
        seen: Dict[str, Dict[str, Any]] = {}
        latest = report.load_latest(self.g) or {}
        sent = latest.get("delivery") or latest.get("last_delivery")
        for src in (st.get("alerts_last"), sent, getattr(self.hub, "last", None)):
            for name, r in (dict(src) if isinstance(src, dict) else {}).items():
                if isinstance(r, dict) and float(r.get("at") or 0) >= float((seen.get(name) or {}).get("at") or 0):
                    seen[name] = r
        return [dict(c, last=seen.get(c["name"])) for c in self.hub.status()]

    def _alerts_check(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """« Alertes » : à corriger si le dernier envoi d'un canal a échoué,
        vert si un envoi a réussi, information tant qu'aucun n'est connu."""
        chans = [c for c in self._alert_channels(st) if c.get("enabled")]
        if not chans:
            return self._check("Alertes", True if self.demo else False,
                               "démonstration" if self.demo
                               else "aucune : python trendguard_bot.py alerts configurer")
        parts, failed, sent = [], False, False
        for c in chans:
            r = c.get("last")
            if not r:
                parts.append(f"{c['label']} : aucun envoi connu, cliquez sur Tester (Réglages ▸ Alertes)")
            elif r.get("ok"):
                sent = True
                parts.append(f"{c['label']} : dernier envoi réussi le {self._when(r.get('at'))}")
            else:
                failed = True
                text = (f"{c['label']} : dernier envoi RATÉ le {self._when(r.get('at'))} "
                        f"({r.get('error') or 'cause inconnue'})")
                if r.get("cause") and r["cause"] != r.get("error"):
                    # Une coupure du réseau ne masque pas la vraie cause.
                    text += (f" ; cause à corriger, constatée le {self._when(r.get('cause_at'))} : "
                             f"{r['cause']}")
                parts.append(text)
        return self._check("Alertes", False if failed else True if sent else None, " ; ".join(parts))

    def _uptime_check(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """Temps de marche du bot sur 7 jours, hors arrêts demandés."""
        u = uptime.summary(st.get("uptime"), st.get("last_cycle_ts"), st.get("stopped_at"))
        pct = u["week_pct"]
        if pct is None:
            return self._check("Disponibilité du bot (7 j)", None, "mesurée à partir du prochain cycle du bot")
        detail = f"{pct:.0f} % du temps (hors arrêts demandés)"
        missed = [e for e in u["events"] if not e["requested"]]
        if missed:
            e = missed[0]
            detail += (f" ; dernier arrêt : {uptime.fdur(e['end'] - e['start'])} le "
                       f"{self._when(e['start'])}, {e['text']}")
        if pct < uptime.GOOD_PCT:
            # Veille déjà corrigée par le bot (rapport de la nuit) : le conseil
            # devient une bonne nouvelle, la mesure sur 7 jours remonte.
            fixed = any(c.get("label") == "Veille du PC (sur secteur)" and c.get("ok") is True
                        for s in (report.load_latest(self.g) or {}).get("sections") or []
                        for c in s.get("checks") or [])
            detail += (" ; veille et capot fermé désormais réglés pour laisser tourner le bot "
                       "quand le PC est branché : la mesure remonte jour après jour" if fixed else
                       " ; PC branché, mise en veille sur « Jamais » et capot fermé sur « Ne rien "
                       "faire » quand il est branché")
        return self._check("Disponibilité du bot (7 j)", pct >= uptime.GOOD_PCT, detail)

    def _from_report(self, checks: List[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Constats du rapport quotidien, en lignes du centre de sécurité :
        mêmes seuils, mêmes mots, la marche à suivre à la suite du constat."""
        rows = []
        for c in checks:
            if c:
                todo = c["reco"].rstrip(".")
                rows.append(self._check(c["label"], c["ok"], c["detail"]
                                        + (f" ; {todo[0].lower()}{todo[1:]}" if todo else "")))
        return rows

    def _power_check(self) -> List[Dict[str, Any]]:
        """Portable sur batterie : à dire tout de suite (rien sur un PC fixe)."""
        try:
            p = (self.control.autonomy() or {}).get("power")
        except Exception:
            p = None
        return self._from_report([report_security.battery_check(p)])

    def _resource_checks(self) -> List[Dict[str, Any]]:
        """Disque et mémoire du PC (rien en démonstration : ce n'est pas
        l'état d'un vrai bot)."""
        if self.demo:
            return []
        try:
            return self._from_report(report_health.resource_checks())
        except OSError:
            return []

    def _garde_check(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """Garde « NO TRADE » de la dernière décision (garde.py) : ce qu'elle
        a vérifié, et si elle a bloqué les achats (une protection, pas une
        panne)."""
        g = st.get("garde") or {}
        if not g.get("checks"):
            return self._check("Garde avant achat", None, "vérifiée à chaque décision de 00:02 UTC")
        if g.get("blocked"):
            return self._check("Garde avant achat", None, f"aucun achat le {g.get('day')} : "
                               + " ; ".join(g["blocked"]))
        return self._check("Garde avant achat", True, f"décision du {g.get('day')} : "
                           + ", ".join(c["label"].lower() for c in g["checks"]) + " conformes")

    def _crash_check(self) -> List[Dict[str, Any]]:
        """Plantages de Windows : le constat du dernier rapport quotidien
        (lire le journal de Windows à chaque actualisation serait trop lent)."""
        r = self.report_view()
        found = [c for s in r.get("sections") or [] for c in s.get("checks") or []
                 if c.get("label") == "Plantages de Windows"]
        return self._from_report(found[:1])

    def _report_check(self) -> Dict[str, Any]:
        """Rapport quotidien : date et verdict du dernier ; à corriger s'il a
        plus de 36 heures."""
        r = self.report_view()
        if not r.get("ready"):
            return self._check("Rapport quotidien", None, "le premier arrive à 00:30 UTC "
                                                          "(ou Réglages ▸ Générer maintenant)")
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(r["generated_at"])).total_seconds() / 3600
        except (KeyError, ValueError, TypeError):
            age_h = 0.0
        s = r.get("score") or {}
        detail = (f"dernier : {self._when(datetime.fromisoformat(r['generated_at']).timestamp())} "
                  f"UTC · {s.get('ok', 0)}/{s.get('total', 0)} conformes · {r.get('verdict', '')}")
        if age_h > 36:
            return self._check("Rapport quotidien", False, detail + " ; plus de 36 h : PC éteint à 00:30 ?")
        return self._check("Rapport quotidien", None, detail)

    def _evolution_check(self) -> Dict[str, Any]:
        """Évolution encadrée : niveau atteint, palier de risque, et rappel de
        ce qui reste hors de sa portée (information)."""
        ev = evolution.summary(self.g)
        if not ev["enabled"]:
            return self._check("Évolution encadrée", None, "désactivée : réglages fixes (TG_EVOLUTION=false)")
        r = ev["risk"]
        return self._check("Évolution encadrée", None,
                           f"niveau {ev['level']} sur {ev['levels']} ({ev['name']}) ; palier de risque "
                           f"{fr(r['pct'], 'g')} % par achat ({fr(r['max_pct'], 'g')} % au plus, selon "
                           "son analyse) ; ne touche jamais au nombre de positions, à l'arrêt d'urgence "
                           "ni au mode réel")

    def _runtime_checks(self, st: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Arrêt d'urgence, relance automatique, disponibilité, alertes,
        garde-fou."""
        if st.get("halted"):
            halt = f"déclenché : {st.get('halt_reason')}" + (
                f" ; {st['resume_note']}" if st.get("resume_note") else "")
        else:
            days = getattr(self.g, "kill_resume_days", 0)
            halt = (f"prêt, à −{self.g.kill_drawdown * 100:.0f} % depuis le plus haut"
                    + (f" ; reprise automatique après {days} jours si le marché redevient haussier"
                       if days > 0 else " ; levé seulement par la commande resume")
                    + (" ; profil prudent actif" if self.g.params.dd_throttle else ""))
        try:
            sup = self.control.autonomy().get("supervisor") or {}
        except Exception:
            sup = {}
        return [
            self._check("Arrêt d'urgence", not bool(st.get("halted")), halt),
            self._check("Relance automatique", bool(sup.get("running")),
                        "active" if sup.get("running") else "inactive : cliquez sur AUTO"),
            self._uptime_check(st),
            *self._power_check(),
            *self._resource_checks(),
            *self._crash_check(),
            self._garde_check(st),
            self._evolution_check(),
            self._report_check(),
            self._alerts_check(st),
            self._check("Garde-fou de Rachelle", True, "secrets masqués, demandes sensibles refusées"),
        ]

    def security_view(self) -> Dict[str, Any]:
        """Centre de sécurité : état des protections, sans jamais afficher
        une clé ni un mot de passe (seulement leur présence)."""
        checks = (self._access_checks() + self._key_checks()
                  + self._runtime_checks(self.data.state()))
        ok = sum(1 for c in checks if c["ok"] is True)
        warn = sum(1 for c in checks if c["ok"] is False)
        return {"checks": checks, "ok": ok, "warn": warn, "total": len(checks)}
