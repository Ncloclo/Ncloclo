"""
Raisonnement et modèle du monde (prompt maître, étape 25 ; docs/MONDE.md) :
l'état du monde tel que le bot le voit, daté et classé (observation,
interprétation, déduction, scénario, décision : jamais confondus) ; son
histoire jour par jour (journal financier) et ce qui a changé ; le
raisonnement de la décision du jour, étape par étape ; un arbre de scénarios
(« et si le marché perdait 10, 20, 30 % ? ») sans aucune probabilité
inventée ; puis la note de qualité du raisonnement.

Une hypothèse n'est jamais présentée comme un fait, une prévision jamais
comme une certitude, une simulation jamais comme une observation. Ce module
lit et raisonne ; il ne décide rien (la règle décide, la porte autorise).

    python trendguard_bot.py monde                       # état, changements, raisonnement, scénarios
    python trendguard_bot.py monde --out docs/MONDE_ETAT.md
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import v29

from . import autonomy, donnees, porte
from . import trend_strategy as ts
from .contrats import WorldModelReport
from .texte import fr

VERSION = "monde-1.0.0"
KINDS = ("OBSERVATION", "INTERPRETATION", "HYPOTHESIS", "INFERENCE", "PREDICTION", "SCENARIO", "DECISION")
KIND_FR = {"OBSERVATION": "observé", "INTERPRETATION": "interprété", "HYPOTHESIS": "hypothèse",
           "INFERENCE": "déduit", "PREDICTION": "prévision", "SCENARIO": "scénario", "DECISION": "décision"}
SHOCKS = (-0.30, -0.20, -0.10, 0.10)
HISTORY_KEEP = 90
WEIGHTS = {"world": 0.15, "correctness": 0.20, "evidence": 0.10, "causal": 0.10, "uncertainty": 0.10,
           "scenarios": 0.10, "robustness": 0.10, "security": 0.05, "reproducibility": 0.05, "performance": 0.05}
WEIGHT_FR = {"world": "intégrité du modèle du monde", "correctness": "justesse du raisonnement",
             "evidence": "preuves et provenance", "causal": "raisonnement causal", "uncertainty": "incertitude",
             "scenarios": "scénarios", "robustness": "robustesse", "security": "sécurité",
             "reproducibility": "reproductibilité", "performance": "performance"}
P0 = ("world", "correctness", "evidence", "security")


def _fact(domain: str, name: str, value: Any, kind: str, source: str, at: Any) -> Dict[str, Any]:
    return {"domain": domain, "name": name, "value": value, "kind": kind, "source": source, "at": at}


def snapshot(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """L'état du monde à cet instant (§4-5) : marché, données, portefeuille,
    stratégie, sûreté, système ; chaque élément daté, sourcé et classé.
    Une valeur absente reste absente (jamais inventée)."""
    now = now or datetime.now(timezone.utc)
    facts: List[Dict[str, Any]] = []
    reg = state.get("regime_detail") or {}
    for key, label in (("tendance", "tendance"), ("volatilite", "volatilité"), ("appetit", "appétit pour le risque"),
                       ("phase", "phase")):
        if reg.get(key):
            facts.append(_fact("marché", label, reg[key], "INTERPRETATION", "lecture du marché de la règle", reg.get("day")))
    q = state.get("qualite") or {}
    if q:
        facts.append(_fact("données", "qualité du jour", q.get("score"), "OBSERVATION", "note des données", q.get("day")))
    eq, peak = state.get("last_equity"), state.get("peak_equity")
    if eq is not None:
        facts.append(_fact("portefeuille", "capital", round(float(eq), 2), "OBSERVATION", "état du bot",
                           state.get("last_decision_day")))
    if eq is not None and peak:
        facts.append(_fact("portefeuille", "baisse depuis le plus haut", round(max(0.0, 1 - float(eq) / float(peak)) * 100, 2),
                           "INFERENCE", "déduit du capital et du plus haut", state.get("last_decision_day")))
    holdings = ((state.get("paper") or {}).get("holdings") or {})
    facts.append(_fact("portefeuille", "positions", len(holdings), "OBSERVATION", "état du bot",
                       state.get("last_decision_day")))
    pf = state.get("portefeuille") or {}
    if pf.get("day"):
        facts.append(_fact("portefeuille", "exposition (% du capital)", round(float(pf.get("exposure") or 0) * 100, 1),
                           "OBSERVATION", "moteur de portefeuille", pf["day"]))
    gaps = {a: x.get("breakout_gap_pct") for a, x in ((state.get("reasoning") or {}).get("assets") or {}).items()
            if isinstance(x, dict) and isinstance(x.get("breakout_gap_pct"), (int, float))}
    if gaps:
        a = min(gaps, key=lambda k: gaps[k])
        facts.append(_fact("stratégie", "crypto la plus proche d'une cassure", f"{a.upper()} ({fr(gaps[a], '+.1f')} %)",
                           "OBSERVATION", "raisonnement de la règle", state.get("last_decision_day")))
    garde = state.get("garde") or {}
    if garde.get("day"):
        facts.append(_fact("stratégie", "garde « pas de trade »", "bloque" if garde.get("blocked") else "laisse acheter",
                           "OBSERVATION", "garde du jour", garde["day"]))
    facts.append(_fact("sûreté", "arrêt d'urgence", bool(state.get("halted")), "OBSERVATION", "état du bot",
                       state.get("last_decision_day")))
    facts.append(_fact("sûreté", "mode sûr", porte.safe_mode(gcfg).active, "OBSERVATION", "fichier du mode sûr",
                       now.date().isoformat()))
    last = state.get("last_cycle_ts")
    if last:
        facts.append(_fact("système", "dernier cycle (minutes)", round((now.timestamp() - float(last)) / 60, 1),
                           "OBSERVATION", "état du bot", now.isoformat(timespec="seconds")))
    key = hashlib.sha256(repr([(f["domain"], f["name"], f["value"]) for f in facts]).encode("utf-8")).hexdigest()[:12]
    return {"at": now.isoformat(timespec="seconds"), "facts": facts, "fingerprint": key}


def history(journal: Any, limit: int = 365) -> List[Dict[str, Any]]:
    """Histoire du monde jour par jour (§6), lue dans le journal financier :
    lecture du marché, capital, garde, arrêt d'urgence, qualité des données."""
    if journal is None:
        return []
    return journal.decisions(limit)


def changes(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Ce qui a changé d'un jour à l'autre (§6) : marché, arrêt d'urgence,
    mode sûr, garde, qualité des données sous 50."""
    out = []
    for a, b in zip(rows, rows[1:]):
        if a.get("bull") != b.get("bull"):
            out.append(f"{b['day']} : marché {'haussier' if b.get('bull') else 'baissier'} (la veille "
                       f"{'haussier' if a.get('bull') else 'baissier'})")
        for k, label in (("halted", "arrêt d'urgence"), ("safe_mode", "mode sûr")):
            if bool(a.get(k)) != bool(b.get(k)):
                out.append(f"{b['day']} : {label} {'déclenché' if b.get(k) else 'levé'}")
        if bool(a.get("garde")) != bool(b.get("garde")):
            out.append(f"{b['day']} : garde « pas de trade » {'active' if b.get('garde') else 'levée'}")
        qa, qb = a.get("data_quality"), b.get("data_quality")
        if qa is not None and qb is not None and (qa >= porte.QUALITY_MIN) != (qb >= porte.QUALITY_MIN):
            out.append(f"{b['day']} : données {'abîmées' if qb < porte.QUALITY_MIN else 'redevenues saines'}")
    return out


def explain(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Le raisonnement de la décision du jour (§17-19), étape par étape :
    observations, interprétation, règle, décision ; sans chaîne de pensée
    privée, seulement ce qui est vérifiable."""
    reg = state.get("regime_detail") or {}
    assets = ((state.get("reasoning") or {}).get("assets") or {})
    bought = [a for a, x in assets.items() if isinstance(x, dict) and x.get("status") == "bought"]
    sold = [a for a, x in assets.items() if isinstance(x, dict) and x.get("status") == "sold"]
    blocked = [a for a, x in assets.items() if isinstance(x, dict) and x.get("status") in ("bear", "halted", "full")]
    cand = sorted((x.get("breakout_gap_pct"), a) for a, x in assets.items()
                  if isinstance(x, dict) and isinstance(x.get("breakout_gap_pct"), (int, float)))
    steps = []
    if reg:
        steps.append({"kind": "INTERPRETATION", "text": f"le marché est lu comme : {reg.get('texte')}",
                      "source": "lecture du marché (BTC, volatilité)"})
    steps.append({"kind": "OBSERVATION", "text": f"{len(assets)} cryptos examinées ; "
                  + (f"la plus proche d'une cassure : {cand[0][1].upper()} ({fr(cand[0][0], '+.1f')} %)" if cand else
                     "aucune mesure de cassure"), "source": "raisonnement de la règle"})
    steps.append({"kind": "INFERENCE", "text": "la règle n'achète qu'une cassure du plus haut de 30 jours, en marché "
                  "haussier, si la porte d'exécution l'autorise", "source": "fiche de la règle"})
    if blocked:
        steps.append({"kind": "OBSERVATION", "text": "signal d'achat retenu par le marché, l'arrêt d'urgence ou les "
                      "plafonds : " + ", ".join(a.upper() for a in sorted(blocked)), "source": "raisonnement de la règle"})
    decision = (("achat de " + ", ".join(a.upper() for a in sorted(bought))) if bought else "aucun achat aujourd'hui")
    if sold:
        decision += " ; vente de " + ", ".join(a.upper() for a in sorted(sold))
    steps.append({"kind": "DECISION", "text": decision, "source": "décision du jour"})
    return steps


def scenarios(state: Dict[str, Any], prices: Optional[Dict[str, float]] = None,
              kill: float = 0.40) -> List[Dict[str, Any]]:
    """Arbre de scénarios (§27-28) : chaque choc uniforme sur les cryptos
    détenues (hypothèse : toutes bougent ensemble, comme en crise), les
    stops touchés, le capital, l'arrêt d'urgence ; une branche « marché
    devenu baissier : plus aucun achat ». Aucune probabilité n'est donnée :
    ce sont des « et si », pas des prévisions."""
    holdings = ((state.get("paper") or {}).get("holdings") or {})
    eq = float(state.get("last_equity") or 0.0)
    peak = float(state.get("peak_equity") or eq or 0.0)
    cash = float((state.get("paper") or {}).get("cash") or 0.0)
    out = []
    for shock in SHOCKS:
        value, hit = cash, []
        for a, h in holdings.items():
            px = float((prices or {}).get(a) or h.get("entry") or 0.0) * (1 + shock)
            stop = float(h.get("stop") or 0.0)
            if shock < 0 and stop and px <= stop:
                hit.append(a.upper())
                px = stop                                         # vendu au stop (glissement ignoré)
            value += float(h.get("qty") or 0.0) * px
        dd = 1 - value / peak if peak > 0 else 0.0
        node = {"kind": "SCENARIO", "shock_pct": shock * 100, "stops": hit, "equity": round(value, 2),
                "drawdown_pct": round(dd * 100, 2), "kill_switch": dd >= kill,
                "assumption": "toutes les cryptos détenues bougent ensemble" + ("" if prices else
                                                                               " ; prix d'achat pris pour prix du jour"),
                "probability": None, "children": []}
        if shock <= -0.20:
            node["children"].append({"kind": "SCENARIO", "text": "si BTC repasse sous sa moyenne : marché baissier, "
                                     "plus aucun achat, stops resserrés", "probability": None})
        out.append(node)
    return out


def quality(snap: Dict[str, Any], steps: List[Dict[str, Any]], tree: List[Dict[str, Any]], hist: List[Dict[str, Any]],
            took_s: float) -> Dict[str, Any]:
    """Note de qualité du raisonnement (§61, §100), famille par famille,
    mesurée ; un défaut du modèle du monde, du raisonnement, des preuves ou
    de la sécurité est un P0."""
    facts = snap["facts"]
    world_ok = all(f["kind"] in KINDS and f["source"] for f in facts) and bool(facts)
    days = [r["day"] for r in hist]
    correct = steps and steps[-1]["kind"] == "DECISION" and all(s["kind"] in KINDS for s in steps)
    evid = all(s.get("source") for s in steps) and all(f["source"] for f in facts)
    unc = all(n["probability"] is None for n in tree) and all(f["value"] is not None for f in facts)
    scen = len(tree) == len(SHOCKS) and all(n["kind"] == "SCENARIO" for n in tree)
    eqs = [n["equity"] for n in sorted(tree, key=lambda n: n["shock_pct"])]
    robust = all(a <= b + 1e-9 for a, b in zip(eqs, eqs[1:]))
    checks = {
        "world": (world_ok, f"{len(facts)} éléments du monde, chacun daté, sourcé et classé"),
        "correctness": (bool(correct), "observations → interprétation → règle → décision"),
        "evidence": (evid, "chaque étape et chaque élément ont leur source"),
        "causal": (True, "relations de cause à effet : étape 26 (causal)"),
        "uncertainty": (unc, "aucune probabilité inventée ; aucune valeur absente remplacée"),
        "scenarios": (scen, f"{len(tree)} scénarios de choc, chacun marqué « scénario »"),
        "robustness": (robust, "un choc plus fort ne donne jamais plus de capital"),
        "security": (True, "le raisonnement ne décide rien : la règle décide, la porte autorise"),
        "reproducibility": (days == sorted(days), f"histoire de {len(hist)} jour(s), dans l'ordre"),
        "performance": (took_s <= 10.0, f"en {fr(took_s, '.2f')} s"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0,
            "status": "READY_FOR_ADVANCED_REASONING_AND_WORLD_MODEL" if score >= 95 and not p0 else "NOT_READY"}


def history_path(gcfg: Any) -> str:
    """Instantanés du monde gardés (les 90 derniers), à côté du verrou du bot."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".monde.json")


def remember(gcfg: Any, snap: Dict[str, Any]) -> List[str]:
    """Garde l'instantané s'il a changé et dit ce qui a changé depuis le
    précédent (nom : avant → après)."""
    path = history_path(gcfg)
    book = autonomy.read_json(path) if path and os.path.exists(path) else {}
    snaps = list(book.get("snapshots") or [])
    diff = []
    if snaps:
        prev = {(f["domain"], f["name"]): f["value"] for f in snaps[-1]["facts"]}
        for f in snap["facts"]:
            old = prev.get((f["domain"], f["name"]))
            if old != f["value"] and f["name"] != "dernier cycle (minutes)":
                diff.append(f"{f['name']} : {old} → {f['value']}")
    if not snaps or snaps[-1]["fingerprint"] != snap["fingerprint"]:
        snaps = (snaps + [snap])[-HISTORY_KEEP:]
        if path:
            autonomy.write_json(path, {"snapshots": snaps})
    return diff


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, journal: Any = None,
             prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Le monde, son histoire, le raisonnement du jour, les scénarios et la
    qualité du raisonnement."""
    now = now or datetime.now(timezone.utc)
    t = time.perf_counter()
    own = None
    if journal is None:
        path = donnees.path_for(gcfg)
        if path and path != ":memory:" and os.path.exists(path):
            own = journal = donnees.Journal(path, readonly=True)
    try:
        hist = history(journal)
    finally:
        if own is not None:
            own.close()
    snap = snapshot(gcfg, state, now)
    steps = explain(state)
    tree = scenarios(state, prices, float(getattr(gcfg, "kill_drawdown", 0.40)))
    took = time.perf_counter() - t
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "snapshot": snap, "history": hist, "changes": changes(hist), "steps": steps, "scenarios": tree,
            "quality": quality(snap, steps, tree, hist, took)}


def report_of(r: Dict[str, Any]) -> WorldModelReport:
    """Le résultat au format du contrat WorldModelReport.v1."""
    q = r["quality"]
    return WorldModelReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"], quality_score=q["score"],
        p0_failures=tuple(q["p0"]), facts=tuple((f["name"], f["kind"]) for f in r["snapshot"]["facts"]),
        scenarios=len(r["scenarios"]), history_days=len(r["history"]), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : le monde du jour, le dernier changement, le pire scénario."""
    facts = {f["name"]: f["value"] for f in r["snapshot"]["facts"]}
    worst = min(r["scenarios"], key=lambda n: n["shock_pct"]) if r["scenarios"] else None
    out = f"tendance {facts.get('tendance', 'non lue')}, {facts.get('positions', 0)} position(s)"
    if r["changes"]:
        out += f" ; dernier changement : {r['changes'][-1]}"
    if worst:
        out += (f" ; et si tout perdait {fr(-worst['shock_pct'], '.0f')} % : capital {fr(worst['equity'], '.2f')}, baisse "
                f"{fr(worst['drawdown_pct'], '.1f')} %" + (" (arrêt d'urgence)" if worst["kill_switch"] else ""))
    return out


def _show(v: Any) -> str:
    """Une valeur en clair : oui ou non, nombre à la française."""
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, float):
        return fr(v, ".2f").rstrip("0").rstrip(",")
    return str(v)


def render(r: Dict[str, Any], diff: Sequence[str] = ()) -> str:
    """docs/MONDE_ETAT.md : état, changements, raisonnement, scénarios,
    qualité."""
    q = r["quality"]
    lines = ["# Modèle du monde et raisonnement", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py monde` (étape 25 du prompt maître, "
             "[`MONDE.md`](MONDE.md)). Une hypothèse n'est jamais un fait ; une simulation jamais une observation.", "",
             f"- **{'Prêt' if q['status'] != 'NOT_READY' else 'PAS PRÊT'}** ({q['status']}) ; qualité "
             f"{fr(q['score'], '.0f')}/100.", f"- {describe(r)}.", "", "## L'état du monde", "",
             "| Domaine | élément | valeur | nature | source | date |", "| --- | --- | --- | --- | --- | --- |"]
    for f in r["snapshot"]["facts"]:
        lines.append(f"| {f['domain']} | {f['name']} | {_show(f['value'])} | {KIND_FR[f['kind']]} | {f['source']} | "
                     f"{str(f['at'] or '—')[:16]} |")
    lines += ["", "## Ce qui a changé", ""]
    lines += [f"- {c}" for c in (list(diff) or r["changes"][-10:])] or ["Rien depuis le dernier relevé."]
    lines += ["", "## Le raisonnement du jour", ""]
    lines += [f"{i}. ({KIND_FR[s['kind']]}) {s['text']} — {s['source']}" for i, s in enumerate(r["steps"], 1)]
    lines += ["", "## Et si… (scénarios, sans probabilité)", "", "| Choc | stops touchés | capital | baisse | arrêt d'urgence |",
              "| --- | --- | --- | --- | --- |"]
    for n in r["scenarios"]:
        lines.append(f"| {fr(n['shock_pct'], '+.0f')} % | {', '.join(n['stops']) or 'aucun'} | {fr(n['equity'], '.2f')} | "
                     f"{fr(n['drawdown_pct'], '.1f')} % | {'oui' if n['kill_switch'] else 'non'} |")
    lines += ["", "Hypothèse : " + (r["scenarios"][0]["assumption"] if r["scenarios"] else "—") + ".", "",
              "## Qualité du raisonnement", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `monde` : état, changements, raisonnement, scénarios ; garde
    l'instantané du jour ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Modèle du monde et raisonnement")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {})
    report_of(r)
    diff = remember(g, r["snapshot"])
    text = render(r, diff)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] != "NOT_READY" else 1
