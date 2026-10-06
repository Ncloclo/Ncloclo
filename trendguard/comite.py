"""
Comité d'agents financiers (prompt maître, étape 5 ; docs/AGENTS.md) : pour
chaque crypto que la règle du bot propose d'acheter, onze agents spécialisés
donnent un avis indépendant, se critiquent et se vérifient, puis le comité
rend une recommandation motivée.

Analystes : données, technique, quant, régime, sentiment, risque (veto),
portefeuille, « sans trade » (cherche toutes les raisons de ne pas acheter).
Contrôle : critique, équipe rouge (essaie de casser la conclusion),
vérificateur indépendant (recalcule la cassure et le momentum autrement).

Recommandations : ACHAT, CONSERVER, ATTENDRE, PAS_DE_TRADE, PLUS_DE_RECHERCHE,
BLOCAGE (agent critique absent). Le comité est CONSULTATIF : il ne change
aucune décision du bot (règle « validé = exécuté » : son avis n'a pas encore
été prouvé meilleur que la règle). Ses avis sont gardés dans le journal
financier pour le mesurer sur les trades réels ; aucun agent ne peut passer
d'ordre ni autoriser un achat.

    python trendguard_bot.py comite aave     # l'avis du comité sur une crypto
    python trendguard_bot.py comite --banc   # les scénarios de référence
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import qualite, regimes
from . import trend_strategy as ts
from .agents import (
    Agent,
    AgentRegistry,
    AgentResult,
    Challenge,
    Manifest,
    MissionResult,
    Supervisor,
    View,
)
from .texte import fr

RECOMMENDATIONS = ("ACHAT", "CONSERVER", "ATTENDRE", "PAS_DE_TRADE", "PLUS_DE_RECHERCHE", "BLOCAGE")
DIS_LABELS = {"LOW": "faible", "MEDIUM": "moyen", "HIGH": "fort"}
LABELS = {"ACHAT": "achat", "CONSERVER": "conserver", "ATTENDRE": "attendre", "PAS_DE_TRADE": "pas de trade",
          "PLUS_DE_RECHERCHE": "plus de recherche", "BLOCAGE": "blocage"}
VERSION = "comite-1.0.0"         # version de l'équipe (agents et règles de recommandation)
BUY_MIN = 30.0                  # consensus minimal pour recommander un achat
NEEDED = ("donnees", "technique", "quant", "regime", "sentiment", "risque", "portefeuille", "sans_trade",
          "critique", "red_team", "verification")
CORR_MAX = 0.85                 # corrélation moyenne avec les positions : concentration


def _r(agent: str, stance: str, score: float, evidence: Sequence[str] = (), **kw: Any) -> AgentResult:
    score = max(-100.0, min(100.0, float(score)))
    return AgentResult(agent, stance, round(score, 1), tuple(evidence), **kw)


# ---------- Analystes ----------

def a_donnees(v: View) -> AgentResult:
    m, q = v["marche"], v.get("qualite", {})
    ev = [f"données notées {fr(q.get('score', 0), '.0f')}/100"] if q else []
    if m["last_day"] != m["day"]:
        return _r("donnees", "CONTRE", -60, ev + [f"dernière clôture du {m['last_day']}, attendue du {m['day']}"],
                  veto="données périmées")
    if q and q.get("score", 100) < 50:
        return _r("donnees", "CONTRE", -60, ev + q.get("issues", [])[:2], veto="données abîmées")
    return _r("donnees", "NEUTRE", 0, ev or ["clôture du jour présente"])


def a_technique(v: View) -> AgentResult:
    s = v["marche"]["snap"]
    if not ts._finite(s.get("close"), s.get("prior_high")):
        return _r("technique", "NEUTRE", 0, uncertainties=("historique trop court pour la cassure",))
    gap = (s["close"] / s["prior_high"] - 1) * 100
    if gap > 0:
        return _r("technique", "POUR", min(90, 55 + gap * 3),
                  [f"clôture {fr(s['close'], '.6g')} au-dessus du plus haut de 30 jours {fr(s['prior_high'], '.6g')} "
                   f"({fr(gap, '+.1f')} %)"])
    return _r("technique", "CONTRE" if gap < -5 else "NEUTRE", max(-60, gap * 4),
              [f"clôture à {fr(gap, '.1f')} % du plus haut de 30 jours : pas de cassure"])


def a_quant(v: View) -> AgentResult:
    s = v["marche"]["snap"]
    mom = s.get("mom")
    if not ts._finite(mom):
        return _r("quant", "NEUTRE", 0, uncertainties=("momentum incalculable",))
    ev = [f"momentum 90 jours {fr(mom, '+.2f')} (hausse rapportée à la volatilité)"]
    warn = ()
    if v["marche"].get("vol_rank") is not None and v["marche"]["vol_rank"] > 0.9:
        warn = ("volatilité dans les 10 % les plus fortes de l'année",)
    score = max(-80.0, min(80.0, mom * 30))
    return _r("quant", "POUR" if mom > 0.2 else "CONTRE" if mom < -0.2 else "NEUTRE", score, ev, uncertainties=warn)


def a_regime(v: View) -> AgentResult:
    rg = v["regime"]
    ev = [f"BTC {'au-dessus de' if rg['bull'] else 'sous'} sa moyenne de {rg['sma']} jours"] + (
        [rg["texte"]] if rg.get("texte") else [])
    if not rg["bull"]:
        return _r("regime", "CONTRE", -80, ev)
    score = 40.0
    if rg.get("volatilite") == "forte":
        score -= 15
    if rg.get("phase") == "crise":
        score -= 30
    return _r("regime", "POUR" if score > 20 else "NEUTRE", score, ev)


def a_sentiment(v: View) -> AgentResult:
    sv = v.get("savoir", {})
    o = (sv.get("opinion") or {}).get(v["marche"]["asset"])
    if not o:
        return _r("sentiment", "NEUTRE", 0, ["aucun avis de source prouvée aujourd'hui"],
                  uncertainties=("aucune source n'a encore prouvé qu'elle voit juste",))
    val = float(o["value"])
    return _r("sentiment", "POUR" if val > 0.2 else "CONTRE" if val < -0.2 else "NEUTRE", val * 60,
              [f"avis {fr(val, '+.2f')} de sources prouvées : {', '.join(o.get('sources') or [])}"])


def a_risque(v: View) -> AgentResult:
    s, pf, p = v["marche"]["snap"], v["portefeuille"], v["politique"]["params"]
    ev, veto = [], ""
    v30 = s.get("vol30")
    if ts._finite(v30):
        ev.append(f"échanges {fr(v30 / 1e6, '.1f')} M$ par jour (minimum {fr(p['min_volume_usd'] / 1e6, '.0f')})")
        if v30 < p["min_volume_usd"]:
            veto = "liquidité insuffisante"
    else:
        veto = "liquidité inconnue"
    held = pf.get("held") or {}
    if len(held) >= p["max_positions"] and v["marche"]["asset"] not in held:
        veto = veto or f"nombre maximal de positions atteint ({p['max_positions']})"
    room = p["max_total_risk"] * pf.get("risk_mult", 1.0) - sum(held.values()) / max(pf.get("equity") or 1, 1e-9)
    ev.append(f"budget de risque restant {fr(room * 100, '.1f')} % du capital")
    if room < p["risk_pct"] * pf.get("risk_mult", 1.0) - 1e-9 and v["marche"]["asset"] not in held:
        veto = veto or "budget de risque cumulé épuisé"
    if ts._finite(s.get("vol"), s.get("close")) and s["close"] > 0:
        stop_gap = p["init_stop_atr"] * s["vol"] / s["close"] * 100
        ev.append(f"stop initial à {fr(stop_gap, '.1f')} % sous le cours")
    return _r("risque", "CONTRE" if veto else "NEUTRE", -70 if veto else 0, ev, veto=veto)


def a_portefeuille(v: View) -> AgentResult:
    m, pf = v["marche"], v["portefeuille"]
    held = pf.get("held") or {}
    if m["asset"] in held:
        return _r("portefeuille", "NEUTRE", 0, ["déjà détenue"])
    corr = m.get("corr_held")
    if corr is None:
        return _r("portefeuille", "POUR", 20, ["aucune position : diversification sans objet"])
    if corr > CORR_MAX:
        return _r("portefeuille", "CONTRE", -30, [f"corrélation moyenne {fr(corr, '.2f')} avec les positions : "
                                                  "concentration"])
    return _r("portefeuille", "POUR", 20, [f"corrélation moyenne {fr(corr, '.2f')} avec les positions"])


def a_sans_trade(v: View) -> AgentResult:
    pol, rg, s = v["politique"], v["regime"], v["marche"]["snap"]
    reasons = []
    if pol.get("halted"):
        reasons.append("arrêt d'urgence déclenché")
    if pol.get("safe_mode"):
        reasons.append("mode sûr actif")
    reasons += [f"garde : {b}" for b in pol.get("garde_blocked") or []]
    if not rg["bull"]:
        reasons.append("marché baissier (règle : aucun achat)")
    if not ts.entry_signal(s, ts.TrendParams(**{k: pol["params"][k] for k in ("min_history", "min_volume_usd")})):
        reasons.append("pas de signal d'achat selon la règle")
    if reasons:
        return _r("sans_trade", "CONTRE", -60, reasons, veto=" ; ".join(reasons))
    return _r("sans_trade", "NEUTRE", 0, ["aucune raison de s'abstenir trouvée"])


# ---------- Contrôle ----------

def a_critique(v: View) -> AgentResult:
    avis = v["avis"]
    ch = []
    for k, r in avis.items():
        if r.uncertainties and abs(r.score) >= 30:
            ch.append(Challenge("critique", k, f"avis tranché malgré une incertitude : {r.uncertainties[0]}"))
    t, q = avis.get("technique"), avis.get("quant")
    if t and q and t.score > 0 > q.score:
        ch.append(Challenge("critique", "technique", "cassure sans momentum : signaux contraires", factor=0.7))
    s = avis.get("sentiment")
    if s and s.score and not any("prouvées" in e for e in s.evidence):
        ch.append(Challenge("critique", "sentiment", "avis non prouvé sur les cours réels", "nullify"))
    return _r("critique", "NEUTRE", 0, [f"{len(ch)} objection(s)"], challenges=tuple(ch))


def a_red_team(v: View) -> AgentResult:
    m, rank = v["marche"], v.get("historique", {})
    ch = []
    rec = rank.get(m["asset"]) if rank else None
    if rec and rec.get("trades", 0) >= 3 and rec.get("total_r", 0) < 0:
        ch.append(Challenge("red_team", "technique", f"sur 2 ans, la règle a perdu sur cette crypto "
                                                     f"({fr(rec['total_r'], '+.1f')} R en {rec['trades']} trades)",
                            factor=0.6))
    rg = v["regime"]
    if rg.get("volatilite") == "forte":
        ch.append(Challenge("red_team", "regime", "volatilité forte : un krach peut sauter les stops", factor=0.7))
    if m.get("vol_rank") is not None and m["vol_rank"] > 0.9:
        ch.append(Challenge("red_team", "quant", "volatilité extrême : la hausse peut s'inverser vite", factor=0.7))
    return _r("red_team", "NEUTRE", 0, [f"{len(ch)} scénario(s) défavorable(s)"], challenges=tuple(ch))


def a_verification(v: View) -> AgentResult:
    """Vérification indépendante : recalcule la cassure et le signe du
    momentum directement sur les clôtures, sans les indicateurs du bot."""
    m, avis = v["marche"], v["avis"]
    c = m["closes"]
    ch = []
    if len(c) >= 32:
        brk = c[-1] > max(c[-31:-1])
        t = avis.get("technique")
        if t and (t.score > 0) != brk:
            ch.append(Challenge("verification", "technique", "cassure non confirmée par le recalcul", "nullify"))
    if len(c) >= 92:
        up = c[-1] > c[-91]
        q = avis.get("quant")
        if q and abs(q.score) >= 10 and (q.score > 0) != up:
            ch.append(Challenge("verification", "quant", "sens du momentum non confirmé par le recalcul", "nullify"))
    return _r("verification", "NEUTRE", 0, ["cassure et momentum recalculés indépendamment"
                                           if not ch else f"{len(ch)} résultat(s) non confirmé(s)"],
              challenges=tuple(ch))


AGENTS: Tuple[Agent, ...] = (
    Agent(Manifest("donnees", "1.0.0", "qualité des données", "analyse", ("donnees",), ("marche", "qualite"),
                   weight=0.0, veto=True, critical=True), a_donnees),
    Agent(Manifest("technique", "1.0.0", "analyste technique", "analyse", ("technique",), ("marche",),
                   critical=True), a_technique),
    Agent(Manifest("quant", "1.0.0", "analyste quantitatif", "analyse", ("quant",), ("marche",)), a_quant),
    Agent(Manifest("regime", "1.0.0", "régime de marché", "analyse", ("regime",), ("regime",), critical=True),
          a_regime),
    Agent(Manifest("sentiment", "1.0.0", "sentiment (noyau de savoir)", "analyse", ("sentiment",),
                   ("marche", "savoir"), weight=0.5), a_sentiment),
    Agent(Manifest("risque", "1.0.0", "gestionnaire du risque", "analyse", ("risque",),
                   ("marche", "portefeuille", "politique"), weight=1.0, veto=True, critical=True), a_risque),
    Agent(Manifest("portefeuille", "1.0.0", "gérant du portefeuille", "analyse", ("portefeuille",),
                   ("marche", "portefeuille"), weight=0.5), a_portefeuille),
    Agent(Manifest("sans_trade", "1.0.0", "agent « pas de trade »", "analyse", ("sans_trade",),
                   ("marche", "regime", "politique"), weight=0.0, veto=True), a_sans_trade),
    Agent(Manifest("critique", "1.0.0", "critique", "contrôle", ("critique",), ("avis",), weight=0.0), a_critique),
    Agent(Manifest("red_team", "1.0.0", "équipe rouge", "contrôle", ("red_team",),
                   ("avis", "marche", "regime", "historique"), weight=0.0), a_red_team),
    Agent(Manifest("verification", "1.0.0", "vérificateur indépendant", "contrôle", ("verification",),
                   ("avis", "marche"), weight=0.0), a_verification),
)


def registry() -> AgentRegistry:
    return AgentRegistry(AGENTS)


# ---------- Recommandation ----------

@dataclass(frozen=True)
class Avis:
    asset: str
    recommendation: str
    consensus: float
    strength: str
    disagreement: str
    reasons: Tuple[str, ...]
    votes: Dict[str, Tuple[str, float]]
    authorized: bool = False

    def __post_init__(self) -> None:
        if self.recommendation not in RECOMMENDATIONS:
            raise ValueError(f"recommandation inconnue : {self.recommendation}")
        if self.authorized:
            raise ValueError("le comité ne peut pas autoriser un achat")


def recommend(asset: str, res: MissionResult, held: bool, data_quality: Optional[float],
              manifests: Dict[str, Manifest]) -> Avis:
    """La recommandation du comité, dans cet ordre : agent critique absent
    → BLOCAGE ; veto → PAS_DE_TRADE ; données faibles → PLUS_DE_RECHERCHE ;
    désaccord fort → ATTENDRE ; déjà détenue → CONSERVER ; consensus fort et
    cassure confirmée → ACHAT ; sinon ATTENDRE."""
    value, strength = res.consensus
    level, _spread = res.disagreement
    votes = {k: (r.stance, r.score * res.factors.get(k, 1.0)) for k, r in res.results.items()}
    critical_missing = [k for k, m in manifests.items() if m.critical and k not in res.results]
    if critical_missing or res.violations:
        why = [f"agent critique absent : {', '.join(critical_missing)}"] if critical_missing else []
        rec, reasons = "BLOCAGE", why + res.violations
    elif res.vetoes:
        rec, reasons = "PAS_DE_TRADE", res.vetoes
    elif data_quality is not None and data_quality < 50:
        rec, reasons = "PLUS_DE_RECHERCHE", [f"données notées {fr(data_quality, '.0f')}/100"]
    elif level == "HIGH":
        rec, reasons = "ATTENDRE", ["désaccord fort entre les analystes"]
    elif held:
        rec, reasons = "CONSERVER", ["déjà détenue : les stops décident de la vente"]
    elif value >= BUY_MIN and res.factors.get("technique", 0) > 0 and res.results["technique"].score > 0:
        rec, reasons = "ACHAT", [f"consensus {strength} ({fr(value, '+.0f')})"]
    else:
        rec, reasons = "ATTENDRE", [f"consensus {strength} ({fr(value, '+.0f')}), insuffisant pour un achat"]
    reasons += [f"objection {c.by} → {c.target} : {c.reason}" for c in res.challenges if c.effect != "veto"][:3]
    return Avis(asset, rec, value, strength, level, tuple(reasons), votes)


def board(asset: str, day: str, close: pd.DataFrame, snap: Dict[str, float], p: ts.TrendParams,
          held: Dict[str, float], equity: float, risk_mult: float, policy: Dict[str, Any],
          opinion: Optional[Dict[str, Any]] = None, ranking: Optional[Dict[str, Any]] = None,
          regime: Optional[Dict[str, Any]] = None, quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Le tableau noir d'une mission : les données de chaque rubrique, que
    chaque agent ne lit que si son manifeste le permet (`regime` : régime du
    jour déjà calculé par le bot, sinon recalculé ; `quality` : note des
    données du jour, sinon celle des clôtures de la crypto)."""
    c = close[asset].loc[:pd.Timestamp(day, tz="UTC")].dropna()
    last_day = str(c.index[-1].date()) if len(c) else ""
    rets = np.log(c / c.shift(1)).dropna()
    vol = rets.rolling(30).std().dropna()
    vol_rank = float((vol.iloc[-365:] <= vol.iloc[-1]).mean()) if len(vol) >= 60 else None
    corr = None
    others = [a for a in held if a != asset and a in close.columns]
    if others:
        win = close[[asset] + others].loc[:pd.Timestamp(day, tz="UTC")].iloc[-91:].pct_change().dropna()
        if len(win) >= 30:
            corr = float(win.corr()[asset].drop(asset).mean())
    rg = regime if regime is not None else regimes.at(
        regimes.regime_frame(close.loc[:pd.Timestamp(day, tz="UTC")], p.regime_sma), day) or {}
    btc = close["btc"].loc[:pd.Timestamp(day, tz="UTC")].dropna()
    bull = bool(len(btc) >= p.regime_sma and btc.iloc[-1] > btc.iloc[-p.regime_sma:].mean())
    return {
        "marche": {"asset": asset, "day": day, "last_day": last_day, "snap": dict(snap), "vol_rank": vol_rank,
                   "corr_held": corr, "closes": [float(x) for x in c.iloc[-200:]]},
        "qualite": quality or qualite.quality(close[[asset]].loc[:pd.Timestamp(day, tz="UTC")], day),
        "regime": {"bull": bull, "sma": p.regime_sma, **{k: rg.get(k) for k in ("texte", "volatilite", "phase")}},
        "savoir": {"opinion": opinion or {}},
        "portefeuille": {"held": dict(held), "equity": equity, "risk_mult": risk_mult},
        "politique": dict(policy, params={k: getattr(p, k) for k in (
            "min_volume_usd", "max_positions", "max_total_risk", "risk_pct", "init_stop_atr", "min_history")}),
        "historique": ranking or {},
    }


def evaluate(asset: str, data: Dict[str, Any], reg: Optional[AgentRegistry] = None) -> Tuple[Avis, MissionResult]:
    """Une mission « évaluer un achat » : équipe, analyses, contrôles,
    débat, consensus, recommandation."""
    reg = reg or registry()
    res = Supervisor(reg).run(f"évaluer l'achat de {asset.upper()}", NEEDED, data)
    manifests = {k: r.agent.manifest for k, r in reg.records.items()}
    held = asset in (data["portefeuille"].get("held") or {})
    view = recommend(asset, res, held, (data.get("qualite") or {}).get("score"), manifests)
    return view, res


def summary(view: Avis) -> str:
    """Une ligne : recommandation, consensus, désaccord, raison principale."""
    pros = sum(1 for s, sc in view.votes.values() if s == "POUR" and sc)
    cons = sum(1 for s, sc in view.votes.values() if s == "CONTRE" and sc)
    return (f"{view.asset.upper()} : {LABELS[view.recommendation]} (consensus {fr(view.consensus, '+.0f')}, "
            f"{pros} pour, {cons} contre, désaccord {DIS_LABELS[view.disagreement]}) — {view.reasons[0]}")


def as_dict(view: Avis) -> Dict[str, Any]:
    return {"asset": view.asset, "recommendation": view.recommendation, "consensus": view.consensus,
            "strength": view.strength, "disagreement": view.disagreement, "reasons": list(view.reasons),
            "votes": {k: [s, round(sc, 1)] for k, (s, sc) in view.votes.items()}, "text": summary(view)}


# ---------- Banc d'essai (scénarios de référence) ----------

def _scenario(kind: str) -> Tuple[Dict[str, Any], str]:
    idx = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    up = 100 * np.exp(np.cumsum(0.004 + rng.normal(0, 0.02, 400)))
    down = 100 * np.exp(np.cumsum(-0.004 + rng.normal(0, 0.02, 400)))
    close = pd.DataFrame({"btc": down if kind == "baissier" else up, "aave": up}, index=idx)
    close.iloc[-1, 1] = close["aave"].iloc[-31:-1].max() * 1.04          # cassure du jour
    day = str(idx[-1].date())
    p = ts.TrendParams(max_positions=20, max_total_risk=0.10)
    f = ts.asset_features(close["aave"], p, pd.Series(2e7 if kind != "illiquide" else 1e6, index=idx))
    snap = {k: float(f[k].iloc[-1]) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    if kind == "perimee":
        close = close.iloc[:-1]
    policy = {"halted": False, "safe_mode": False, "garde_blocked": ["BTC −18 % en un jour"] if kind == "garde" else []}
    # Signaux contraires : sources prouvées très négatives, et une position déjà
    # détenue qui évolue comme elle (concentration).
    held = {"aave": 1.0} if kind == "detenue" else {"btc": 1.0} if kind == "contraire" else {}
    opinion = {"aave": {"value": -0.9, "sources": ["Oracle"]}} if kind == "contraire" else {}
    return board("aave", day, close, snap, p, held, 100.0, 1.0, policy, opinion), day


BENCHMARK = (("tendance nette", "nette", "ACHAT"), ("marché baissier", "baissier", "PAS_DE_TRADE"),
             ("crypto peu échangée", "illiquide", "PAS_DE_TRADE"), ("données périmées", "perimee", "PAS_DE_TRADE"),
             ("garde du jour", "garde", "PAS_DE_TRADE"), ("déjà détenue", "detenue", "CONSERVER"),
             ("signaux contraires", "contraire", "ATTENDRE"),
             ("agent du risque en panne", "panne_risque", "BLOCAGE"))


def benchmark() -> List[Dict[str, Any]]:
    """Les scénarios de référence du comité, et s'il rend la recommandation
    attendue (le résultat de chaque scénario est connu d'avance)."""
    out = []
    for name, kind, expected in BENCHMARK:
        reg = registry()
        if kind == "panne_risque":
            reg.quarantine("risque", "panne simulée")
        data, _day = _scenario("nette" if kind == "panne_risque" else kind)
        view, _res = evaluate("aave", data, reg)
        out.append({"scenario": name, "expected": expected, "got": view.recommendation,
                    "pass": view.recommendation == expected})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `comite` : l'avis du comité sur une crypto, ou le banc
    d'essai."""
    import os

    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Comité d'agents financiers de TrendGuard (consultatif)")
    ap.add_argument("crypto", nargs="?", default="", help="ex. aave")
    ap.add_argument("--banc", action="store_true", help="scénarios de référence")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    if args.banc or not args.crypto:
        rows = benchmark()
        for r in rows:
            print(f"  {'✓' if r['pass'] else '✗'} {r['scenario']} : attendu {r['expected']}, obtenu {r['got']}")
        print(f"{sum(r['pass'] for r in rows)} scénario(s) réussi(s) sur {len(rows)}")
        return 0 if all(r["pass"] for r in rows) else 1
    g = load_guard_config_from_env()
    asset = args.crypto.lower()
    close, volume = load_history(args.cache, list(g.universe))
    if asset not in close.columns:
        print(f"Crypto inconnue : {asset.upper()}")
        return 1
    p = params_for(g)
    day = str(close.index[-1].date())
    f = ts.asset_features(close[asset], p, volume[asset] if asset in volume else None)
    snap = {k: float(f[k].iloc[-1]) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    st = read_state(g.db_file)
    held = {a: float(h.get("risk_quote") or 0) for a, h in ((st.get("paper") or {}).get("holdings") or {}).items()}
    g_ = st.get("garde") or {}
    policy = {"halted": bool(st.get("halted")), "safe_mode": bool((st.get("mode_sur") or {}).get("active")),
              "garde_blocked": g_.get("blocked") or [] if g_.get("day") == day else []}
    ranking = {r["asset"]: r for r in ((st.get("selection") or {}).get("ranking") or [])}
    data = board(asset, day, close, snap, p, held, float(st.get("last_equity") or 0) or 1.0,
                 float(st.get("risk_mult") or 1.0), policy, (st.get("savoir") or {}).get("opinion"), ranking)
    view, res = evaluate(asset, data)
    print(f"COMITÉ D'AGENTS — {asset.upper()}, bougie du {day} (consultatif : la règle et la porte décident)")
    for k, r in res.results.items():
        print(f"  {k:<12} {r.stance:<7} {fr(r.score * res.factors.get(k, 1.0), '+.0f'):>5}  "
              f"{'; '.join(r.evidence)}" + (f"  [veto : {r.veto}]" if r.veto else ""))
    for c in res.challenges:
        print(f"  objection {c.by} → {c.target} : {c.reason}")
    print(f"Désaccord : {DIS_LABELS[res.disagreement[0]]} ; consensus {fr(res.consensus[0], '+.0f')} ({res.consensus[1]})")
    print(f"Recommandation : {LABELS[view.recommendation].upper()} — " + " ; ".join(view.reasons))
    return 0

