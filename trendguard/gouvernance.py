"""
Gouvernance de l'architecture (documents transverses du prompt maître :
RACI normalisée, matrice des autorités et ordre officiel, critères de sortie
et frontières contractuelles ; docs/GOUVERNANCE.md) : qui possède quoi (un
seul responsable par responsabilité critique), qui peut observer,
recommander, bloquer, autoriser, exécuter ; une seule source de vérité par
donnée critique ; les frontières entre modules vérifiées par lecture du code ;
la barrière risque → porte → validation finale → ordre sans raccourci ; dix
portes de qualité (PASS, FAIL ou WAIVED) et la note de préparation.

Lecture seule : ce module mesure et dit, il ne change rien.

    python trendguard_bot.py gouvernance                 # RACI, autorités, frontières, portes de qualité
    python trendguard_bot.py gouvernance --out docs/GOUVERNANCE_ETAT.md
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import v29

from . import audit, chantiers, contrats, controle, cyber, jumeau, perception, porte_examen, uptime
from . import trend_strategy as ts
from .contrats import GovernanceReport
from .texte import fr

VERSION = "gouvernance-1.0.0"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OWNER = "vous"

PLANS = {"A": "Fondation", "B": "Données et connaissances", "C": "Perception et cognition",
         "D": "Intelligence du monde", "E": "Planification", "F": "Intelligence métier", "G": "Sûreté et contrôle",
         "H": "Opérations réelles", "I": "Apprentissage", "J": "Sécurité", "K": "Interface humaine"}

# RACI normalisée : (plan, responsabilité, A, R, C, I). A : exactement un
# responsable (un module, ou vous) ; R : au moins un qui fait le travail.
RACI: Tuple[Tuple[str, str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("A", "Stockage canonique (base, journal financier)", "trendguard/donnees.py", ("trendguard/donnees.py",),
     ("trendguard/controle.py",), ("trendguard/report_health.py",)),
    ("A", "Contrats de données", "trendguard/contrats.py", ("trendguard/contrats.py",), (), ("trendguard/chantiers.py",)),
    ("A", "Journal d'audit", "trendguard/audit.py", ("trendguard/audit.py",), ("trendguard/cyber.py",), (OWNER,)),
    ("B", "Qualité des données", "trendguard/qualite.py", ("trendguard/qualite.py", "trendguard/garde.py"),
     ("trendguard/perception.py",), ("trendguard/report_health.py",)),
    ("B", "Recherche externe et vérification des sources", "trendguard/recherche.py",
     ("trendguard/recherche.py", "trendguard/savoir.py", "trendguard/market_watch.py"), ("trendguard/modeles.py",), ()),
    ("B", "Mémoire durable", "trendguard/memoire.py", ("trendguard/memoire.py",), ("trendguard/recherche.py",), ()),
    ("C", "Perception", "trendguard/perception.py", ("trendguard/perception.py",), ("trendguard/qualite.py",), ()),
    ("C", "Raisonnement (diagnostic)", "trendguard/cognitif.py", ("trendguard/cognitif.py", "trendguard/expert.py"),
     ("trendguard/monde.py",), (OWNER,)),
    ("C", "Orchestration des agents", "trendguard/comite.py", ("trendguard/comite.py", "trendguard/agents.py"),
     ("trendguard/cognitif.py",), ()),
    ("C", "Modèles d'IA (LLM)", "trendguard/modeles.py", ("trendguard/modeles.py",), ("trendguard/apprentissage.py",), ()),
    ("D", "État du monde", "trendguard/monde.py", ("trendguard/monde.py", "trendguard/regimes.py"),
     ("trendguard/perception.py",), ()),
    ("D", "Modèle causal", "trendguard/causal.py", ("trendguard/causal.py",), ("trendguard/trend_strategy.py",), ()),
    ("D", "Simulation (jumeau)", "trendguard/jumeau.py", ("trendguard/jumeau.py", "trendguard/trend_strategy.py"),
     ("trendguard/acceptation.py",), ()),
    ("E", "Objectifs et plans", "trendguard/objectifs.py", ("trendguard/objectifs.py",),
     ("trendguard/acceptation.py", "trendguard/autorisation.py"), (OWNER,)),
    ("F", "Intelligence financière", "trendguard/finance.py",
     ("trendguard/finance.py", "trendguard/evenements.py", "trendguard/savoir.py"), ("trendguard/regimes.py",), ()),
    ("F", "Quantification (risque d'un jour, résistance)", "trendguard/moteur_quant.py",
     ("trendguard/moteur_quant.py", "trendguard/risque.py", "trendguard/stress.py"), (), ()),
    ("F", "Stratégie (la règle)", "trendguard/trend_strategy.py", ("trendguard/trend_strategy.py",
                                                                   "trendguard/moteur_strategie.py"),
     ("trendguard/evolution.py",), (OWNER,)),
    ("F", "Backtest et études", "trendguard/moteur_backtest.py",
     ("trendguard/moteur_backtest.py", "trendguard/replay.py", "trendguard/strategy_lab.py"), (), ()),
    ("F", "Allocation du portefeuille", "trendguard/moteur_portefeuille.py",
     ("trendguard/moteur_portefeuille.py", "trendguard/selection.py"), ("trendguard/moteur_risque.py",), ()),
    ("F", "Essai paper et son acceptation", "trendguard/acceptation.py", ("trendguard/bot.py", "trendguard/acceptation.py"),
     (), (OWNER,)),
    ("G", "Risque", "trendguard/moteur_risque.py", ("trendguard/moteur_risque.py", "trendguard/garde.py"),
     ("trendguard/moteur_portefeuille.py",), (OWNER,)),
    ("G", "Règles de politique", "trendguard/politique.py", ("trendguard/politique.py",), ("trendguard/moteur_risque.py",),
     ()),
    ("G", "Autorité et permissions", "trendguard/autorisation.py", ("trendguard/autorisation.py",),
     ("trendguard/politique.py",), (OWNER,)),
    ("G", "Validation finale avant l'ordre", "trendguard/porte.py", ("trendguard/porte.py", "trendguard/bot_execution.py"),
     ("trendguard/autorisation.py",), ()),
    ("H", "Exécution des ordres", "trendguard/bot_execution.py", ("trendguard/bot_execution.py",),
     ("trendguard/porte.py",), (OWNER,)),
    ("H", "Paliers du réel", "trendguard/deploiement.py", ("trendguard/deploiement.py",), ("trendguard/acceptation.py",),
     (OWNER,)),
    ("H", "Opérations de production", "trendguard/controle.py",
     ("trendguard/controle.py", "trendguard/autonomy.py", "trendguard/maintenance.py"), ("trendguard/cyber.py",), (OWNER,)),
    ("H", "Alertes", "trendguard/alerts.py", ("trendguard/alerts.py",), (), (OWNER,)),
    ("I", "Apprentissage et évolution encadrée", "trendguard/apprentissage.py",
     ("trendguard/apprentissage.py", "trendguard/evolution.py", "trendguard/learning.py"), ("trendguard/jumeau.py",), (OWNER,)),
    ("J", "Cybersécurité", "trendguard/cyber.py", ("trendguard/cyber.py", "trendguard/report_security.py"),
     ("trendguard/controle.py",), (OWNER,)),
    ("K", "Interface humaine", "panel/interface.py", ("panel/server.py", "panel/assistant.py", "panel/interface.py"),
     (), (OWNER,)),
    ("K", "Armer le réel, changer le risque, lever le mode sûr", OWNER, (OWNER,), ("trendguard/autorisation.py",), ()),
)

# Frontières « X ≠ Y » (RACI normalisée §5-20) : deux responsabilités qui ne
# doivent jamais avoir le même responsable.
SEPARATIONS = (("Règles de politique", "Autorité et permissions"),
               ("Autorité et permissions", "Validation finale avant l'ordre"),
               ("Validation finale avant l'ordre", "Exécution des ordres"),
               ("Risque", "Allocation du portefeuille"), ("Risque", "Règles de politique"),
               ("Intelligence financière", "Quantification (risque d'un jour, résistance)"),
               ("Quantification (risque d'un jour, résistance)", "Stratégie (la règle)"),
               ("Stratégie (la règle)", "Risque"), ("État du monde", "Simulation (jumeau)"),
               ("Recherche externe et vérification des sources", "Mémoire durable"),
               ("Raisonnement (diagnostic)", "Orchestration des agents"),
               ("Modèles d'IA (LLM)", "Raisonnement (diagnostic)"), ("Cybersécurité", "Opérations de production"),
               ("Apprentissage et évolution encadrée", "Stratégie (la règle)"), ("Perception", "État du monde"))

# Matrice des autorités : (module, observer, recommander, bloquer, autoriser, exécuter).
AUTHORITY: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    ("trendguard/qualite.py", "oui", "non", "données abîmées : décision reportée", "non", "non"),
    ("trendguard/recherche.py", "oui", "oui", "non (seule une annonce de Binance bloque, via la garde)", "non", "non"),
    ("trendguard/memoire.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/perception.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/monde.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/causal.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/jumeau.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/cognitif.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/objectifs.py", "oui", "oui", "non", "non", "non"),
    ("trendguard/moteur_risque.py", "oui", "oui", "BLOCAGE DU RISQUE", "non", "non"),
    ("trendguard/politique.py", "oui", "oui", "signale un écart (la porte applique)", "non", "non"),
    ("trendguard/autorisation.py", "oui", "non", "REFUS D'AUTORISATION", "oui (actions de l'opérateur)", "non"),
    ("trendguard/porte.py", "oui", "non", "BLOCAGE FINAL", "un achat, après un contrôle approuvé", "non"),
    ("trendguard/bot_execution.py", "oui", "non", "échec d'exécution", "non", "oui (seul à envoyer un ordre)"),
    ("trendguard/cyber.py", "oui", "oui", "BLOCAGE DE SÉCURITÉ", "non", "défensif uniquement"),
    ("trendguard/controle.py", "oui", "oui", "opérationnel (redémarrage)", "non", "opérations autorisées"),
    ("trendguard/apprentissage.py", "oui", "oui", "modèle dégradé ; évolution gelée en réel contrôlé", "non", "non"),
    (OWNER, "oui", "oui", "arrêt d'urgence, mode sûr", "oui (armer le réel, le risque)", "non (le bot exécute)"),
)

# Une seule source de vérité par donnée critique : (donnée, propriétaire, où).
SYSTEM_OF_RECORD: Tuple[Tuple[str, str, str], ...] = (
    ("Bougies validées", "trendguard/qualite.py", "état du bot (note des données) ; Binance fait foi"),
    ("Journal financier (achats, ventes, frais)", "trendguard/donnees.py", "base du bot, tables fin_"),
    ("Limites de risque", "trendguard/config.py", "réglages TG_RISK_* (vous seul)"),
    ("Règles de politique", "trendguard/politique.py", "registre des politiques (code versionné)"),
    ("Autorisations", "trendguard/autorisation.py", "décisions du moteur d'autorisation"),
    ("Ordres et positions", "trendguard/bot_execution.py", "état du bot (paper) ; Binance (réel)"),
    ("Incidents", "trendguard/controle.py", "<bot>.incidents.json"),
    ("Versions des modèles", "trendguard/apprentissage.py", "fiches des modèles (code versionné)"),
    ("Connaissances", "trendguard/memoire.py", "reconstruites depuis les sources, jamais copiées"),
    ("État du monde", "trendguard/monde.py", "<bot>.monde.json"),
    ("Résultats de simulation", "trendguard/jumeau.py", "à la demande, jamais en production"),
    ("Observations", "trendguard/perception.py", "à la demande, depuis l'état du bot et les caches"),
    ("Journal d'audit", "trendguard/audit.py", "<bot>.audit.jsonl (chaîné)"),
)

# Modules d'intelligence et de conseil : jamais un lien direct vers
# l'exécution des ordres (barrières non contournables).
UPSTREAM = ("trendguard/perception.py", "trendguard/jumeau.py", "trendguard/objectifs.py", "trendguard/monde.py",
            "trendguard/causal.py", "trendguard/recherche.py", "trendguard/memoire.py", "trendguard/cognitif.py",
            "trendguard/expert.py", "trendguard/comite.py", "trendguard/agents.py", "trendguard/modeles.py",
            "trendguard/savoir.py", "trendguard/finance.py", "trendguard/apprentissage.py", "trendguard/cyber.py",
            "trendguard/controle.py", "panel/assistant.py", "panel/interface.py", "panel/server.py")
EXECUTION = {"bot_execution"}

GATES = ("FUNCTIONAL", "CONTRACT", "SECURITY", "PERFORMANCE", "RELIABILITY", "DATA_INTEGRITY", "OBSERVABILITY",
         "FAILURE_RECOVERY", "REPRODUCIBILITY", "GOVERNANCE")
GATE_FR = {"FUNCTIONAL": "fonctionnel", "CONTRACT": "contrats", "SECURITY": "sécurité", "PERFORMANCE": "performance",
           "RELIABILITY": "fiabilité", "DATA_INTEGRITY": "intégrité des données", "OBSERVABILITY": "observabilité",
           "FAILURE_RECOVERY": "reprise après panne", "REPRODUCIBILITY": "reproductibilité", "GOVERNANCE": "gouvernance"}
P0 = ("CONTRACT", "SECURITY", "DATA_INTEGRITY", "GOVERNANCE")
AVAILABILITY_TARGET = 99.0
BROAD = {"Exception", "BaseException"}


def raci(rows: Sequence[Tuple[str, str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = RACI,
         root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """RACI normalisée (§2-3) : exactement un A, au moins un R, chaque
    responsabilité une seule fois et dans un seul plan, chaque module cité
    présent, et les frontières « X ≠ Y » tenues (responsables différents)."""
    problems = []
    names = [r[1] for r in rows]
    for n in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"« {n} » : deux fois (doublon)")
    for plan, name, a, r, c, i in rows:
        if plan not in PLANS:
            problems.append(f"« {name} » : plan inconnu {plan}")
        if not isinstance(a, str) or not a or "," in a:
            problems.append(f"« {name} » : il faut exactement un A")
        if not r:
            problems.append(f"« {name} » : aucun R")
        for m in (a,) + tuple(r) + tuple(c) + tuple(i):
            if m != OWNER and not (root / m).exists():
                problems.append(f"« {name} » : module absent {m}")
        if a in c or a in i:
            problems.append(f"« {name} » : le responsable est aussi consulté ou informé")
    owner = {n: a for _p, n, a, _r, _c, _i in rows}
    for x, y in SEPARATIONS:
        if x in owner and y in owner and owner[x] == owner[y]:
            problems.append(f"« {x} » et « {y} » : même responsable ({owner[x]})")
        elif x not in owner or y not in owner:
            problems.append(f"frontière « {x} ≠ {y} » : responsabilité absente de la RACI")
    return {"ok": not problems, "problems": problems, "count": len(rows),
            "plans": {p: [n for q, n, *_x in rows if q == p] for p in PLANS}}


def system_of_record(rows: Sequence[Tuple[str, str, str]] = SYSTEM_OF_RECORD, root: pathlib.Path = ROOT
                     ) -> Dict[str, Any]:
    """Une donnée, une source de vérité, un propriétaire (§17) ; le journal
    financier n'est touché que par son propriétaire (tables fin_)."""
    problems = []
    data = [d for d, _o, _w in rows]
    for d in sorted({d for d in data if data.count(d) > 1}):
        problems.append(f"« {d} » : deux sources de vérité")
    for d, owner, _w in rows:
        if not (root / owner).exists():
            problems.append(f"« {d} » : propriétaire absent {owner}")
    fin = re.compile(r"\bfin_[a-z]+\b")
    for f in sorted((root / "trendguard").glob("*.py")) + sorted((root / "panel").glob("*.py")):
        if f.name in ("donnees.py", "gouvernance.py"):
            continue
        if fin.search(f.read_text(encoding="utf-8")):
            problems.append(f"{f.relative_to(root).as_posix()} touche aux tables du journal financier")
    return {"ok": not problems, "problems": problems, "count": len(rows)}


def boundaries(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Frontières critiques (§19), vérifiées par lecture du code : aucun
    module d'intelligence ou de conseil n'importe l'exécution des ordres ; un
    seul appel d'achat dans tout le code, après la porte et la validation
    finale ; la porte contrôle avant d'autoriser ; le jumeau et la
    perception sont isolés ; la sécurité ne crée aucune permission."""
    rows = []
    for f in UPSTREAM:
        p = root / f
        bad = sorted(perception._imports(p) & EXECUTION) if p.exists() else []
        rows.append((f"{pathlib.Path(f).stem} → ordres", not bad, "aucun lien direct" if not bad
                     else "importe " + ", ".join(bad)))
    rt = porte_examen.routes(root)
    rows.append(("tout le code → Binance (achat)", rt["ok"],
                 f"{len(rt['calls'])} appel(s) d'achat ; après la porte et la validation finale : "
                 + ("oui" if rt["ordered"] else "non")))
    try:
        text = (root / "trendguard" / "bot_execution.py").read_text(encoding="utf-8")
        body = text.split("    def _gate(", 1)[1].split("\n    def ", 1)[0]
        port = text.split("    def _portfolio(", 1)[1].split("\n    def ", 1)[0]
        i, j = body.find("porte.check("), body.find("porte.authorize(")
        chain = 0 <= i < j and "moteur_risque.gate(" in port
    except (OSError, IndexError):
        chain = False
    rows.append(("risque → porte → autorisation", chain, "le moteur de risque nourrit le contrôle, contrôle avant "
                                                         "autorisation" if chain else "ordre non retrouvé"))
    iso = jumeau.isolation()
    rows.append(("jumeau → production", iso["ok"], "aucune bibliothèque réseau, aucune écriture dans le bot"))
    dep = perception.dependencies(root)
    rows.append(("perception → décision", dep["ok"], "aucune dépendance interdite" if dep["ok"]
                 else "; ".join(dep["problems"])))
    cy = perception._imports(root / "trendguard" / "cyber.py") & {"autorisation", "bot_execution"}
    off = cyber.offensive_capabilities()
    rows.append(("sécurité → exécution", not cy and not off, "bloque, gèle, mode sûr ; aucune permission créée"
                 if not cy and not off else "lien vers " + ", ".join(sorted(cy) + off)))
    ctl = (root / "trendguard" / "controle.py").read_text(encoding="utf-8")
    silent_limits = any(s in ctl for s in ("set_key(", "risk_pct =", "risk_pct=", "TG_RISK_PCT ="))
    rows.append(("plan de contrôle → risque", not silent_limits, "aucune limite de risque modifiée"
                 if not silent_limits else "modifie une limite de risque"))
    return {"ok": all(ok for _n, ok, _d in rows), "rows": rows,
            "bypasses": sum(1 for _n, ok, _d in rows if not ok)}


def silent_failures(root: pathlib.Path = ROOT) -> List[str]:
    """Zéro échec silencieux (§44) : une exception large (Exception) attrapée
    puis ignorée sans raison écrite à côté. Les exceptions étroites (fichier,
    format) sont déjà classées par leur type."""
    out = []
    for d in ("trendguard", "panel"):
        for f in sorted((root / d).rglob("*.py")):
            src, tree = perception.parsed(f)
            lines = src.splitlines()
            for n in ast.walk(tree):
                if not isinstance(n, ast.ExceptHandler) or not all(isinstance(b, (ast.Pass, ast.Continue))
                                                                   for b in n.body):
                    continue
                names = ({n.type.id} if isinstance(n.type, ast.Name) else
                         {e.id for e in n.type.elts if isinstance(e, ast.Name)} if isinstance(n.type, ast.Tuple)
                         else {"Exception"} if n.type is None else set())
                if names & BROAD and "#" not in lines[n.lineno - 1] and "#" not in lines[n.body[0].lineno - 1]:
                    out.append(f"{f.relative_to(root).as_posix()}:{n.lineno}")
    return out


def _gate(result: str, detail: str) -> Tuple[str, str]:
    return result, detail


def gates(gcfg: Any, state: Mapping[str, Any], now: datetime, root: pathlib.Path = ROOT,
          ra: Optional[Dict[str, Any]] = None, so: Optional[Dict[str, Any]] = None,
          bd: Optional[Dict[str, Any]] = None, silent: Optional[List[str]] = None) -> Dict[str, Tuple[str, str]]:
    """Les dix portes de qualité (§32), chacune PASS, FAIL ou WAIVED, d'après
    une preuve mesurée."""
    out: Dict[str, Tuple[str, str]] = {}
    comps = chantiers.COMPONENTS
    missing = [t for c in comps for t in c.tests if not (root / t).exists()]
    out["FUNCTIONAL"] = _gate("FAIL" if missing else "PASS", f"{len(comps)} composants, chacun avec ses tests"
                              if not missing else "tests absents : " + ", ".join(missing[:3]))
    known = {c.contract_id for c in contrats.REGISTRY}
    unknown = sorted({k for c in comps for k in c.contracts if k not in known})
    doc = root / "docs" / "CONTRATS.md"
    fresh = doc.exists() and doc.read_text(encoding="utf-8") == contrats.render()
    out["CONTRACT"] = _gate("PASS" if not unknown and fresh else "FAIL",
                            f"{len(known)} contrats au registre, document à jour" if not unknown and fresh else
                            ("contrats inconnus : " + ", ".join(unknown[:3]) if unknown else "docs/CONTRATS.md à régénérer"))
    eg = cyber.egress(root)
    off = cyber.offensive_capabilities()
    out["SECURITY"] = _gate("PASS" if not eg.get("unknown") and not off else "FAIL",
                            "aucune adresse hors de la liste blanche, aucune capacité offensive"
                            if not eg.get("unknown") and not off else
                            "hors liste blanche : " + ", ".join(sorted(eg.get("unknown") or [])[:3]))
    out["PERFORMANCE"] = _gate("WAIVED", "pas de banc global : chaque moteur mesure son temps dans son examen")
    up = uptime.summary(state.get("uptime"), state.get("last_cycle_ts"), state.get("stopped_at"), now.timestamp())
    week = up.get("week_pct")
    out["RELIABILITY"] = (_gate("WAIVED", "pas encore assez d'historique de marche") if week is None else
                          _gate("PASS" if week >= AVAILABILITY_TARGET else "FAIL",
                                f"disponibilité sur 7 jours {fr(week, '.1f')} % (objectif {fr(AVAILABILITY_TARGET, '.0f')} %)"))
    au = audit.verify(audit.path_for(gcfg))
    so = so or system_of_record(root=root)
    out["DATA_INTEGRITY"] = _gate("PASS" if au["ok"] and so["ok"] else "FAIL",
                                  f"journal d'audit intact ({au['events']} événements), une source de vérité par donnée"
                                  if au["ok"] and so["ok"] else ("journal d'audit modifié à la ligne "
                                                                 f"{au['bad_line']}" if not au["ok"]
                                                                 else "; ".join(so["problems"][:2])))
    silent = silent_failures(root) if silent is None else silent
    out["OBSERVABILITY"] = _gate("PASS" if not silent else "FAIL", "aucune exception large ignorée sans raison écrite"
                                 if not silent else f"{len(silent)} échec(s) silencieux : " + ", ".join(silent[:3]))
    services = {s.sid for s in controle.SERVICES}
    no_rb = sorted(services - set(controle.SERVICE_RUNBOOK))
    out["FAILURE_RECOVERY"] = _gate("PASS" if not no_rb else "FAIL", f"{len(services)} services, chacun avec sa "
                                    "procédure de reprise" if not no_rb else "sans procédure : " + ", ".join(no_rb))
    req = root / "requirements-docker.txt"
    lines = [x.strip() for x in req.read_text(encoding="utf-8").splitlines()] if req.exists() else []
    loose = [x for x in lines if x and not x.startswith("#") and "==" not in x]
    out["REPRODUCIBILITY"] = _gate("PASS" if lines and not loose else "FAIL",
                                   "versions des bibliothèques figées (requirements-docker.txt)" if lines and not loose
                                   else "versions non figées : " + ", ".join(loose[:3]) if loose else "liste absente")
    ra = ra or raci(root=root)
    bd = bd or boundaries(root)
    gov = ra["ok"] and so["ok"] and bd["ok"]
    out["GOVERNANCE"] = _gate("PASS" if gov else "FAIL",
                              f"RACI : {ra['count']} responsabilités, un seul responsable chacune ; frontières tenues"
                              if gov else "; ".join((ra["problems"] + so["problems"])[:2]
                                                    + [n for n, ok, _d in bd["rows"] if not ok][:2]))
    return out


def band(score: float) -> str:
    """Bandes des critères de sortie (§33)."""
    return ("READY" if score >= 95 else "CANDIDATE" if score >= 90 else "VALIDATING" if score >= 80
            else "DEVELOPMENT" if score >= 70 else "REJECTED")


def readiness(g: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    """Note = Σ poids × note, sur les portes non levées (WAIVED) ; un échec
    P0 : BLOCKED quelle que soit la note."""
    counted = {k: v for k, v in g.items() if v[0] != "WAIVED"}
    score = round(100.0 * sum(1 for r, _d in counted.values() if r == "PASS") / len(counted), 1) if counted else 0.0
    p0 = [k for k in P0 if g.get(k, ("FAIL", ""))[0] == "FAIL"]
    return {"score": score, "p0": p0, "status": "BLOCKED" if p0 else band(score)}


def evaluate(gcfg: Any, state: Mapping[str, Any], now: Optional[datetime] = None,
             root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """La gouvernance du moment : RACI, sources de vérité, frontières,
    échecs silencieux, dix portes, note."""
    now = now or datetime.now(timezone.utc)
    ra, so, bd = raci(root=root), system_of_record(root=root), boundaries(root)
    silent = silent_failures(root)
    g = gates(gcfg, state, now, root, ra, so, bd, silent)
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "raci": ra, "sor": so, "boundaries": bd, "silent": silent, "gates": g, "readiness": readiness(g)}


def report_of(r: Dict[str, Any]) -> GovernanceReport:
    """Le résultat au format du contrat GovernanceReport.v1."""
    rd = r["readiness"]
    return GovernanceReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=rd["status"],
                            readiness_score=rd["score"], p0_failures=tuple(rd["p0"]),
                            gates=tuple((k, v[0]) for k, v in r["gates"].items()),
                            raci_violations=len(r["raci"]["problems"]), sor_conflicts=len(r["sor"]["problems"]),
                            bypasses=r["boundaries"]["bypasses"], silent_failures=len(r["silent"]),
                            engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : note, portes, écarts."""
    rd, g = r["readiness"], r["gates"]
    fails = [GATE_FR[k] for k, (res, _d) in g.items() if res == "FAIL"]
    return (f"{rd['status']} ({fr(rd['score'], '.0f')}/100) ; portes de qualité : "
            f"{sum(v[0] == 'PASS' for v in g.values())} PASS, {len(fails)} FAIL"
            + (f" ({', '.join(fails)})" if fails else "") + f", {sum(v[0] == 'WAIVED' for v in g.values())} WAIVED ; "
            f"RACI : {len(r['raci']['problems'])} écart(s) ; contournements : {r['boundaries']['bypasses']}")


def render(r: Dict[str, Any]) -> str:
    """docs/GOUVERNANCE_ETAT.md : portes, RACI, autorités, sources de vérité,
    frontières."""
    rd = r["readiness"]
    lines = ["# Gouvernance de l'architecture", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py gouvernance` (documents transverses du prompt "
             "maître, [`GOUVERNANCE.md`](GOUVERNANCE.md)). Lecture seule.", "",
             f"- **{rd['status']}** ; note {fr(rd['score'], '.0f')}/100" + (f" ; P0 en échec : {', '.join(rd['p0'])}"
                                                                          if rd["p0"] else "") + ".",
             f"- {describe(r)}."]
    lines += ["", "## Les dix portes de qualité", "", "| Porte | P0 | résultat | preuve |", "| --- | --- | --- | --- |"]
    for k, (res, d) in r["gates"].items():
        lines.append(f"| {GATE_FR[k]} | {'oui' if k in P0 else 'non'} | {res} | {d} |")
    lines += ["", "## RACI normalisée (un seul responsable par responsabilité)", "",
              "| Plan | responsabilité | A | R | C | I |", "| --- | --- | --- | --- | --- | --- |"]

    def names(ms: Sequence[str]) -> str:
        return ", ".join(m if m == OWNER else f"`{pathlib.Path(m).name}`" for m in ms) or "—"
    for plan, name, a, rr, c, i in RACI:
        lines.append(f"| {plan} {PLANS[plan]} | {name} | {names((a,))} | {names(rr)} | {names(c)} | {names(i)} |")
    if r["raci"]["problems"]:
        lines += [""] + [f"- **{p}**" for p in r["raci"]["problems"]]
    lines += ["", "## Qui peut quoi", "", "| Module | observer | recommander | bloquer | autoriser | exécuter |",
              "| --- | --- | --- | --- | --- | --- |"]
    for m, o, rec, b, a, e in AUTHORITY:
        lines.append(f"| {names((m,))} | {o} | {rec} | {b} | {a} | {e} |")
    lines += ["", "## Une source de vérité par donnée", "", "| Donnée | propriétaire | où |", "| --- | --- | --- |"]
    lines += [f"| {d} | {names((o,))} | {w} |" for d, o, w in SYSTEM_OF_RECORD]
    lines += ["", "## Frontières critiques (vérifiées dans le code)", "", "| Frontière | tenue | preuve |",
              "| --- | --- | --- |"]
    lines += [f"| {n} | {'oui' if ok else '**NON**'} | {d} |" for n, ok, d in r["boundaries"]["rows"]]
    if r["silent"]:
        lines += ["", "## Échecs silencieux à expliquer", ""] + [f"- `{s}`" for s in r["silent"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `gouvernance` : la gouvernance du moment ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Gouvernance de l'architecture")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {})
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["readiness"]["status"] == "READY" else 1
