"""
Perception et observations multi-sources (prompt maître, étape 29 et ses
corrections ; docs/PERCEPTION.md) : ce que le bot perçoit du marché, source
par source (bougies du bot, caches de l'évolution et des études, carnets
d'ordres, horloge, calendrier, note des données), transformé en observations
datées, tracées et alignées dans le temps ; les sources qui parlent du même
fait le même jour sont comparées : confirmées, ou en conflit (la source de
plus haut rang l'emporte, jamais une moyenne) ; ce qui manque est dit
INCONNU, jamais deviné.

La perception observe ; elle ne décide pas : aucune dépendance directe vers
la politique, l'autorisation, la porte d'exécution ou les ordres (manifeste
des dépendances, vérifié par lecture du code).

    python trendguard_bot.py perception                  # observations, faits, conflits, dépendances
    python trendguard_bot.py perception --out docs/PERCEPTION_ETAT.md
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import pathlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

import v29

from . import trend_strategy as ts
from .contrats import ContractError, Observation, PerceptionReport
from .texte import fr, fr_plain

VERSION = "perception-1.0.0"
ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICE_TOLERANCE = 0.005
CLOCK_MAX_UNCERTAINTY_MS = 1000
CLOCK_MAX_AGE_H = 24
CANDLE_STALE_DAYS = 3
KEEP_DAYS = 3

# Sources de la perception : rang (1 = fait foi), modalité, d'où vient la
# donnée. Les deux caches et les bougies du bot viennent tous de Binance :
# ils se recoupent (même jour, même cours), ils ne sont pas indépendants.
SOURCES: Dict[str, Tuple[int, str, str]] = {
    "bougies du bot": (1, "SERIE", "état du bot : bougies de Binance de la dernière décision"),
    "cache de l'évolution": (1, "SERIE", "data_evolution : bougies de Binance de l'évolution encadrée"),
    "cache des études": (2, "SERIE", "data_binance : bougies de Binance des études"),
    "note des données": (1, "JUGEMENT", "état du bot : contrôle des bougies du jour"),
    "carnets d'ordres": (2, "CARNET", "état du bot : écart et profondeur mesurés au fil des jours"),
    "horloge": (1, "HORLOGE", "état du bot : écart avec l'heure de Binance"),
    "calendrier économique": (2, "EVENEMENT", "état du bot : annonces américaines des 48 heures"),
}
MODALITY_FR = {"SERIE": "cours journaliers", "JUGEMENT": "contrôle des données", "CARNET": "carnet d'ordres",
               "HORLOGE": "horloge", "EVENEMENT": "calendrier"}
ATTR_UNIT = {"clôture": "USDT", "écart achat-vente": "%", "note": "/100", "écart d'horloge": "ms",
             "annonces à venir": "nombre"}

# Manifeste des dépendances (corrections de l'étape 29) : le STATUT dit si
# la dépendance est obligatoire, le TYPE dit sa nature ; jamais l'un pour
# l'autre. Numéros des documents de la RACI (19 plan de contrôle, 20
# gouvernance des modèles, 21 cybersécurité, 22 interface, 23 recherche).
DEP_STATUSES = ("OBLIGATOIRE", "CONDITIONNELLE", "OPTIONNELLE", "ACTIVATION", "INTERDITE")
DEP_TYPES = ("DATA", "CONTRACT", "SECURITY", "GOVERNANCE", "MODEL", "RUNTIME", "DEPLOYMENT", "INTEGRATION",
             "CONSUMER", "PRODUCER")
MANIFEST: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    ("03", "trendguard/donnees.py", "OBLIGATOIRE", "DATA", "RUNTIME", ""),
    ("06", "trendguard/contrats.py", "OBLIGATOIRE", "CONTRACT", "GOVERNANCE", ""),
    ("20", "trendguard/apprentissage.py", "OBLIGATOIRE", "GOVERNANCE", "MODEL", ""),
    ("21", "trendguard/cyber.py", "OBLIGATOIRE", "SECURITY", "RUNTIME", ""),
    ("07", "trendguard/modeles.py", "CONDITIONNELLE", "MODEL", "RUNTIME", "LLM_FEATURE_ENABLED"),
    ("19", "trendguard/controle.py", "ACTIVATION", "DEPLOYMENT", "RUNTIME", "PRODUCTION_ACTIVATION"),
    ("22", "panel/interface.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("23", "trendguard/recherche.py", "OPTIONNELLE", "INTEGRATION", "PRODUCER", ""),
    ("24", "trendguard/memoire.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("25", "trendguard/monde.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("26", "trendguard/causal.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("27", "trendguard/objectifs.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("28", "trendguard/jumeau.py", "OPTIONNELLE", "INTEGRATION", "CONSUMER", ""),
    ("15", "trendguard/politique.py", "INTERDITE", "RUNTIME", "", ""),
    ("16", "trendguard/autorisation.py", "INTERDITE", "RUNTIME", "", ""),
    ("17", "trendguard/porte.py", "INTERDITE", "RUNTIME", "", ""),
    ("18", "trendguard/bot_execution.py", "INTERDITE", "RUNTIME", "", ""),
    ("18", "trendguard/deploiement.py", "INTERDITE", "RUNTIME", "", ""),
)
NETWORK = {"socket", "urllib", "requests", "http", "ccxt", "smtplib"}


def _obs(source: str, entity: str, attribute: str, value: Any, day: Optional[str], observed_at: Optional[str],
         provenance: str) -> Dict[str, Any]:
    """Une observation, validée par son contrat (Observation.v1) : source,
    modalité, rang, date du fait, provenance."""
    rank, modality, _what = SOURCES[source]
    oid = hashlib.sha256(f"{source}|{entity}|{attribute}|{day}|{observed_at}|{value}".encode()).hexdigest()[:12]
    o = Observation(observation_id=oid, source=source, modality=modality, entity=entity, attribute=attribute,
                    value=None if value is None else float(value), unit=ATTR_UNIT[attribute], day=day,
                    observed_at=observed_at, rank=rank, provenance=provenance)
    return o.as_dict()


def observe(state: Mapping[str, Any], caches: Mapping[str, Optional[pd.DataFrame]]) -> Tuple[List[Dict[str, Any]],
                                                                                           List[str]]:
    """Toutes les observations du moment, chacune avec sa source et sa date ;
    une donnée illisible est écartée et dite, jamais réparée."""
    out: List[Dict[str, Any]] = []
    refused: List[str] = []

    def add(*args: Any) -> None:
        try:
            out.append(_obs(*args))
        except (ContractError, TypeError, ValueError) as e:
            refused.append(f"{args[0]} / {args[1]} : {e}")

    an = state.get("anticipation") or {}
    for a, v in (an.get("assets") or {}).items():
        add("bougies du bot", a, "clôture", v.get("close"), an.get("day"), None, "état : anticipation.assets")
    frames = {s: df for s, df in caches.items() if df is not None and len(df)}
    common = None
    for df in frames.values():
        common = df.index if common is None else common.intersection(df.index)
    for source, df in frames.items():
        days = df.index[-KEEP_DAYS:]
        if common is not None and len(frames) > 1:
            days = days.union(common[-KEEP_DAYS:])         # les derniers jours communs : de quoi recouper
        for d, row in df.loc[days].iterrows():
            for a, px in row.items():
                if pd.notna(px):
                    add(source, a, "clôture", px, str(d.date()), None, f"{source} : {a}.csv")
    q = state.get("qualite") or {}
    if q.get("score") is not None:
        add("note des données", "bougies", "note", q.get("score"), q.get("day"), None, "état : qualite")
    for a, b in ((state.get("learning") or {}).get("books") or {}).items():
        if b.get("spread") is not None:
            add("carnets d'ordres", a, "écart achat-vente", float(b["spread"]) * 100, None, None,
                "état : learning.books (moyenne, sans date)")
    if state.get("clock_offset_ms") is not None:
        add("horloge", "pc", "écart d'horloge", state.get("clock_offset_ms"), None, state.get("clock_synced_at"),
            "état : clock_offset_ms")
    ev = state.get("evenements") or {}
    if ev.get("day"):
        add("calendrier économique", "marché", "annonces à venir", len(ev.get("upcoming") or []), ev.get("day"), None,
            "état : evenements")
    return out, refused


def align(obs: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Alignement dans le temps (§16) : une bougie du jour J n'est close
    qu'à J+1 0 h UTC ; une bougie pas encore close ou un fait daté après
    maintenant est refusé (FUTURE) ; sans date, il n'entre dans aucune
    comparaison (UNDATED) ; trop vieux, il est marqué (STALE)."""
    last_closed = (now - timedelta(days=1)).date().isoformat()
    for o in obs:
        if o["day"]:
            if o["day"] > last_closed:
                o["alignment"] = "FUTURE"
            elif (now.date() - datetime.fromisoformat(o["day"]).date()).days > CANDLE_STALE_DAYS:
                o["alignment"] = "STALE"
            else:
                o["alignment"] = "ALIGNED"
        elif o["observed_at"]:
            t = datetime.fromisoformat(o["observed_at"])
            t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            o["alignment"] = ("FUTURE" if t > now + timedelta(seconds=5) else
                              "STALE" if now - t > timedelta(hours=CLOCK_MAX_AGE_H) else "ALIGNED")
        else:
            o["alignment"] = "UNDATED"
    return obs


def fuse(obs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fusion (§18-19) : les observations datées du même fait le même jour
    sont regroupées ; d'accord à 0,5 % près : CONFIRMED ; en désaccord :
    CONFLICT, la source de plus haut rang l'emporte et l'autre valeur est
    gardée, jamais une moyenne ; seule : SINGLE. Les observations refusées
    (futur) n'entrent jamais."""
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for o in obs:
        if o.get("alignment") in ("ALIGNED", "STALE") and o["day"] and o["value"] is not None:
            groups.setdefault((o["entity"], o["attribute"], o["day"]), []).append(o)
    facts, conflicts = [], []
    order = list(SOURCES)
    for (entity, attribute, day), grp in sorted(groups.items()):
        grp = sorted(grp, key=lambda o: (o["rank"], order.index(o["source"])))
        best = grp[0]
        others = [o for o in grp[1:] if o["source"] != best["source"]]
        apart = [o for o in others if abs(o["value"] - best["value"]) > PRICE_TOLERANCE * abs(best["value"] or 1)]
        kind = "SINGLE" if not others else "CONFLICT" if apart else "CONFIRMED"
        fact = {"entity": entity, "attribute": attribute, "day": day, "value": best["value"], "state": kind,
                "source": best["source"], "sources": [o["observation_id"] for o in grp],
                "stale": best["alignment"] == "STALE",
                "confidence": ("haute" if kind == "CONFIRMED" and best["alignment"] == "ALIGNED" else
                               "basse" if kind == "CONFLICT" or best["alignment"] == "STALE" else "moyenne")}
        facts.append(fact)
        if apart:
            conflicts.append({"entity": entity, "attribute": attribute, "day": day, "kept": (best["source"], best["value"]),
                              "against": [(o["source"], o["value"]) for o in apart],
                              "gap_pct": round(max(abs(o["value"] / best["value"] - 1) for o in apart) * 100, 3)})
    return facts, conflicts


def current(facts: List[Dict[str, Any]], universe: Sequence[str]) -> List[Dict[str, Any]]:
    """Le cours de clôture le plus récent de chaque crypto suivie ; une
    crypto sans observation datée : INCONNU (aucune valeur inventée)."""
    out = []
    for a in universe:
        mine = [f for f in facts if f["entity"] == a and f["attribute"] == "clôture"]
        if mine:
            out.append(max(mine, key=lambda f: f["day"]))
        else:
            out.append({"entity": a, "attribute": "clôture", "day": None, "value": None, "state": "UNKNOWN",
                        "source": "", "sources": [], "stale": False, "confidence": "inconnue"})
    return out


def _imports(path: pathlib.Path) -> set:
    """Modules du dépôt importés par un fichier (lecture du code)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level and n.module:
                names.add(n.module.split(".")[0])
            elif n.level:
                names |= {a.name for a in n.names}
            elif n.module in ("trendguard", "panel"):
                names |= {a.name for a in n.names}
            elif n.module:
                names.add(n.module.split(".")[-1] if n.module.startswith(("trendguard.", "panel."))
                          else n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
    return names


def dependencies(root: pathlib.Path = ROOT, me: Optional[pathlib.Path] = None, llm: bool = False
                 ) -> Dict[str, Any]:
    """Le manifeste des dépendances vérifié (corrections de l'étape 29) :
    statut et type valides et distincts, dépendances obligatoires présentes,
    aucune dépendance interdite importée, aucun cycle (une dépendance
    obligatoire n'importe jamais la perception), aucune bibliothèque réseau."""
    me = me or pathlib.Path(__file__)
    mine = _imports(me)
    stem = me.stem
    rows, problems = [], []
    seen: Dict[str, str] = {}
    for step, f, status, typ, typ2, cond in MANIFEST:
        p = root / f
        mod = pathlib.Path(f).stem
        if status not in DEP_STATUSES or typ not in DEP_TYPES or (typ2 and typ2 not in DEP_TYPES):
            problems.append(f"{f} : statut ou type inconnu")
        if seen.get(f, status) != status:
            problems.append(f"{f} : deux statuts")
        seen[f] = status
        present = p.exists()
        imported = mod in mine
        cycle = present and status == "OBLIGATOIRE" and stem in _imports(p)
        if status == "OBLIGATOIRE" and not present:
            problems.append(f"{f} : dépendance obligatoire absente")
        if status == "INTERDITE" and imported:
            problems.append(f"{f} : dépendance interdite importée")
        if cycle:
            problems.append(f"{f} : cycle (importe la perception)")
        if status == "CONDITIONNELLE" and imported and not llm:
            problems.append(f"{f} : dépendance conditionnelle utilisée sans sa fonction ({cond})")
        rows.append({"step": step, "file": f, "status": status, "type": typ, "type2": typ2, "condition": cond,
                     "present": present, "imported": imported})
    net = sorted(mine & NETWORK)
    if net:
        problems.append("bibliothèque réseau : " + ", ".join(net))
    return {"rows": rows, "problems": problems, "ok": not problems,
            "forbidden": tuple(r["file"] for r in rows if r["status"] == "INTERDITE" and r["imported"])}


WEIGHTS = {"accuracy": 0.20, "fusion": 0.15, "provenance": 0.15, "alignment": 0.10, "uncertainty": 0.10,
           "robustness": 0.10, "security": 0.10, "performance": 0.05, "observability": 0.05}
WEIGHT_FR = {"accuracy": "justesse de la perception", "fusion": "fusion des sources", "provenance": "preuve et provenance",
             "alignment": "alignement dans le temps", "uncertainty": "incertitude dite", "robustness": "robustesse",
             "security": "sécurité et dépendances", "performance": "performance", "observability": "observabilité"}
P0 = ("provenance", "alignment", "security")


def band(score: float, p0: Sequence[str]) -> str:
    """Bandes de l'étape 29 (§53) ; un P0 : NOT_READY quelle que soit la note."""
    if p0:
        return "NOT_READY"
    return ("READY" if score >= 95 else "CANDIDATE" if score >= 90 else "VALIDATING" if score >= 80
            else "DEVELOPMENT" if score >= 70 else "REJECTED")


def quality(r: Dict[str, Any], took_s: float) -> Dict[str, Any]:
    """Note de préparation de la perception (§52-53), famille par famille."""
    obs, facts, cur, dep = r["observations"], r["facts"], r["current"], r["dependencies"]
    q = r["data_score"]
    multi = [f for f in facts if f["state"] != "SINGLE"]
    used_future = [f for f in facts for o in obs if o["observation_id"] in f["sources"] and o["alignment"] == "FUTURE"]
    clock = r["clock"]
    unsourced = [f for f in facts + cur if f["value"] is not None and not f["sources"]]
    checks = {
        "accuracy": (q is not None and q >= 90 and not r["conflicts"],
                     (f"note des bougies {fr_plain(q, 1)}/100" if q is not None else "note des bougies absente")
                     + f" ; {len(r['conflicts'])} conflit(s) entre sources"),
        "fusion": (bool(multi), f"{len(multi)} fait(s) vu(s) par plusieurs sources, la source de plus haut rang "
                                "l'emporte, jamais une moyenne" if multi else "aucun fait vu par deux sources"),
        "provenance": (not unsourced, f"{len(facts)} fait(s), chacun avec sa source ; {len(unsourced)} sans source"),
        "alignment": (not used_future and clock["ok"], f"aucune bougie non close utilisée ; horloge : {clock['text']}"
                      if not used_future else f"{len(used_future)} fait(s) tirés du futur"),
        "uncertainty": (all(f.get("confidence") for f in cur),
                        f"{sum(f['state'] == 'UNKNOWN' for f in cur)} crypto(s) INCONNUE(S), dites et jamais devinées"),
        "robustness": (r["robust"], "sans les bougies du bot : aucune valeur inventée, le reste tient"
                       if r["robust"] else "une source en moins fait inventer une valeur"),
        "security": (dep["ok"], "aucune dépendance interdite, aucun cycle, aucune bibliothèque réseau" if dep["ok"]
                     else "; ".join(dep["problems"])),
        "performance": (took_s <= 10, f"en {fr(took_s, '.2f')} s"),
        "observability": (all(o.get("observation_id") and o.get("provenance") for o in obs) and bool(obs),
                          f"{len(obs)} observation(s), chacune avec son identifiant et sa provenance"
                          + (f" ; {len(r['refused'])} écartée(s)" if r["refused"] else "")),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0, "status": band(score, p0),
            "unsourced": len(unsourced)}


def clock_check(state: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    """L'horloge du PC alignée sur Binance : écart mesuré depuis moins d'un
    jour, incertitude sous une seconde (le bot horodate à l'heure de
    Binance)."""
    at = state.get("clock_synced_at")
    unc = state.get("clock_uncertainty_ms")
    if not at or unc is None:
        return {"ok": False, "text": "jamais mesurée"}
    t = datetime.fromisoformat(str(at))
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    age_h = (now - t).total_seconds() / 3600
    ok = age_h <= CLOCK_MAX_AGE_H and float(unc) <= CLOCK_MAX_UNCERTAINTY_MS
    return {"ok": ok, "text": f"écart {fr_plain(float(state.get('clock_offset_ms') or 0), 0)} ms corrigé, incertitude "
                              f"{fr_plain(float(unc), 0)} ms, mesuré il y a {fr(max(age_h, 0), '.1f')} h"}


def evaluate(gcfg: Any, state: Mapping[str, Any], caches: Optional[Mapping[str, Optional[pd.DataFrame]]] = None,
             now: Optional[datetime] = None, root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """La perception du moment : observations, alignement, fusion, faits
    courants, conflits, injection de panne, dépendances, qualité."""
    now = now or datetime.now(timezone.utc)
    t = time.perf_counter()
    caches = dict(caches or {})
    obs, refused = observe(state, caches)
    align(obs, now)
    facts, conflicts = fuse(obs)
    universe = [a.lower() for a in getattr(gcfg, "universe", ()) or ()] or sorted(
        {o["entity"] for o in obs if o["attribute"] == "clôture"})
    cur = current(facts, universe)
    without_bot = [o for o in obs if o["source"] != "bougies du bot"]
    f2, _c2 = fuse(without_bot)
    cur2 = current(f2, universe)
    robust = all(f["value"] is None or f["sources"] for f in cur2) and all(f["source"] != "bougies du bot" for f in f2)
    q = (state.get("qualite") or {}).get("score")
    r: Dict[str, Any] = {"version": VERSION, "now": now.isoformat(timespec="seconds"),
                         "mode": getattr(gcfg, "run_mode", "paper"), "observations": obs, "refused": refused,
                         "facts": facts, "conflicts": conflicts, "current": cur, "robust": robust,
                         "clock": clock_check(state, now), "data_score": None if q is None else float(q),
                         "dependencies": dependencies(root),
                         "sources": {s: sum(o["source"] == s for o in obs) for s in SOURCES}}
    r["quality"] = quality(r, time.perf_counter() - t)
    return r


def report_of(r: Dict[str, Any]) -> PerceptionReport:
    """Le résultat au format du contrat PerceptionReport.v1."""
    q = r["quality"]
    return PerceptionReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                            readiness_score=q["score"], p0_failures=tuple(q["p0"]), observations=len(r["observations"]),
                            facts=len(r["facts"]), confirmed=sum(f["state"] == "CONFIRMED" for f in r["facts"]),
                            conflicts=len(r["conflicts"]), unknown=sum(f["state"] == "UNKNOWN" for f in r["current"]),
                            unsourced=q["unsourced"], forbidden_dependencies=r["dependencies"]["forbidden"],
                            engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : observations, faits confirmés, conflits, inconnus."""
    facts, cur = r["facts"], r["current"]
    days = sorted({f["day"] for f in cur if f["day"]})
    out = (f"{len(r['observations'])} observation(s) de {sum(1 for n in r['sources'].values() if n)} source(s) ; "
           f"{sum(f['state'] == 'CONFIRMED' for f in facts)} fait(s) confirmé(s) par deux sources, "
           f"{len(r['conflicts'])} conflit(s), {sum(f['state'] == 'UNKNOWN' for f in cur)} crypto(s) inconnue(s)")
    if days:
        out += f" ; cours le plus récent : bougie du {days[-1]}"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/PERCEPTION_ETAT.md : sources, faits, conflits, dépendances,
    qualité."""
    q = r["quality"]
    lines = ["# Perception et observations", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py perception` (étape 29 du prompt maître, "
             "[`PERCEPTION.md`](PERCEPTION.md)). La perception observe ; elle ne décide pas.", "",
             f"- **{q['status']}** ; note {fr(q['score'], '.0f')}/100.", f"- {describe(r)}."]
    lines += ["", "## Les sources", "", "| Source | rang | modalité | observations | d'où |", "| --- | --- | --- | --- | --- |"]
    for s, (rank, mod, what) in SOURCES.items():
        lines.append(f"| {s} | {rank} | {MODALITY_FR[mod]} | {r['sources'][s]} | {what} |")
    lines += ["", "Les trois séries de cours viennent de Binance : elles se recoupent (même jour, même cours), elles ne "
              "sont pas indépendantes. Rang 1 : fait foi en cas de désaccord."]
    al: Dict[str, int] = {}
    for o in r["observations"]:
        al[o["alignment"]] = al.get(o["alignment"], 0) + 1
    lines += ["", "Alignement : " + ", ".join(f"{k} {v}" for k, v in sorted(al.items())) + " (FUTURE : refusée ; "
              "UNDATED : jamais comparée ; STALE : marquée)." if al else "Aucune observation."]
    lines += ["", "## Le cours de chaque crypto (fait le plus récent)", "",
              "| Crypto | bougie | clôture | état | confiance | source |", "| --- | --- | --- | --- | --- | --- |"]
    for f in r["current"]:
        lines.append(f"| {f['entity'].upper()} | {f['day'] or '—'} | "
                     + (fr_plain(f["value"], 6) if f["value"] is not None else "INCONNU")
                     + f" | {f['state']} | {f['confidence']} | {f['source'] or '—'} |")
    lines += ["", "## Conflits entre sources", ""]
    if r["conflicts"]:
        lines += ["| Fait | jour | gardé | contre | écart |", "| --- | --- | --- | --- | --- |"]
        for c in r["conflicts"]:
            lines.append(f"| {c['entity'].upper()} {c['attribute']} | {c['day']} | {c['kept'][0]} "
                         f"({fr_plain(c['kept'][1], 6)}) | " + ", ".join(f"{s} ({fr_plain(v, 6)})" for s, v in c["against"])
                         + f" | {fr(c['gap_pct'], '.2f')} % |")
    else:
        lines.append("Aucun : les sources qui parlent du même jour sont d'accord à 0,5 % près.")
    lines += ["", "## Manifeste des dépendances (statut et type)", "",
              "| Étape | module | statut | type | condition | importé |", "| --- | --- | --- | --- | --- | --- |"]
    for d in r["dependencies"]["rows"]:
        lines.append(f"| {d['step']} | `{d['file']}` | {d['status']} | {d['type']}"
                     + (f" / {d['type2']}" if d["type2"] else "") + f" | {d['condition'] or '—'} | "
                     + ("oui" if d["imported"] else "non") + " |")
    if r["dependencies"]["problems"]:
        lines += [""] + [f"- **{p}**" for p in r["dependencies"]["problems"]]
    lines += ["", "## Qualité", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def load_caches(universe: Sequence[str]) -> Dict[str, Optional[pd.DataFrame]]:
    """Les deux caches de bougies (jamais retéléchargés ici)."""
    from .evolution import load_history
    out: Dict[str, Optional[pd.DataFrame]] = {}
    for source, folder in (("cache de l'évolution", "data_evolution"), ("cache des études", "data_binance")):
        try:
            out[source] = load_history(os.path.join(v29.APP_DIR, folder), list(universe), max_age_days=None)[0]
        except Exception:                # cache absent ou illisible : cette source manque, c'est dit
            out[source] = None
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `perception` : la perception du moment ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Perception et observations multi-sources")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {}, load_caches(g.universe))
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] == "READY" else 1
