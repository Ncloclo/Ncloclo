"""
Le capital dans votre monnaie : FCFA (ou euros), frais de change et impôt sur
les gains, pour savoir ce qu'un retrait complet vous rapporterait vraiment.

Le bot compte en USDT. Pour vous, ce qui compte est ce qui arrive sur votre
compte en FCFA :

  - le FCFA (BCEAO et BEAC) vaut exactement 1/655,957 euro (parité fixe) ; le
    taux EUR/USDT est relu chaque heure chez Binance (paire EUR/USDT) ;
  - passer des FCFA aux USDT puis revenir (P2P, mobile money, banque) coûte
    des frais de change (TG_FRAIS_CHANGE, 2 % par conversion par défaut : une
    estimation, à remplacer par vos vrais taux) ;
  - l'impôt sur les plus-values (TG_IMPOT_PLUS_VALUES, 0 % par défaut) se
    règle selon votre pays ; il ne s'applique qu'au gain.

Le capital de départ est converti au taux du jour où le bot l'a mesuré pour
la première fois : une variation de l'euro face au dollar change vos gains en
FCFA, comme dans la réalité. Information seulement : aucune décision du bot
n'en dépend.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .texte import fr

XOF_PER_EUR = 655.957           # parité fixe du FCFA (BCEAO et BEAC) depuis 1999
CODES = ("USDT", "EUR", "FCFA")
RATE_REFRESH_SEC = 3600


def eur_usdt(exchange: Any) -> Optional[float]:
    """USDT pour 1 euro (paire EUR/USDT de Binance), ou None."""
    try:
        px = float(exchange.fetch_ticker("EUR/USDT")["last"])
    except Exception:
        return None
    return px if px > 0 else None


def per_usdt(code: str, eurusdt: Optional[float]) -> Optional[float]:
    """Unités de la monnaie `code` pour 1 USDT."""
    if code == "USDT":
        return 1.0
    if not eurusdt or eurusdt <= 0:
        return None
    if code == "EUR":
        return 1.0 / eurusdt
    if code == "FCFA":
        return XOF_PER_EUR / eurusdt
    return None


def view(code: str, equity_usdt: float, start_usdt: float, rate_now: float,
         rate_start: Optional[float], change_cost: float, tax_rate: float) -> Dict[str, Any]:
    """Capital et retrait complet dans votre monnaie. `rate_*` : unités de
    votre monnaie pour 1 USDT, aujourd'hui et le jour du départ."""
    rate_start = rate_start or rate_now
    equity = equity_usdt * rate_now
    invested = start_usdt * rate_start * (1 + change_cost)   # ce que le départ vous a coûté
    gross = equity * (1 - change_cost)                       # retrait complet, frais de change payés
    gain = gross - invested
    tax = max(0.0, gain) * tax_rate
    net = gross - tax
    return {"code": code, "rate": rate_now, "rate_start": rate_start, "equity": equity,
            "invested": invested, "withdraw_gross": gross, "tax": tax, "withdraw_net": net,
            "gain_net": net - invested,
            "gain_net_pct": (net / invested - 1) * 100 if invested > 0 else 0.0,
            "change_cost": change_cost, "tax_rate": tax_rate}


def amount(v: float, code: str) -> str:
    """Montant lisible : FCFA sans décimale, euros et USDT au centime."""
    return f"{fr(v, ',.0f')} {code}" if code == "FCFA" else f"{fr(v, ',.2f')} {code}"


def line(v: Optional[Dict[str, Any]]) -> Optional[str]:
    """Une ligne pour le résumé du jour, ou None."""
    if not v or v.get("code") in (None, "USDT"):
        return None
    c = v["code"]
    return (f"  💰 ≈ {amount(v['equity'], c)} (1 USDT = {fr(v['rate'], '.2f')} {c}) ; retrait "
            f"complet : {amount(v['withdraw_net'], c)} nets "
            f"({fr(v['gain_net_pct'], '+.1f')} % depuis le départ, frais de change "
            f"{fr(v['change_cost'] * 100, '.1f')} %, impôt {fr(v['tax_rate'] * 100, '.0f')} %)")
