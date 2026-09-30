"""Raisonnement du jour, en clair (tableau de bord et page Cryptos).

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import trend_strategy as ts
from .texte import fr

EXIT_WHY = {"STOP": "clôture sous son stop suiveur, la tendance s'essouffle",
            "STOP_LATE": "stop franchi pendant l'arrêt du bot",
            "DELISTED": "plus cotée sur Binance", "DELISTED_LATE": "plus cotée sur Binance",
            "EXCHANGE_STOP": "stop catastrophe, chute brutale entre deux clôtures"}
# En deux mots (résumé du jour, journal) : les mêmes que le panneau (REASON, core.js).
EXIT_SHORT = {"STOP": "stop de clôture", "STOP_LATE": "stop, rattrapage",
              "DELISTED": "retrait de la cote", "DELISTED_LATE": "retrait de la cote",
              "EXCHANGE_STOP": "stop catastrophe"}


WATCH_BAND_PCT = 5.0        # « sous surveillance » : à moins de 5 % de la cassure


def _pc(x: float, d: int = 1) -> str:
    """0.021 → « +2,1 % » (format français)."""
    return fr(x * 100, f"+.{d}f") + " %"


def _explain_asset(a: str, s: Dict[str, float], gap: Optional[float], bull: bool,
                   holdings: Dict[str, ts.Holding], sold: Dict[str, str], bought: List[str],
                   notes: Dict[str, Tuple[str, str]], halted: bool,
                   p: ts.TrendParams, equity: Optional[float] = None,
                   mult: float = 1.0) -> Tuple[str, str]:
    if a in sold:
        return "sold", "Vendue : " + EXIT_WHY.get(sold[a], sold[a].lower())
    if a in bought:
        return "bought", (f"Achetée : cassure de son plus haut de {p.breakout_n} jours, tendance "
                          f"de fond positive, {fr(p.risk_pct * 100, 'g')} % du capital risqué")
    if a in holdings:
        h, close = holdings[a], s.get("close")
        if ts._finite(close) and close > 0:
            return "held", (f"En portefeuille ({_pc(close / h.entry - 1)}) : la tendance "
                            f"tient, stop à {_pc(h.stop / close - 1)} du cours")
        return "held", "En portefeuille : la tendance tient"
    if a in notes:
        return notes[a]
    if not s or not ts._finite(s.get("close"), s.get("prior_high"), s.get("vol"), s.get("mom")):
        return "nodata", "Pas encore assez de données"
    if s.get("age", 0) < p.min_history:
        return "young", f"Historique trop court (moins de {p.min_history} jours de cotation)"
    v30 = s.get("vol30")
    if not ts._finite(v30) or v30 < p.min_volume_usd:
        traded = fr(v30 / 1e6, ".1f") + " M$" if ts._finite(v30) else "inconnu"
        return "illiquid", (f"Pas assez échangée sur Binance ({traded} par jour, minimum "
                            f"{p.min_volume_usd / 1e6:.0f} M$)")
    if s["mom"] <= 0:
        return "weak", "Tendance de fond (90 jours) négative"
    if s["close"] <= s["prior_high"]:
        need = _pc((gap or 0.0) / 100)
        if gap is not None and gap <= WATCH_BAND_PCT:
            return "watch", (f"Sous surveillance : encore {need} pour casser son plus haut de "
                             f"{p.breakout_n} jours")
        return "wait", f"Pas de cassure : il lui faut {need} pour dépasser son plus haut de {p.breakout_n} jours"
    if not bull:
        return "bear", ("Signal d'achat, mais marché baissier : le bot attend le retour de BTC "
                        "au-dessus de sa moyenne")
    if halted:
        return "halted", "Signal d'achat, mais arrêt d'urgence actif"
    if len(holdings) >= p.max_positions:
        return "full", (f"Signal d'achat, mais {len(holdings)} positions sont déjà ouvertes "
                        f"(maximum {p.max_positions}) : achat dès qu'une position sera vendue")
    if equity:
        engaged = sum(h.risk_quote for h in holdings.values()) / equity * 100
        if engaged + p.risk_pct * mult * 100 > p.max_total_risk * mult * 100 + 1e-9:
            return "full", (f"Signal d'achat, mais plafond de risque cumulé atteint "
                            f"({fr(engaged, '.1f')} % engagés sur "
                            f"{fr(p.max_total_risk * mult * 100, '.0f')} % permis) : achat dès "
                            "qu'une position sera vendue")
        return "full", ("Signal d'achat, mais les achats mieux classés du jour ou les liquidités "
                        "disponibles ont pris la place")
    return "full", "Signal d'achat, mais plafond atteint (positions, risque total ou liquidités)"


def explain_decision(day: str, bull: bool, btc_gap: Optional[float],
                     snap: Dict[str, Dict[str, float]], holdings: Dict[str, ts.Holding],
                     exits: List[Tuple[str, str]], bought: List[str],
                     notes: Dict[str, Tuple[str, str]], halted: bool, mult: float,
                     p: ts.TrendParams, equity: Optional[float] = None) -> Dict[str, Any]:
    """Raisonnement de la décision du jour, actif par actif : ce que le bot
    a fait, pourquoi il n'a pas acheté les autres, et ce qu'il guette.
    Mêmes règles que la décision elle-même (trend_strategy.entry_signal)."""
    sold = dict(exits)
    assets: Dict[str, Dict[str, Any]] = {}
    for a in sorted(set(snap) | set(holdings) | set(sold)):
        s = snap.get(a) or {}
        close, hi = s.get("close"), s.get("prior_high")
        gap = (hi / close - 1) * 100 if ts._finite(close, hi) and close > 0 else None
        status, text = _explain_asset(a, s, gap, bull, holdings, sold, bought, notes, halted, p,
                                      equity, mult)
        assets[a] = {"status": status, "text": text,
                     "breakout_gap_pct": round(gap, 2) if gap is not None else None}
    radar = sorted((a for a, x in assets.items() if x["status"] == "watch"),
                   key=lambda a: assets[a]["breakout_gap_pct"])
    btc = f" ({_pc(btc_gap / 100)})" if btc_gap is not None else ""
    lines = [f"Marché haussier : BTC au-dessus de sa moyenne {p.regime_sma} jours{btc}, achats autorisés."
             if bull else
             f"Marché baissier : BTC sous sa moyenne {p.regime_sma} jours{btc}, aucun achat et stops "
             f"resserrés pour protéger les gains."]
    acts = []
    if bought:
        acts.append(f"{len(bought)} achat(s) : {', '.join(a.upper() for a in bought)}")
    if sold:
        acts.append(f"{len(sold)} vente(s) : {', '.join(a.upper() for a in sold)}")
    if acts:
        lines.append("Aujourd'hui : " + " ; ".join(acts) + ".")
    elif holdings:
        lines.append(f"Aujourd'hui : aucun changement, {len(holdings)} position(s) conservée(s).")
    else:
        lines.append("Aujourd'hui : aucun achat, capital à l'abri en USDT.")
    deferred = [a.upper() for a, (st, _t) in sorted(notes.items()) if st == "deferred"]
    if deferred:
        lines.append(f"Ruse : achat de {', '.join(deferred)} différé (conditions d'achat "
                     f"anormales), nouvel essai toutes les 5 min.")
    if radar:
        lines.append("Sous surveillance : " + ", ".join(
            f"{a.upper()} ({_pc(assets[a]['breakout_gap_pct'] / 100)})" for a in radar[:3])
            + " avant la cassure.")
    if mult < 1:
        lines.append(f"Profil prudent actif : risque par trade × {fr(mult, 'g')}.")
    if halted:
        lines.append("Arrêt d'urgence actif : aucun achat.")
    return {"day": day, "bull": bull,
            "btc_gap_pct": round(btc_gap, 2) if btc_gap is not None else None,
            "lines": lines, "assets": assets, "radar": radar[:5]}
