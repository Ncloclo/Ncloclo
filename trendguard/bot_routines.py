"""Les routines du bot TrendGuard, autour de la décision quotidienne :
disponibilité (uptime.py) et alimentation du portable, évolution encadrée
(evolution.py), rapport quotidien (report.py), veille officielle
(market_watch.py), noyau de savoir (savoir.py), horloge, auto-diagnostic,
anticipation de la clôture, apprentissage libre (learning.py) et point de
situation du journal.

Partie de la classe TrendGuardBot (bot.py), qui en hérite.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import v29

from . import (
    anticipation,
    autonomy,
    comite,
    evenements,
    evolution,
    finance,
    learning,
    libre,
    modeles,
    report,
    savoir,
    systeme,
    uptime,
)
from . import diagnostics as dg
from . import market_watch as mw
from . import trend_strategy as ts
from .bot_types import last_closed_day
from .texte import fr


class RoutinesMixin:
    """Routines du bot (disponibilité, évolution, rapport, veille,
    anticipation, apprentissage) ; état et réglages : ceux de TrendGuardBot."""

    # ---------- Disponibilité (uptime.py) et alertes ----------

    def _note_downtime(self) -> None:
        """Fin d'un cycle réussi : un trou de plus d'une heure depuis le
        cycle réussi précédent est un arrêt (PC éteint ou en veille, bot
        figé, Internet coupé, arrêt demandé). Il est gardé pour le panneau
        et signalé s'il n'a pas été demandé. Première fois : les arrêts
        passés sont reconstitués d'après le journal."""
        now = time.time()
        last = self.state.get("last_cycle_ts")
        up = self.state.get("uptime")
        if not isinstance(up, dict):
            up = uptime.from_log(self.g.log_file, last) if self.g.log_file else {}
            up["since"] = up.get("since") or last or now
            up.setdefault("events", [])
            self.state["uptime"] = up
        if not last or now - float(last) <= uptime.GAP_SEC:
            return
        ev = {"start": float(last), "end": now,
              "cause": uptime.cause_of_gap(float(last), now, self._started_at,
                                           self._tries_since, self.state.get("stopped_at"))}
        uptime.add(up, ev, now)
        text = uptime.describe(ev)
        if ev["cause"] == uptime.USER:
            self.logger.info(f"[REPRISE] le bot a été {text}")
            return
        late = uptime.crossed_close(ev, self.g.decision_delay_sec)
        self.logger.warning(f"[REPRISE] le bot a été {text}"
                            + (" ; décision de clôture prise en retard" if late else ""))
        self.notifier(
            f"⚠️ TrendGuard a été {text}. Il a repris et a rattrapé les contrôles manqués"
            + (" ; la décision de la clôture quotidienne a été prise en retard" if late else "")
            + f". {uptime.ADVICE.get(ev['cause'], '')}".rstrip(),
            dedup_key=f"tg-downtime-{int(ev['start'])}", critical=True)

    # ---------- Évolution encadrée (evolution.py) ----------

    def _apply_evolution(self) -> None:
        """Réglages choisis par l'évolution encadrée, pris en compte au
        démarrage et juste avant la décision quotidienne, jamais en cours de
        journée : cassure, stops, lecture du marché, et palier de risque
        (1 à 2 × le risque par achat du .env, appliqué à la décision)."""
        if not self.g.evolution:
            return
        step = evolution.risk_step_for(self.g)
        if step != self.risk_step:
            self.logger.info(f"[PALIER] palier de risque en vigueur : "
                             f"{fr(self.g.params.risk_pct * step * 100, 'g')} % par achat")
            self.risk_step = step
        p = evolution.params_for(self.g)
        # Changement de configuration critique : une trace dans l'audit, une
        # seule fois par changement (pas à chaque redémarrage).
        now = {"palier": step, **{k: getattr(p, k) for k in evolution.SPACE if getattr(p, k) != getattr(self.g.params, k)}}
        before = self.state.get("reglages_audites")
        if before != now:
            if before is not None:
                self._audit("reglages.evolution", "strategie", "appliqué", before=before, after=now,
                            reason="réglages choisis par l'évolution encadrée (épreuves sur 8 ans)")
            self.state["reglages_audites"] = now
        if p == self.p:
            return
        changed = {k: getattr(p, k) for k in evolution.SPACE if getattr(p, k) != getattr(self.p, k)}
        self.logger.info(f"[ÉVOLUTION] réglages en vigueur : {evolution.describe(self.p, changed)}")
        self.p = p

    def _launch_evolution(self, day: str) -> None:
        """Routine quotidienne de l'évolution (épreuves), une fois par jour
        après la décision, dans un processus séparé : les stops restent
        surveillés pendant qu'elle calcule."""
        if not (self.g.evolution and self.track_uptime) or self.state.get("evolution_day") == day:
            return
        self.state["evolution_day"] = day
        peak, eq = self.state.get("peak_equity"), self.state.get("last_equity")
        storm = bool(self.state.get("halted")) or bool(
            peak and eq and float(eq) < float(peak) * (1 - evolution.STORM_DD))
        # Situation du bot pour le palier de risque : baisse depuis le plus
        # haut et marché lus à la décision qui vient d'être prise.
        facts = []
        if peak and eq and float(peak) > 0:
            facts += ["--baisse", f"{max(0.0, 1 - float(eq) / float(peak)):.4f}"]
        bull = self.state.get("last_regime_bull")
        if bull is not None:
            facts += ["--marche", "haussier" if bull else "baissier"]
        if autonomy.launch_tool(self.g, ["evolution", "quotidien"] + (["--tempete"] if storm else [])
                                + facts, ".evolution.log"):
            self.logger.info("[ÉVOLUTION] épreuves du jour lancées"
                             + (" (tempête : aucun changement permis)" if storm else ""))
        else:
            self.logger.warning("[ÉVOLUTION] épreuves du jour impossibles à lancer")

    EXPERT_MINUTE = 45          # diagnostic expert du jour : 00:45 UTC, après le rapport

    def _launch_expert(self, now: datetime) -> None:
        """Diagnostic expert du noyau cognitif (expert.py), une fois par
        jour, sans réseau, dans un processus séparé : lecture seule, il
        propose et n'agit jamais ; Rachelle en résume le résultat."""
        if not self.track_uptime:
            return
        today = now.date().isoformat()
        if self.state.get("expert_day") == today or now.hour * 60 + now.minute < self.EXPERT_MINUTE:
            return
        self.state["expert_day"] = today
        if autonomy.launch_tool(self.g, ["expert", "--rapide"], ".expert.log"):
            self.logger.info("[EXPERT] diagnostic expert du jour lancé (lecture seule)")
        else:
            self.logger.warning("[EXPERT] diagnostic expert du jour impossible à lancer")

    def _launch_report(self, now: datetime) -> None:
        """Rapport quotidien (report.py) à partir de 00:30 UTC, une fois par
        jour, dans un processus séparé ; rattrapé au retour du PC s'il était
        éteint à cette heure-là. Et un rapport aussitôt après une compétence
        acquise dont le rapport du jour n'a pas pu rendre compte."""
        if not (self.g.daily_report and self.track_uptime):
            return
        today = now.date().isoformat()
        self._report_after_skill(today)
        if self.state.get("report_day") == today or now.hour * 60 + now.minute < report.REPORT_MINUTE:
            return
        self.state["report_day"] = today
        try:
            if report.launch(self.g, "quotidien"):
                self.logger.info("[RAPPORT] analyse profonde, sécurité et rapport du jour lancés")
        except Exception as e:
            self.logger.warning(f"[RAPPORT] rapport du jour impossible : {e}")

    # ---------- Noyau de savoir (savoir.py) ----------

    def _launch_savoir(self) -> None:
        """Lecture d'Internet par le noyau de savoir toutes les
        TG_SAVOIR_MINUTES, dans un processus séparé : le bot surveille ses
        stops pendant ce temps, et une source en panne ne le gêne jamais."""
        if not (self.g.savoir and self.track_uptime):
            return
        last = float(self.state.get("savoir_launched_at") or 0.0)
        if time.time() - last < self.g.savoir_minutes * 60:
            return
        self.state["savoir_launched_at"] = time.time()
        held = ",".join(sorted(self._holdings()))
        if not autonomy.launch_tool(self.g, ["savoir", "collecter"] + (["--detenues", held] if held else []),
                                    ".savoir.log"):
            self.logger.warning("[SAVOIR] lecture d'Internet impossible à lancer")

    def _savoir_holds(self, day: str, close: Any) -> Dict[str, Dict[str, Any]]:
        """Bilan du noyau de savoir à la décision : sources jugées sur les
        cours réels, avis du bot, achats à reporter (sources prouvées
        seulement). Une panne ne bloque jamais la décision : aucun report."""
        if not self.g.savoir:
            return {}
        memory = None
        try:
            memory = savoir.Memory(self.g.savoir_db)
            res = savoir.judge(memory, close, day)
            counts = memory.counts(day)
        except Exception as e:
            self.logger.warning(f"[SAVOIR] bilan impossible : {e}")
            return {}
        finally:
            if memory is not None:
                memory.close()
        before = set((self.state.get("savoir") or {}).get("proven") or [])
        self.state["savoir"] = {"day": day, "line": savoir.reasoning_line(res, counts),
                                "proven": res["proven"], "holds": sorted(res["holds"]),
                                "influence": res["influence"], "opinion": res.get("opinion") or {}}
        for s in sorted(set(res["proven"]) - before):
            sc = next(x for x in res["scores"] if x["source"] == s)
            self.logger.info(f"[SAVOIR] compétence acquise : {s} est {sc['verdict']} "
                             f"({fr(sc['rate'] * 100, '.0f')} % de réussite contre "
                             f"{fr(sc['chance'] * 100, '.0f')} % pour le hasard, {sc['weeks']} semaines)")
        for s in sorted(before - set(res["proven"])):
            self.logger.info(f"[SAVOIR] {s} n'est plus prouvée : son avis ne compte plus")
        return res["holds"]

    def _libre_step(self, day: str, close: Any, snap: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        """La journée du bot libre (libre.py), à la décision : il apprend de
        la veille, révise ses règles, se fait son avis et agit, dans son
        propre portefeuille fictif gardé dans le noyau de savoir. Jamais
        bloquant pour le bot principal, jamais d'argent réel."""
        if not (self.g.savoir and self.g.libre):
            return
        memory = None
        try:
            memory = savoir.Memory(self.g.savoir_db)
            book = memory.get("libre")
            if not isinstance(book, dict) or book.get("capital") != float(self.g.paper_capital):
                book = libre.new(self.g.paper_capital, day)       # capital changé : nouvel essai
            today, yesterday = libre.last_views(memory, day)
            # Même filtre de liquidité que le bot principal (5 M$ par jour).
            liquid = None if snap is None else {
                a for a, s in snap.items()
                if ts._finite(s.get("vol30")) and s["vol30"] >= self.p.min_volume_usd}
            notes = libre.step(book, day, close, today, yesterday, liquid=liquid)
            memory.put("libre", book)
        except Exception as e:
            self.logger.warning(f"[LIBRE] journée du bot libre impossible : {e}")
            return
        finally:
            if memory is not None:
                memory.close()
        s = libre.summary(book, self.state.get("last_equity"), self.state.get("start_equity"))
        self.state.setdefault("savoir", {}).update(libre_day=day, libre_line=s["text"] + ".")
        if notes:
            self.logger.info("[LIBRE] " + " ; ".join(notes))

    COMMITTEE_MAX = 6           # cryptos examinées par le comité à chaque décision

    def _committee(self, day: str, close: Any, snap: Dict[str, Dict[str, float]], held: Dict[str, float],
                   equity: float, blocked: List[str], safe: bool, allowed: Any) -> None:
        """Avis consultatif du comité d'agents (comite.py) sur les cryptos
        que la règle propose d'acheter : gardé dans l'état, le raisonnement
        et le journal financier, pour mesurer avec le temps s'il aurait aidé.
        Il ne change aucune décision ; une panne ne bloque jamais le bot."""
        cands = sorted((a for a, s in snap.items() if a in allowed and ts.entry_signal(s, self.p)),
                       key=lambda a: -float(snap[a].get("mom") or 0))[:self.COMMITTEE_MAX]
        sv = self.state.get("savoir") or {}
        ranking = {r["asset"]: r for r in ((self.state.get("selection") or {}).get("ranking") or [])}
        rg = self.state.get("regime_detail") or {}
        q = self.state.get("qualite") or {}
        policy = {"halted": bool(self.state.get("halted")), "safe_mode": safe, "garde_blocked": list(blocked)}
        views = {}
        try:
            for a in cands:
                data = comite.board(a, day, close, snap[a], self.p, held, equity,
                                    float(self.state.get("risk_mult") or 1.0), policy,
                                    sv.get("opinion") if sv.get("day") == day else {}, ranking,
                                    regime=rg if rg.get("day") == day else None,
                                    quality=q if q.get("day") == day else None)
                view, _res = comite.evaluate(a, data, self.agents)
                views[a] = comite.as_dict(view)
        except Exception as e:           # consultatif : jamais bloquant
            self.logger.warning(f"[COMITÉ] avis impossible : {e}")
        self.state["comite"] = {"day": day, "views": views, "agents": self.agents.metrics()}
        if views:
            self.logger.info("[COMITÉ] " + " ; ".join(v["text"].split(" — ")[0] for v in views.values()))
            self._journal("record_committee", f"D-{day}", list(views.values()), comite.VERSION)

    def _finance(self, day: str, close: Any, feats: Dict[str, Any]) -> None:
        """Cœur d'intelligence financière (finance.py) à chaque décision :
        indicateurs versionnés, prévisions de fréquence et analyse de chaque
        crypto, gardés dans le journal financier ; prévisions arrivées à
        échéance comparées au résultat. Consultatif : aucune décision ne
        change ; une panne ne bloque jamais le bot."""
        if not self.g.finance:
            return
        try:
            c = close
            vol = getattr(self, "_last_volume", None)
            v = None if vol is None else vol.reindex(c.index)
            rg = self.state.get("regime_detail") or {}
            sv = self.state.get("savoir") or {}
            ev = self.state.get("evenements") or {}
            g_ = self.state.get("garde") or {}
            blocks = list(g_.get("blocked") or []) if g_.get("day") == day else []
            if self.state.get("halted"):
                blocks.append("arrêt d'urgence déclenché")
            calib = finance.calibration(self._journal("forecast_outcomes") or [])
            ctx = {"cross": finance.cross_asset(c, day), "regime": rg if rg.get("day") == day else None,
                   "sentiment": ({a: o.get("value") for a, o in (sv.get("opinion") or {}).items()}
                                 if sv.get("day") == day else {}),
                   "committee": (self.state.get("comite") or {}).get("views") or {}, "calibration": calib,
                   "events": [x.get("label") for x in (ev.get("upcoming") or [])] if ev.get("day") == day else [],
                   "policy_blocks": blocks, "vetoed": {a for a in (self.state.get("vetoes") or {}) if self._vetoed(a)},
                   "ohlcv_bad": getattr(self, "_ohlcv_bad", {})}
            frames, out, qualities = {}, {}, []
            due = (pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=finance.HORIZON)).strftime("%Y-%m-%d")
            for a in c.columns:
                frames[a] = finance.features(c[a], c["btc"] if "btc" in c else None, None if v is None else v[a])
                an = finance.analyze(a, c, v, day, self.p, dict(ctx, feature_frame=frames[a], strat=feats.get(a)))
                d = an.details
                qualities.append(d["quality"]["quality"])
                self._journal("record_finance", day, a, d["features"], finance.FEATURE_VERSION, d["data_cutoff_at"],
                              {"recommendation": an.recommendation, "reasons": an.reasons,
                               "opportunity": an.opportunity, "version": finance.ANALYSIS_VERSION},
                              d["forecast"], float(c[a].iloc[-1]) if pd.notna(c[a].iloc[-1]) else None, due)
                fc = d["forecast"] or {}
                out[a] = {"reco": an.recommendation, "reasons": list(an.reason_texts), "opportunity": an.opportunity,
                          "p_up": fc.get("p_up"), "ci": [fc.get("ci_low"), fc.get("ci_high")], "cases": fc.get("cases")}
            evaluated = 0
            for row in self._journal("due_forecasts", day) or []:
                at = pd.Timestamp(row["due_day"], tz="UTC")
                if row["asset"] in close.columns and at in close.index and pd.notna(close.loc[at, row["asset"]]):
                    actual = float(close.loc[at, row["asset"]]) / float(row["close"]) - 1
                    e = finance.evaluate_forecast(float(row["p_up"]), actual)
                    self._journal("record_forecast_evaluation", row["id"], actual, e["hit"], e["brier"])
                    evaluated += 1
            calib = finance.calibration(self._journal("forecast_outcomes") or [])
            complete = [x["opportunity"] is not None for x in out.values()]
            fis = finance.intelligence_score(qualities, calib, sum(complete) / len(complete) if complete else 0.0)
            top = sorted((a for a in out if out[a]["opportunity"] is not None),
                         key=lambda a: -out[a]["opportunity"])[:5]
            self.state["finance"] = {"day": day, "assets": out, "top": top, "fis": fis, "calibration": calib,
                                     "evaluated": evaluated, "drift": finance.drift(frames, day),
                                     "contagion": ctx["cross"].get("contagion"),
                                     "no_trade": sum(1 for x in out.values() if x["reco"] == "NO_TRADE"),
                                     "signals": sorted(a for a, x in out.items() if x["reco"] == "BUY_SIGNAL")}
        except Exception as e:           # consultatif : jamais bloquant
            self.logger.warning(f"[FINANCE] analyse impossible : {e}")

    def _events_day(self, day: str, close: Any, now: datetime) -> None:
        """Calendrier économique à la décision (evenements.py) : annonces
        américaines des 48 prochaines heures et réaction mesurée du bitcoin
        les jours d'annonce. Une information : rien n'est bloqué."""
        if not self.g.savoir:
            return
        memory = None
        try:
            memory = savoir.Memory(self.g.savoir_db)
            book = memory.get(evenements.KEY)
        except Exception as e:
            self.logger.warning(f"[CALENDRIER] lecture impossible : {e}")
            return
        finally:
            if memory is not None:
                memory.close()
        btc = close["btc"] if "btc" in close.columns else None
        self.state["evenements"] = evenements.day_view(book, btc, now, day)

    def _report_after_skill(self, today: str) -> None:
        """Compétence ou expérience acquise (réglage adopté, confirmé ou
        annulé, palier de risque changé) après le rapport du jour : analyse
        profonde et rapport envoyé aussitôt, une fois par changement. Avant
        le rapport de la nuit, c'est lui qui en rendra compte."""
        change = evolution.last_change(self.g)
        if not change or change["at"] <= (self.state.get("report_change_at") or ""):
            return
        if self.state.get("report_day") != today:
            return
        latest = report.load_latest(self.g) or {}
        if str(latest.get("generated_at") or "") >= change["at"]:
            self.state["report_change_at"] = change["at"]
            return
        try:
            if report.launch(self.g, "maintenant", motif=f"après une compétence acquise ({change['text']})"):
                self.state["report_change_at"] = change["at"]
                self.logger.info("[RAPPORT] compétence acquise : analyse profonde et rapport lancés")
        except Exception as e:
            self.logger.warning(f"[RAPPORT] rapport de la compétence acquise impossible : {e}")

    # ---------- Alimentation du portable ----------

    POWER_EVERY_SEC = 60        # alimentation relue une fois par minute (sans PowerShell)
    BATTERY_ALERT_SEC = 60      # sur batterie depuis une minute : alerte

    def _watch_power(self, now: Optional[float] = None) -> None:
        """Portable débranché : alerte (e-mail, WhatsApp) après une minute sur
        batterie, car Windows l'endort capot fermé ou après 10 minutes sans
        activité, et le bot s'arrête ; puis « chargeur rebranché ». Rien sur
        un PC fixe. Une alerte par débranchement, même après un redémarrage
        du bot (état gardé)."""
        now = time.time() if now is None else now
        if now - self._last_power < self.POWER_EVERY_SEC:
            return
        self._last_power = now
        p = systeme.power_source()
        if not p or p.get("battery_pct") is None:
            return
        watch = self.state.setdefault("power_watch", {})
        if p["ac"]:
            if watch.get("alerted"):
                self.logger.info("[ALIMENTATION] chargeur rebranché : le bot tourne sur secteur")
                self.notifier("✅ TrendGuard : chargeur rebranché, le bot tourne de nouveau sur secteur.",
                              dedup_key=f"tg-secteur-{int(watch.get('since') or now)}", critical=True)
            watch.clear()
            return
        since = float(watch.setdefault("since", now))
        if not watch.get("alerted") and now - since >= self.BATTERY_ALERT_SEC:
            watch["alerted"] = True
            pct = p["battery_pct"]
            self.logger.warning(f"[ALIMENTATION] PC sur batterie ({pct} %) : branchez le chargeur")
            self.notifier(f"🔌 TrendGuard : le PC est sur batterie ({pct} %). Branchez le chargeur : sur "
                          "batterie, Windows l'endort capot fermé ou après 10 minutes sans activité, "
                          "et le bot s'arrête.", dedup_key=f"tg-batterie-{int(since)}", critical=True)

    def _note_stop(self) -> None:
        """Arrêt propre (bouton ARRÊTER, Ctrl+C) : noté, pour que la reprise
        ne le compte pas comme une panne."""
        try:
            self.state["stopped_at"] = time.time()
            self._save_state()
        except Exception as e:
            self.logger.warning(f"[ARRÊT] état non enregistré : {e}")

    def _keep_alert_status(self) -> None:
        """Résultat du dernier envoi de chaque canal d'alerte (e-mail,
        WhatsApp), gardé pour le centre de sécurité du panneau."""
        last = getattr(self.notifier, "last", None)
        if isinstance(last, dict) and last:
            self.state["alerts_last"] = {k: dict(v) for k, v in list(last.items())}

    # ---------- Veille (market_watch.py) ----------

    def _vetoed(self, a: str) -> Optional[Dict[str, Any]]:
        """Veto officiel actif sur cette crypto (annonce de retrait Binance)."""
        v = (self.state.get("vetoes") or {}).get(a.lower())
        if v and v.get("until", "") >= self._now.date().isoformat():
            return v
        return None

    def _refresh_vetoes(self, now: datetime, max_age: float = 3600) -> None:
        """Annonces officielles de Binance, lues sans IA toutes les heures,
        et juste avant chaque décision si la dernière lecture a plus de
        10 minutes. Binance injoignable : les vetos précédents restent en
        place et la décision a lieu quand même."""
        if not self.g.watch or time.time() - self._last_veto_refresh < max_age:
            return
        self._last_veto_refresh = time.time()
        memory = None
        try:
            memory = mw.WatchMemory(self.g.watch_db)
            vetoes, monitoring = mw.refresh_official(
                [s.base.lower() for s in self.slots.values()], now, memory)
        except Exception as e:
            self.logger.warning(f"[VEILLE] annonces Binance indisponibles "
                                f"({mw.friendly_error(e)}) : vetos précédents conservés")
            return
        finally:
            if memory is not None:
                memory.close()
        held = set(self._holdings())
        for a, v in vetoes.items():
            if a not in (self.state.get("vetoes") or {}):
                warn = (" ; position DÉTENUE : le stop reste actif, vendre avant la date du "
                        "retrait est conseillé" if a in held else "")
                self.logger.warning(f"[VEILLE] {v['reason']} : nouveaux achats bloqués{warn} — {v['url']}")
                self.notifier(f"⚠️ TrendGuard : {v['reason']}, nouveaux achats bloqués{warn}.\n{v['url']}",
                              critical=a in held, dedup_key=f"tg-veto-{a}-{v['date']}")
        for m in monitoring:
            self.logger.info(f"[VEILLE] Binance place {', '.join(a.upper() for a in m['assets'])} "
                             f"sous surveillance (Monitoring Tag, {m['date']}) — {m['url']}")
        self.state["vetoes"] = vetoes

    def _daily_watch(self, now: datetime, day: str) -> None:
        """Rapport quotidien des IA (conseil seulement : aucun effet sur les
        ordres). Une panne des IA ou du réseau n'affecte jamais le trading."""
        if not (self.g.watch and self.g.watch_ai) or self.state.get("last_watch_day") == day:
            return
        self.state["last_watch_day"] = day
        self._save_state()
        memory = None
        trace = modeles.WatchTrace(modeles.ledger_path(self.g.watch_db))
        try:
            memory = mw.WatchMemory(self.g.watch_db)
            report = mw.daily_report([s.base.lower() for s in self.slots.values()],
                                     list(self._holdings()), now, memory,
                                     close=self._last_close, trace=trace)
        except Exception as e:
            self.logger.warning(f"[VEILLE] rapport impossible : {mw.friendly_error(e)}")
            return
        finally:
            trace.close()
            if memory is not None:
                memory.close()
        c = report["consensus"]
        self.state["last_watch"] = {
            "day": report["day"], "sentiment": c["sentiment"], "providers": c["providers"],
            "providers_total": len(report["providers"]),
            "disagreements": sorted(c.get("disagreements") or {}),
            "alerts": [a["text"] for a in report["alerts"][:6]]}
        self._save_state()
        self.logger.info("[VEILLE]\n" + mw.render(report))
        urgent = [a for a in report["alerts"] if a["level"] >= 2]
        if urgent:
            self.notifier("🔎 TrendGuard veille " + day + "\n" + "\n".join(
                f"• {a['text']}" for a in urgent[:6]),
                critical=any(a["level"] >= 3 for a in urgent), dedup_key=f"tg-veille-{day}")

    def _decision_deferred(self, day: str, why: str) -> None:
        """Décision reportée au cycle suivant ; alerte si cela dure plus
        d'une heure."""
        since = self.state.setdefault("decision_deferred_since", time.time())
        waited = time.time() - float(since)
        self.logger.warning(f"[DECISION] {day} reportée ({why}) — en attente "
                            f"depuis {waited / 60:.0f} min")
        if waited >= 3600 and not self.state.get("decision_deferred_notified") == day:
            self.state["decision_deferred_notified"] = day
            self.notifier(f"⚠️ TrendGuard : décision du {day} bloquée depuis "
                          f"{fr(waited / 3600, '.1f')} h ({why}). Vérifier la connexion "
                          f"à Binance.", critical=True)

    def _sync_clock(self, force: bool = False) -> None:
        """Met le bot à l'heure de Binance (au démarrage puis toutes les
        `clock_resync_min` minutes, en paper comme en réel) : décisions,
        clôture des bougies, dates, journaux et horodatage des ordres
        signés. Le PC peut dériver de plusieurs secondes par jour (service
        de temps Windows arrêté) ; au-delà de 10 s, Binance refuserait
        tous les ordres, y compris les stops (-1021)."""
        every = self.g.clock_resync_min * 60
        if every <= 0 or (not force
                          and time.time() - self._last_clock_sync < every):
            return
        self._last_clock_sync = time.time()
        sync = v29.sync_exchange_clock(self.exchange)
        if sync is None:
            if callable(getattr(self.exchange, "fetch_time", None)):
                self._last_clock_sync -= max(every - 300, 0)   # nouvel essai dans 5 min
                self.logger.warning("[CLOCK] heure de Binance indisponible (réseau) : "
                                    "dernier écart conservé, nouvel essai dans 5 min")
            return
        previous = self.state.get("clock_offset_ms")
        self.state["clock_offset_ms"] = round(sync.offset_ms)
        self.state["clock_uncertainty_ms"] = round(sync.uncertainty_ms)
        self.state["clock_synced_at"] = v29._utcnow_iso()
        if force or previous is None or abs(sync.offset_ms - float(previous)) >= 500:
            self.logger.info(f"[CLOCK] {v29.describe_clock(sync.offset_ms, sync.uncertainty_ms)}"
                             f" → le bot utilise l'heure de Binance")

    def holdings_for_diagnosis(self) -> List[Dict[str, Any]]:
        return [{"asset": a, "qty": h.qty, "entry": h.entry, "stop": h.stop,
                 "risk_quote": h.risk_quote} for a, h in self._holdings().items()]

    def _auto_diagnose(self, now: datetime, day: str) -> None:
        """Auto-diagnostic périodique (lecture seule) : données, marché,
        portefeuille, santé de la stratégie, réel vs attendu. Il alerte
        (journal + notification) mais ne modifie jamais la stratégie. Un
        échec du diagnostic n'affecte jamais le trading."""
        every = self.g.auto_diagnose_days
        last = self.state.get("last_auto_diag_day")
        if every <= 0 or (last and (pd.Timestamp(day) - pd.Timestamp(last)).days < every):
            return
        self.state["last_auto_diag_day"] = day
        self._save_state()
        try:
            findings = dg.run_diagnosis(
                self.exchange, self.p, [s.base for s in self.slots.values()],
                self.state, self.holdings_for_diagnosis(),
                float(self.state.get("last_equity") or 0.0), day, now,
                quote=self.g.quote, kill_drawdown=self.g.kill_drawdown,
                sections=("data", "market", "portfolio", "strategy", "live",
                          "alternatives", "watch"))
        except Exception as e:
            self.logger.warning(f"[DIAG] auto-diagnostic impossible : {e}")
            return
        v = dg.verdict(findings)
        self.state["last_auto_diag_verdict"] = v
        # Avantage de la stratégie disparu ou résultats réels incompatibles
        # avec l'historique : l'arrêt d'urgence ne se lèvera pas seul.
        self.state["edge_alert"] = any(f.level == "ALERTE" and f.section in ("Stratégie", "Réel vs attendu")
                                       for f in findings)
        self._save_state()
        self.logger.info("[DIAG]\n" + dg.render(findings, f"(auto, {day})"))
        if v in ("ATTENTION", "ALERTE"):
            points = [f"• {f.message}" for f in findings
                      if f.level in ("ATTENTION", "ALERTE")]
            self.notifier(f"{dg.ICONS[v]} TrendGuard auto-diagnostic {day} : {v}\n"
                          + "\n".join(points[:6]),
                          dedup_key=f"tg-diag-{day}", critical=(v == "ALERTE"))

    # ---------- Anticipation (vente ou achat probables ce soir) ----------

    ANTICIPATION_WINDOW_H = 3.0      # alerte dans les 3 h avant la clôture
    ANTICIPATION_EVERY_SEC = 900     # au plus un calcul toutes les 15 min
    ANTICIPATION_THRESHOLD = 0.6     # probabilité à partir de laquelle on prévient

    def holdings_view(self) -> List[Dict[str, Any]]:
        """Positions au format de l'anticipation (stop de clôture, stop
        catastrophe, risque initial, coût)."""
        out = []
        book = (self.state.get("paper") or {}).get("holdings") or {}
        for a, h in self._holdings().items():
            disaster = None
            if self.live:
                s = self.slots.get(a.upper())
                disaster = s.ctx.position.sl_price if s else None
            else:
                disaster = (book.get(a) or {}).get("disaster")
            out.append({"asset": a, "qty": h.qty, "entry": h.entry, "stop": h.stop,
                        "disaster": disaster, "risk": h.risk_quote, "cost": h.cost})
        return out

    def forecast(self, now: datetime, assets: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Anticipation au cours du moment : ventes et achats probables à la
        prochaine clôture, pour les positions, BTC et les candidats à moins de
        10 % de leur niveau d'achat (ou les cryptos `assets`). None avant la
        première décision."""
        basis = self.state.get("anticipation")
        if not basis:
            return None
        holdings = self.holdings_view()
        wanted = set(assets or []) | {h["asset"] for h in holdings} | {"btc"}
        if assets is None:
            # Candidats proches de leur niveau d'achat (moins de 10 %).
            wanted |= {a for a, b in (basis.get("assets") or {}).items()
                       if b.get("close") and b.get("buy_trigger")
                       and b["buy_trigger"] / b["close"] - 1 <= 0.10}
        prices: Dict[str, float] = {}
        for a in sorted(wanted):
            s = self.slots.get(a.upper())
            if s is None:
                continue
            try:
                prices[a] = float(s.ex.get_ticker()["last"])
            except Exception:
                continue
        return anticipation.forecast(
            basis, prices, holdings, now, self.p,
            float(self.state.get("last_equity") or self.g.paper_capital),
            float(self.state.get("risk_mult", 1.0) or 1.0), self.active_now(),
            (self.state.get("vetoes") or {}).keys(), bool(self.state.get("halted")),
            calibrate=learning.calibrator(self.state.get("learning")))

    # ---------- Apprentissage libre (learning.py) ----------

    BOOK_SAMPLE_EVERY_SEC = 3600
    BOOK_SAMPLE_FAST_SEC = 600         # tant qu'une normale reste à apprendre
    BOOK_SAMPLE_MAX_SEC = 20

    def _learning(self) -> Dict[str, Any]:
        self.state["learning"] = learning.ensure(self.state.get("learning"))
        return self.state["learning"]

    def _sample_books(self) -> None:
        """En marche continue : écart achat/vente et profondeur de chaque
        carnet, pour apprendre la normale de chaque crypto. Toutes les 10 min
        tant qu'une normale reste à apprendre (quelques heures), puis toutes
        les heures. Au plus 20 s ; une erreur réseau arrête le relevé."""
        L = self._learning()
        learned = all((L["books"].get(s.base.lower()) or {}).get("spread") is not None
                      for s in self.slots.values())
        every = self.BOOK_SAMPLE_EVERY_SEC if learned else self.BOOK_SAMPLE_FAST_SEC
        if not self.track_uptime or time.time() - self._last_book_sample < every:
            return
        self._last_book_sample = time.time()
        t0 = time.time()
        for s in list(self.slots.values()):
            fetch = getattr(s.ex.exchange, "fetch_order_book", None)
            if fetch is None or time.time() - t0 > self.BOOK_SAMPLE_MAX_SEC:
                return
            try:
                st = learning.book_stats(fetch(s.symbol, limit=100), self.BOOK_DEPTH_BAND)
            except Exception:
                return
            if st is not None:
                learning.observe_book(L, s.base.lower(), *st)

    def _learn_forecast(self, now: datetime) -> None:
        """Relève les probabilités du modèle 12, 6, 3 et 1 heure avant la
        clôture ; elles seront comparées à ce qui s'est passé."""
        basis = self.state.get("anticipation")
        if not (self.track_uptime and basis):
            return
        close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
        bucket = learning.forecast_bucket((close_at - now).total_seconds() / 3600)
        for_day = (pd.Timestamp(basis["next_close"]) - pd.Timedelta(days=1)).date().isoformat()
        L = self._learning()
        if bucket is None or learning.has_snapshot(L, for_day, bucket):
            return
        f = self.forecast(now)
        if f:
            learning.record_forecast(L, f, for_day, bucket)

    def _learn_close(self, day: str, snap: Dict[str, Dict[str, float]], bull: bool,
                     exits: List[Tuple[str, str]]) -> None:
        """À la décision : chaque prévision relevée pour cette clôture
        devient une leçon (vendu ? cassure ? marché baissier ?)."""
        sold = {a for a, reason in exits if reason == "STOP"}
        signals = {a for a, s in snap.items()
                   if ts._finite(s.get("close"), s.get("prior_high"), s.get("mom"))
                   and s["close"] > s["prior_high"] and s["mom"] > 0}
        n = learning.evaluate(self._learning(), day, sold, signals, not bull)
        if n:
            self.logger.info(f"[APPRENTISSAGE] {n} prévision(s) comparée(s) à la clôture du {day}")

    def _anticipate(self, now: datetime) -> None:
        """Dans les 3 h avant la clôture : prévient une fois par soir quand
        une vente ou un achat deviennent probables (les règles, elles, ne
        changent pas : c'est la clôture qui décide)."""
        basis = self.state.get("anticipation")
        if not self.g.anticipation_alerts or not basis:
            return
        close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
        hours = (close_at - now).total_seconds() / 3600
        if not 0 < hours <= self.ANTICIPATION_WINDOW_H:
            return
        if time.time() - self._last_anticipation < self.ANTICIPATION_EVERY_SEC:
            return
        self._last_anticipation = time.time()
        f = self.forecast(now)
        if not f:
            return
        sent = self.state.get("anticipation_sent") or {}
        keys = sent.get("keys", []) if sent.get("close") == basis["next_close"] else []
        for al in anticipation.alerts_to_send(f, keys, self.ANTICIPATION_THRESHOLD):
            self.logger.info(f"[ANTICIPATION] {al['text']}")
            self.notifier(al["text"], dedup_key=f"anticipation-{basis['next_close']}-{al['key']}")
            keys.append(al["key"])
        self.state["anticipation_sent"] = {"close": basis["next_close"], "keys": keys}

    def _heartbeat(self, now: datetime) -> None:
        """Une ligne de journal toutes les `heartbeat_min` minutes : le bot
        est visiblement vivant entre deux décisions quotidiennes."""
        every = self.g.heartbeat_min * 60
        if every <= 0 or time.time() - self._last_heartbeat < every:
            return
        self._last_heartbeat = time.time()
        try:
            holdings = self._holdings()
            prices: Dict[str, float] = {}
            for a in holdings:
                s = self.slots.get(a.upper())
                if s is not None:
                    prices[a] = s.ex.get_ticker()["last"]
            equity, cash = self._equity_and_cash(prices)
            self._log_equity(equity, cash)
            start = self.state.get("start_equity") or equity
            parts = [f"{a.upper()} {fr((prices.get(a, h.entry) / h.entry - 1) * 100, '+.1f')} %"
                     for a, h in sorted(holdings.items())]
            # Décision du jour à 00:00 UTC + délai : entre 00:00 et 00:02,
            # elle est encore à venir aujourd'hui, pas demain.
            nxt = (datetime.combine(now.date(), datetime.min.time(),
                                    tzinfo=now.tzinfo)
                   + timedelta(seconds=self.g.decision_delay_sec))
            if nxt <= now:
                nxt += timedelta(days=1)
            h_left, rem = divmod(int((nxt - now).total_seconds()), 3600)
            day = last_closed_day(now, self.g.decision_delay_sec)
            when = (f"décision du {day} en attente (nouvel essai à chaque cycle)"
                    if self.state.get("last_decision_day") != day
                    else f"prochaine décision dans {h_left} h {rem // 60:02d}")
            bull = self.state.get("last_regime_bull")
            regime = "?" if bull is None else ("HAUSSIER" if bull else "BAISSIER")
            self.logger.info(
                f"[HEARTBEAT] capital {fr(equity, ',.2f')} {self.g.quote} "
                f"({fr((equity / start - 1) * 100, '+.2f')} %) | régime BTC {regime} | "
                f"{len(holdings)} position(s)"
                + (f" : {', '.join(parts)}" if parts else "")
                + f" | {when}"
                + (f" | heure Binance ({v29.describe_clock(v29.clock_offset_ms())})"
                   if self.state.get("clock_synced_at") else "")
                + (" | 🛑 ARRÊT D'URGENCE" if self.state.get("halted") else ""))
        except Exception as e:
            self.logger.warning(f"[HEARTBEAT] indisponible : {e}")
