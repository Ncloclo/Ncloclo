"""
Anticipation : ce que le bot fera probablement à la prochaine clôture.

Les règles ne changent pas (trend_strategy.py). Ce module les applique à
l'avance, avec les cours du moment :

- VENTES : chaque position est vendue si la clôture passe sous son stop
  suiveur. Niveau exact, distance au cours, probabilité d'ici la clôture
  (volatilité de la crypto, temps restant), gain ou perte verrouillés.
- ACHATS : une crypto est achetée si sa clôture dépasse son plus haut des
  30 dernières clôtures (niveau connu dès maintenant), au-dessus de son
  cours d'il y a 90 jours, marché haussier, liquidité suffisante, crypto
  sélectionnée et place disponible dans le plafond de risque.
- MARCHÉ : niveau de BTC sous lequel le bot n'achèterait plus.
- RISQUE : risque engagé, places restantes, perte si tous les stops sont
  touchés ce soir.
- CONSEILS : ce qu'il faut en retenir, en phrases simples.

Probabilités : modèle simple (mouvement d'ici la clôture de loi normale,
écart-type tiré de la volatilité récente de la crypto), corrigé par
l'expérience du bot dès qu'elle suffit (learning.py : chaque prévision est
comparée à la clôture). Ce sont des ordres de grandeur pour se préparer, pas
des prévisions de prix.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from . import trend_strategy as ts

SQRT_PI_2 = math.sqrt(math.pi / 2)     # |Δ| moyen → écart-type (loi normale)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sigma_to_close(vol: float, hours_left: float) -> float:
    """Écart-type du cours d'ici la clôture, à partir de `vol` (moyenne
    exponentielle des variations journalières absolues, en prix)."""
    return max(vol, 1e-12) * SQRT_PI_2 * math.sqrt(max(hours_left, 0.25) / 24.0)


def prob_below(price: float, level: float, sigma: float) -> float:
    return norm_cdf((level - price) / sigma) if sigma > 0 else float(price < level)


def basis_from_market(close: pd.DataFrame, feats: Dict[str, pd.DataFrame], day: str,
                      p: ts.TrendParams) -> Dict[str, Any]:
    """Niveaux de la PROCHAINE décision, connus dès la clôture `day` :
    plus haut des 30 dernières clôtures, cours d'il y a 90 jours (tendance de
    fond), volatilité, liquidité ; seuil du régime BTC."""
    i = close.index.get_loc(pd.Timestamp(day, tz="UTC"))
    out: Dict[str, Any] = {}
    for a in close.columns:
        c = close[a]
        f = feats.get(a)
        if f is None or pd.isna(c.iloc[i]):
            continue
        j = i - p.mom_n + 1
        vol30, age, vol = f["vol30"].iloc[i], f["age"].iloc[i], f["vol"].iloc[i]
        out[a] = {
            "close": float(c.iloc[i]),
            "buy_trigger": float(c.iloc[max(0, i - p.breakout_n + 1):i + 1].max()),
            "mom_ref": float(c.iloc[j]) if j >= 0 and not pd.isna(c.iloc[j]) else None,
            "vol": float(vol) if ts._finite(float(vol)) else None,
            "liquid": bool(ts._finite(float(vol30)) and vol30 >= p.min_volume_usd
                           and age + 1 >= p.min_history)}
    btc = close["btc"]
    lo = i - p.regime_sma + 2
    thr = float(btc.iloc[lo:i + 1].mean()) if lo >= 0 else None
    next_close = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=2)
    return {"day": day, "next_close": next_close.isoformat(), "assets": out,
            "btc_threshold": thr if thr is not None and math.isfinite(thr) else None}


def _sells(assets: Dict[str, Any], prices: Dict[str, float], holdings: List[Dict[str, Any]],
           hours: float, p: ts.TrendParams) -> List[Dict[str, Any]]:
    """Ventes possibles : chaque position sous son stop de clôture."""
    sells = []
    for h in holdings:
        a, px, b = h["asset"], prices.get(h["asset"]), assets.get(h["asset"]) or {}
        if not px or not h.get("stop"):
            continue
        sig = sigma_to_close(b.get("vol") or px * 0.03, hours)
        stop = float(h["stop"])
        sells.append({
            "asset": a, "price": px, "stop": stop, "disaster": h.get("disaster"),
            "dist_pct": round((stop / px - 1) * 100, 2),
            "prob": round(prob_below(px, stop, sig), 3),
            "locked_pct": round((stop / h["entry"] - 1) * 100, 2) if h.get("entry") else None,
            "at_stop_usdt": round(h["qty"] * stop * (1 - p.fee - p.slippage) - (h.get("cost") or 0), 2),
            "given_back_usdt": round(h["qty"] * max(0.0, px - stop), 2)})
    sells.sort(key=lambda s: -s["prob"])
    return sells


def _regime(basis: Dict[str, Any], assets: Dict[str, Any], prices: Dict[str, float],
            hours: float) -> Optional[Dict[str, Any]]:
    """Niveau de BTC sous lequel le bot n'achèterait plus (None si inconnu)."""
    btc_px, thr = prices.get("btc"), basis.get("btc_threshold")
    if not (btc_px and thr):
        return None
    sig = sigma_to_close((assets.get("btc") or {}).get("vol") or btc_px * 0.03, hours)
    return {"price": btc_px, "threshold": thr, "dist_pct": round((thr / btc_px - 1) * 100, 2),
            "bull_now": btc_px > thr, "prob_bear": round(prob_below(btc_px, thr, sig), 3)}


def _buy_blocks(a: str, b: Dict[str, Any], allowed: set, vetoed: set, bull: bool,
                halted: bool, slots: int) -> List[str]:
    """Ce qui empêcherait l'achat même si la cassure a lieu."""
    blocked = []
    if a not in allowed:
        blocked.append("non sélectionnée")
    if a in vetoed:
        blocked.append("bloquée par la veille")
    if not b.get("liquid"):
        blocked.append("trop peu échangée")
    if not bull:
        blocked.append("marché baissier")
    if halted:
        blocked.append("arrêt d'urgence")
    elif slots == 0:
        blocked.append("plafond de risque atteint")
    return blocked


def _buys(assets: Dict[str, Any], prices: Dict[str, float], held: set, hours: float,
          blocks: Callable[[str, Dict[str, Any]], List[str]]) -> List[Dict[str, Any]]:
    """Achats possibles : il faut clôturer au-dessus du plus haut de 30 jours
    ET du cours d'il y a 90 jours (tendance de fond positive)."""
    buys = []
    for a, b in assets.items():
        px = prices.get(a)
        if a in held or not px or not b.get("buy_trigger"):
            continue
        trig = max(b["buy_trigger"], b.get("mom_ref") or 0.0)
        sig = sigma_to_close(b.get("vol") or px * 0.03, hours)
        prob = 1.0 - prob_below(px, trig, sig)
        dist = (trig / px - 1) * 100
        if prob >= 0.01 or dist <= 10:
            buys.append({"asset": a, "price": px, "trigger": trig, "dist_pct": round(dist, 2),
                         "prob": round(prob, 3), "blocked": blocks(a, b)})
    buys.sort(key=lambda x: (bool(x["blocked"]), -x["prob"]))
    return buys[:12]


def _calibrate(sells: List[Dict[str, Any]], buys: List[Dict[str, Any]],
               regime: Optional[Dict[str, Any]], calibrate: Callable[[str, float], float]) -> None:
    """Probabilités corrigées par l'expérience du bot (learning.py) ; celles
    du modèle restent dans prob_model."""
    for s in sells:
        s["prob_model"], s["prob"] = s["prob"], round(calibrate("sell", s["prob"]), 3)
    sells.sort(key=lambda s: -s["prob"])
    for b in buys:
        b["prob_model"], b["prob"] = b["prob"], round(calibrate("buy", b["prob"]), 3)
    buys.sort(key=lambda x: (bool(x["blocked"]), -x["prob"]))
    if regime:
        regime["prob_bear_model"] = regime["prob_bear"]
        regime["prob_bear"] = round(calibrate("bear", regime["prob_bear"]), 3)


def forecast(basis: Dict[str, Any], prices: Dict[str, float], holdings: List[Dict[str, Any]],
             now: datetime, p: ts.TrendParams, equity: float, mult: float = 1.0,
             allowed: Optional[Iterable[str]] = None, vetoed: Iterable[str] = (),
             halted: bool = False,
             calibrate: Optional[Callable[[str, float], float]] = None) -> Dict[str, Any]:
    """Anticipation de la prochaine décision avec les cours du moment.
    holdings : [{asset, qty, entry, stop, disaster, risk, cost}].
    calibrate : correction des probabilités apprise (learning.calibrator)."""
    close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
    hours = max(0.0, (close_at - now).total_seconds() / 3600)
    assets = basis.get("assets") or {}
    allowed = set(allowed) if allowed is not None else set(assets)
    vetoed = set(vetoed)
    sells = _sells(assets, prices, holdings, hours, p)
    regime = _regime(basis, assets, prices, hours)
    bull_tonight = regime["bull_now"] if regime else True

    open_risk = sum(float(h.get("risk") or 0) for h in holdings)
    per_trade = p.risk_pct * equity * mult
    budget = p.max_total_risk * equity * mult
    slots = 0
    if per_trade > 0 and not halted:
        slots = max(0, min(p.max_positions - len(holdings),
                           int((budget - open_risk + 1e-9) // per_trade)))
    buys = _buys(assets, prices, {h["asset"] for h in holdings}, hours,
                 lambda a, b: _buy_blocks(a, b, allowed, vetoed, bull_tonight, halted, slots))
    if calibrate is not None:
        _calibrate(sells, buys, regime, calibrate)

    stop_loss = sum(s["given_back_usdt"] for s in sells)
    risk = {"open_risk_usdt": round(open_risk, 2), "open_risk_pct": round(open_risk / equity * 100, 2) if equity else None,
            "budget_pct": round(p.max_total_risk * mult * 100, 2), "slots": slots,
            "positions": len(holdings), "max_positions": p.max_positions,
            "all_stops_usdt": round(stop_loss, 2),
            "all_stops_pct": round(stop_loss / equity * 100, 2) if equity else None}
    out = {"hours_left": round(hours, 2), "next_close": basis["next_close"], "basis_day": basis["day"],
           "sells": sells, "buys": buys, "regime": regime, "risk": risk,
           "calibrated": calibrate is not None}
    out["advice"] = advice(out)
    return out


def _fr(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def _px(v: float) -> str:
    a = abs(v)
    return _fr(v, 0 if a >= 1000 else 2 if a >= 1 else 4 if a >= 0.1 else 5)


def advice(f: Dict[str, Any]) -> List[str]:
    """Conseils tirés de l'anticipation : ce qu'il faut savoir, pas des
    conseils d'investissement."""
    tips: List[str] = []
    likely_sells = [s for s in f["sells"] if s["prob"] >= 0.5]
    for s in likely_sells[:3]:
        tips.append(f"{s['asset'].upper()} sera probablement vendue ce soir "
                    f"(clôture sous {_px(s['stop'])}, probabilité {round(s['prob'] * 100)} %) : "
                    f"c'est la règle qui limite la perte, rien à faire.")
    near = [s for s in f["sells"] if 0.15 <= s["prob"] < 0.5]
    if near:
        tips.append("À surveiller côté ventes : " + ", ".join(
            f"{s['asset'].upper()} ({round(s['prob'] * 100)} %)" for s in near[:4]) + ".")
    free = [b for b in f["buys"] if not b["blocked"] and b["prob"] >= 0.3]
    for b in free[:3]:
        tips.append(f"Achat possible ce soir : {b['asset'].upper()} si la clôture dépasse "
                    f"{_px(b['trigger'])} (probabilité {round(b['prob'] * 100)} %).")
    capped = [b for b in f["buys"] if b["blocked"] == ["plafond de risque atteint"] and b["prob"] >= 0.3]
    if capped:
        tips.append("Plafond de risque atteint : " + ", ".join(b["asset"].upper() for b in capped[:4])
                    + " attendront qu'une position soit vendue, pour ne jamais risquer plus de "
                    f"{_fr(f['risk']['budget_pct'], 0)} % du capital.")
    r = f.get("regime")
    if r and r["bull_now"] and r["prob_bear"] >= 0.1:
        tips.append(f"Marché proche de basculer : sous {_px(r['threshold'])} $ à la clôture, BTC "
                    f"repasserait sous sa moyenne 150 jours et le bot n'achèterait plus "
                    f"(probabilité {round(r['prob_bear'] * 100)} %).")
    elif r and not r["bull_now"]:
        tips.append(f"Marché baissier : aucun achat tant que BTC reste sous {_px(r['threshold'])} $.")
    risk = f["risk"]
    if risk["positions"] and risk["all_stops_pct"] is not None:
        tips.append(f"Pire cas ce soir, si tous les stops étaient touchés : "
                    f"−{_fr(risk['all_stops_pct'])} % du capital par rapport à maintenant.")
    if not tips:
        tips.append("Rien de particulier à prévoir d'ici la prochaine clôture.")
    return tips


def alerts_to_send(f: Dict[str, Any], sent: Iterable[str], threshold: float = 0.6) -> List[Dict[str, str]]:
    """Alertes d'anticipation (une seule fois par crypto et par soir)."""
    sent = set(sent)
    out = []
    for s in f["sells"]:
        key = f"vente:{s['asset']}"
        if s["prob"] >= threshold and key not in sent:
            out.append({"key": key, "text": (
                f"📉 Vente probable ce soir : {s['asset'].upper()} si la clôture passe sous "
                f"{_px(s['stop'])} (cours {_px(s['price'])}, probabilité {round(s['prob'] * 100)} %).")})
    for b in f["buys"]:
        key = f"achat:{b['asset']}"
        if not b["blocked"] and b["prob"] >= threshold and key not in sent:
            out.append({"key": key, "text": (
                f"📈 Achat probable ce soir : {b['asset'].upper()} si la clôture dépasse "
                f"{_px(b['trigger'])} (cours {_px(b['price'])}, probabilité {round(b['prob'] * 100)} %).")})
    return out
