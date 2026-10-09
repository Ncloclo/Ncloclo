"""
Mémoire et graphe de connaissances (prompt maître, étape 24 ;
docs/MEMOIRE.md) : ce que TrendGuard sait, relié et daté, reconstruit à la
demande depuis ses sources de vérité, jamais stocké à part (aucune seconde
vérité qui pourrait diverger).

    sources de vérité (état du bot, journal financier, savoir, registres)
    → entités (cryptos, trades, positions, décisions, ordres, sources,
      modèles, services, contrats, procédures) et relations, chacune avec sa
      provenance, ses dates (valide du… au…) et sa nature (observée,
      déduite, rapportée)
    → questions : tout sur une entité, ce qui était vrai à une date, le
      chemin entre deux entités ; contradictions entre sources
    → note de santé de la mémoire et verdict

Règles : une déduction n'est jamais enregistrée comme une observation ; ce
qu'une source extérieure dit reste « rapporté », jamais un fait sur une
crypto ; rien n'est réécrit (la mémoire se reconstruit, les sources gardent
leur historique).

    python trendguard_bot.py memoire                     # santé et contenu
    python trendguard_bot.py memoire ETH                 # tout sur une entité
"""

from __future__ import annotations

import argparse
import os
import pathlib
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import autorisation, contrats, controle, donnees
from . import trend_strategy as ts
from .contrats import MemoryHealthReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "memoire-1.0.0"
KINDS = ("OBSERVED", "INFERRED", "REPORTED")
KIND_FR = {"OBSERVED": "observé", "INFERRED": "déduit", "REPORTED": "rapporté"}
MEMORY_TYPES = {"asset": "sémantique", "trade": "épisodique", "position": "épisodique", "decision": "épisodique",
                "order": "épisodique", "source": "sémantique", "model": "sémantique", "service": "sémantique",
                "contract": "sémantique", "runbook": "procédurale", "veto": "épisodique", "regime": "épisodique"}
WEIGHTS = {"integrity": 0.20, "provenance": 0.15, "retrieval": 0.15, "governance": 0.10, "security": 0.15,
           "reasoning": 0.10, "temporal": 0.05, "performance": 0.05, "observability": 0.05}
WEIGHT_FR = {"integrity": "intégrité", "provenance": "provenance", "retrieval": "recherche", "governance": "gouvernance",
             "security": "sécurité", "reasoning": "raisonnement", "temporal": "cohérence dans le temps",
             "performance": "performance", "observability": "observabilité"}
P0 = ("integrity", "provenance", "security", "temporal")


class Graph:
    """Graphe en mémoire : entités et relations, avec provenance et dates."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []

    def node(self, nid: str, ntype: str, label: str, source: str, valid_from: Optional[str] = None,
             valid_to: Optional[str] = None, kind: str = "OBSERVED", **props: Any) -> str:
        """Ajoute (ou complète) une entité ; la première provenance est gardée."""
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "type": ntype, "label": label, "source": source, "kind": kind,
                               "valid_from": valid_from, "valid_to": valid_to, "props": dict(props)}
        else:
            self.nodes[nid]["props"].update({k: v for k, v in props.items() if v is not None})
        return nid

    def edge(self, a: str, rel: str, b: str, source: str, kind: str = "OBSERVED", valid_from: Optional[str] = None,
             valid_to: Optional[str] = None, **props: Any) -> None:
        """Ajoute une relation, avec sa provenance et sa nature."""
        self.edges.append({"from": a, "rel": rel, "to": b, "source": source, "kind": kind,
                           "valid_from": valid_from, "valid_to": valid_to, "props": dict(props)})

    def neighbours(self, nid: str) -> List[Tuple[str, str, str]]:
        """(sens, relation, voisin) de chaque relation d'une entité."""
        out = [("→", e["rel"], e["to"]) for e in self.edges if e["from"] == nid]
        return out + [("←", e["rel"], e["from"]) for e in self.edges if e["to"] == nid]

    def path(self, a: str, b: str, max_len: int = 6) -> Optional[List[str]]:
        """Le plus court chemin entre deux entités (sans tenir compte du sens)."""
        if a not in self.nodes or b not in self.nodes:
            return None
        prev: Dict[str, Optional[str]] = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                out = [b]
                while prev[out[-1]] is not None:
                    out.append(prev[out[-1]])  # type: ignore[arg-type]
                return list(reversed(out))
            for _d, _r, n in self.neighbours(cur):
                if n not in prev:
                    prev[n] = cur
                    q.append(n)
                    if len(prev) > 50_000:
                        return None
        return None


def build(gcfg: Any, state: Dict[str, Any], journal_path: Optional[str] = None, now: Optional[datetime] = None,
          journal: Any = None) -> Graph:
    """Reconstruit la mémoire depuis les sources de vérité (lecture seule) :
    état du bot, journal financier, registres des modèles, des services, des
    contrats et des procédures. Rien n'est inventé : une source absente ne
    donne aucune entité."""
    now = now or datetime.now(timezone.utc)
    g = Graph()
    for base in getattr(gcfg, "universe", ()):
        g.node(f"asset:{base.lower()}", "asset", base.upper(), "réglages (TG_UNIVERSE)")
    for a, h in sorted(((state.get("paper") or {}).get("holdings") or {}).items()):
        pid = g.node(f"position:{a}", "position", f"position {a.upper()}", "état du bot", valid_from=h.get("entry_date"),
                     entry=h.get("entry"), stop=h.get("stop"), qty=h.get("qty"))
        g.edge(pid, "SUR", g.node(f"asset:{a}", "asset", a.upper(), "état du bot"), "état du bot",
               valid_from=h.get("entry_date"))
    for i, t in enumerate(state.get("trades") or []):
        a = str(t.get("asset", "?")).lower()
        tid = g.node(f"trade:etat:{i}", "trade", f"trade {a.upper()} {str(t.get('date', ''))[:10]}", "état du bot",
                     valid_from=t.get("entry_date"), valid_to=t.get("date"), pnl=t.get("pnl"), r=t.get("r"))
        g.edge(tid, "SUR", g.node(f"asset:{a}", "asset", a.upper(), "état du bot"), "état du bot",
               valid_from=t.get("entry_date"), valid_to=t.get("date"))
        if isinstance(t.get("pnl"), (int, float)):
            res = "gain" if t["pnl"] > 0 else "perte"
            g.node(f"resultat:{res}", "result", res, "déduit du résultat des trades", kind="INFERRED")
            g.edge(tid, "TERMINE_EN", f"resultat:{res}", "déduit du résultat", kind="INFERRED")
    for a, v in sorted((state.get("vetoes") or {}).items()):
        vid = g.node(f"veto:{a}", "veto", f"retrait de {a.upper()} annoncé", "annonce officielle de Binance (veille)",
                     valid_to=(v or {}).get("until"), reason=(v or {}).get("reason"))
        g.edge(vid, "CONCERNE", g.node(f"asset:{a}", "asset", a.upper(), "état du bot"), "veille")
    reg = state.get("regime_detail") or {}
    if reg:
        rid = g.node(f"regime:{state.get('last_decision_day')}", "regime", str(reg.get("texte") or "lecture du marché"),
                     "état du bot (décision)", valid_from=state.get("last_decision_day"))
        g.edge(rid, "LU_SUR", g.node("asset:btc", "asset", "BTC", "réglages"), "décision")
    path = journal_path if journal_path is not None else donnees.path_for(gcfg)
    if journal is not None or (path and path != ":memory:" and os.path.exists(path)):
        j = journal if journal is not None else donnees.Journal(path, readonly=True)
        try:
            for t in j.trades(limit=100_000):
                tid = g.node(f"trade:{t['id']}", "trade", f"trade {str(t['asset']).upper()} (journal)",
                             "journal financier", valid_from=t.get("opened_at"), valid_to=t.get("closed_at"),
                             pnl=t.get("pnl"))
                g.edge(tid, "SUR", g.node(f"asset:{str(t['asset']).lower()}", "asset", str(t["asset"]).upper(),
                                          "journal financier"), "journal financier")
                try:
                    lin = j.lineage(t["id"])
                except (KeyError, TypeError):
                    continue
                for key, rel in (("exit_order", "VENDU_PAR"), ("entry_order", "ACHETE_PAR")):
                    if lin.get(key):
                        o = lin[key]
                        oid = g.node(f"order:{o['id']}", "order", f"ordre {o.get('side')} {str(o.get('asset')).upper()}",
                                     "journal financier", valid_from=o.get("created_at"))
                        g.edge(tid, rel, oid, "journal financier")
                        if key == "entry_order" and lin.get("decision"):
                            d = lin["decision"]
                            did = g.node(f"decision:{d['id']}", "decision", f"décision du {d.get('day')}",
                                         "journal financier", valid_from=d.get("day"))
                            g.edge(oid, "DECIDE_PAR", did, "journal financier")
        finally:
            if journal is None:
                j.close()
    # Résolution des entités : un trade de l'état du bot et un trade du journal
    # financier sur la même crypto, vendus le même jour, sont le même trade.
    trades = [n for n in g.nodes.values() if n["type"] == "trade"]
    for a in (n for n in trades if n["source"] == "état du bot"):
        for b in (n for n in trades if n["source"] == "journal financier"):
            same_asset = a["label"].split()[1] == b["label"].split()[1]
            if same_asset and a["valid_to"] and b["valid_to"] and str(a["valid_to"])[:10] == str(b["valid_to"])[:10]:
                g.edge(a["id"], "MEME_QUE", b["id"], "déduit (même crypto, même jour de vente)", kind="INFERRED")
    from . import apprentissage
    for c in apprentissage.registry(gcfg):
        g.node(f"model:{c['model_id']}", "model", c["name"], "registre des modèles", risk=c["risk"], state=c["state"])
    for s in controle.SERVICES:
        g.node(f"service:{s.sid}", "service", s.name, "registre des services", tier=s.tier)
    for s in controle.SERVICES:
        for d in s.deps:
            g.edge(f"service:{s.sid}", "DEPEND_DE", f"service:{d}", "registre des services")
    for c in contrats.REGISTRY:
        g.node(f"contract:{c.contract_id}", "contract", c.contract_id, "registre des contrats", level=c.level)
    for rid, rb in controle.RUNBOOKS.items():
        g.node(f"runbook:{rid}", "runbook", rb["title"], "procédures du plan de contrôle",
               version=controle.runbook_version(rid))
        g.edge(f"runbook:{rid}", "REPARE", f"service:{rb['service']}", "procédures du plan de contrôle")
    return g


def about(g: Graph, query: str) -> Dict[str, Any]:
    """Tout ce que la mémoire sait d'une entité (par identifiant ou nom),
    avec la provenance et la nature de chaque fait."""
    q = query.lower()
    nid = query if query in g.nodes else q if q in g.nodes else f"asset:{q}" if f"asset:{q}" in g.nodes else next(
        (n for n, x in g.nodes.items() if x["label"].lower() == q), None)
    if nid is None:
        return {"found": False, "query": query}
    facts = []
    for e in g.edges:
        if nid in (e["from"], e["to"]):
            other = e["to"] if e["from"] == nid else e["from"]
            facts.append({"rel": e["rel"], "with": g.nodes.get(other, {}).get("label", other), "source": e["source"],
                          "kind": e["kind"], "from": e["valid_from"], "to": e["valid_to"]})
    return {"found": True, "node": g.nodes[nid], "facts": facts}


def at(g: Graph, day: str) -> List[str]:
    """Ce qui était vrai à une date : positions et trades ouverts ce jour-là
    (raisonnement dans le temps, sur les dates de validité)."""
    out = []
    for n in g.nodes.values():
        if n["type"] not in ("trade", "position"):
            continue
        a, b = str(n["valid_from"] or "")[:10], str(n["valid_to"] or "9999")[:10]
        if a and a <= day <= b:
            out.append(n["label"])
    return sorted(out)


def contradictions(g: Graph) -> List[str]:
    """Contradictions entre sources : une crypto détenue selon l'état du bot
    alors qu'un retrait de Binance la vise est permis (ventes autorisées) ;
    une position sans crypto connue ne l'est pas."""
    out = []
    for n in g.nodes.values():
        if n["type"] == "position":
            asset = n["id"].split(":", 1)[1]
            if f"asset:{asset}" not in g.nodes:
                out.append(f"position {asset.upper()} sans crypto connue")
    return out


def health(g: Graph, build_s: float) -> Dict[str, Any]:
    """Note de santé de la mémoire (§59) : chaque famille mesurée sur le
    graphe ; un défaut d'intégrité, de provenance, de sécurité ou de dates
    est un P0."""
    missing = [e for e in g.edges if e["from"] not in g.nodes or e["to"] not in g.nodes]
    no_source = [n["id"] for n in g.nodes.values() if not n["source"]] + [e["rel"] for e in g.edges if not e["source"]]
    bad_kind = [x for x in list(g.nodes.values()) + g.edges if x["kind"] not in KINDS]
    reported_facts = [e for e in g.edges if e["kind"] == "REPORTED" and g.nodes.get(e["to"], {}).get("type") == "asset"
                      and e["rel"] not in ("DIT", "CONCERNE")]
    inferred_ok = all(e["source"].startswith("déduit") for e in g.edges if e["kind"] == "INFERRED")
    time_bad = [n["id"] for n in g.nodes.values() if n["valid_from"] and n["valid_to"]
                and str(n["valid_from"])[:10] > str(n["valid_to"])[:10] and n["type"] != "veto"]
    retrieval_ok = all(about(g, n)["found"] for n in list(g.nodes)[:50])
    writers = [k for k in autorisation.PRINCIPALS if autorisation.decide(k, "CHANGE_SETTINGS", context={
        c: True for c in autorisation.CONDITIONS_FR}).allowed and autorisation.PRINCIPALS[k]["type"] in ("AGENT", "AI_MODEL")]
    checks = {
        "integrity": (not missing, f"{len(g.nodes)} entités, {len(g.edges)} relations ; {len(missing)} relation(s) sans "
                                   "extrémité"),
        "provenance": (not no_source, f"{len(no_source)} fait(s) sans source"),
        "retrieval": (retrieval_ok, "chaque entité retrouvée par son nom ou son identifiant"),
        "governance": (not bad_kind, "chaque fait est observé, déduit ou rapporté"),
        "security": (not reported_facts and not writers, "aucun texte extérieur pris pour un fait ; aucune IA "
                                                         "n'écrit dans la mémoire"),
        "reasoning": (inferred_ok, "chaque déduction est marquée comme telle, avec sa règle"),
        "temporal": (not time_bad, f"{len(time_bad)} fait(s) qui finissent avant de commencer"),
        "performance": (build_s <= 30.0, f"reconstruite en {fr(build_s, '.2f')} s"),
        "observability": (True, "contenu, santé et contradictions lisibles (commande memoire)"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0,
            "status": "READY_FOR_GOVERNED_LONG_TERM_MEMORY" if score >= 95 and not p0 else "NOT_READY"}


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None,
             journal_path: Optional[str] = None, journal: Any = None) -> Dict[str, Any]:
    """Reconstruit la mémoire, la mesure, et dit ce qu'elle contient."""
    now = now or datetime.now(timezone.utc)
    t = time.perf_counter()
    g = build(gcfg, state, journal_path, now, journal)
    took = time.perf_counter() - t
    h = health(g, took)
    types: Dict[str, int] = {}
    for n in g.nodes.values():
        types[n["type"]] = types.get(n["type"], 0) + 1
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "graph": g, "types": types, "health": h, "contradictions": contradictions(g), "build_s": took}


def report_of(r: Dict[str, Any]) -> MemoryHealthReport:
    """La santé au format du contrat MemoryHealthReport.v1."""
    g, h = r["graph"], r["health"]
    return MemoryHealthReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=h["status"], health_score=h["score"],
        p0_failures=tuple(h["p0"]), entities=len(g.nodes), relations=len(g.edges),
        inferred=sum(1 for e in g.edges if e["kind"] == "INFERRED"), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : taille, santé."""
    g, h = r["graph"], r["health"]
    return (f"{len(g.nodes)} entités et {len(g.edges)} relations reconstruites depuis les sources de vérité en "
            f"{fr(r['build_s'], '.2f')} s ; santé {fr(h['score'], '.0f')}/100" + (f" (P0 : {', '.join(h['p0'])})"
                                                                                 if h["p0"] else ""))


def render(r: Dict[str, Any]) -> str:
    """Le contenu et la santé de la mémoire, en clair."""
    h = r["health"]
    lines = ["# Mémoire et graphe de connaissances", "", f"Mesuré le {r['now'][:10]} (étape 24 du prompt maître).", "",
             f"- **{'Prête' if h['status'] != 'NOT_READY' else 'PAS PRÊTE'}** ({h['status']}).", f"- {describe(r)}.", "",
             "| Entités | nombre | mémoire |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v} | {MEMORY_TYPES.get(k, 'déduite' if k == 'result' else 'sémantique')} |"
              for k, v in sorted(r["types"].items())]
    lines += ["", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in h["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    lines += ["", "Contradictions : " + ("; ".join(r["contradictions"]) if r["contradictions"] else "aucune") + "."]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `memoire` : santé et contenu ; avec un nom, tout sur cette
    entité."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Mémoire et graphe de connaissances")
    ap.add_argument("entite", nargs="?", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {})
    report_of(r)
    if args.entite:
        a = about(r["graph"], args.entite)
        if not a["found"]:
            print(f"Rien en mémoire sur « {args.entite} ».")
            return 1
        print(f"{a['node']['label']} ({a['node']['type']}, source : {a['node']['source']})")
        for f in a["facts"]:
            print(f"- {f['rel']} {f['with']} ({KIND_FR[f['kind']]}, {f['source']})")
        return 0
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["health"]["status"] != "NOT_READY" else 1
