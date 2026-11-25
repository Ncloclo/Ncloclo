"""
Le capital dans votre monnaie, quelle qu'elle soit (une soixantaine de
monnaies), avec les frais de change et l'impôt sur les gains, pour savoir ce
qu'un retrait complet vous rapporterait vraiment.

Le bot compte en USDT (compté comme un dollar). Pour vous, ce qui compte est
ce qui arrive sur votre compte dans votre monnaie (TG_DEVISE ; par défaut
celle de votre pays, TG_PAYS) :

  - l'euro : taux EUR/USDT relu chaque heure chez Binance ;
  - le franc CFA (BCEAO et BEAC, XOF et XAF) : exactement 1/655,957 euro
    (parité fixe) ;
  - les autres monnaies : leur cours face au dollar, lu chaque jour chez
    Yahoo Finance par le suivi des marchés (multi_actifs) ;
  - passer de votre monnaie aux USDT puis revenir (P2P, mobile money, banque)
    coûte des frais de change (TG_FRAIS_CHANGE, 2 % par conversion par
    défaut : une estimation, à remplacer par vos vrais taux) ;
  - l'impôt sur les plus-values (TG_IMPOT_PLUS_VALUES, 0 % par défaut) se
    règle selon votre pays ; il ne s'applique qu'au gain.

Le capital de départ est converti au taux du jour où le bot l'a mesuré pour
la première fois : une variation de votre monnaie face au dollar change vos
gains, comme dans la réalité. Information seulement : aucune décision du bot
n'en dépend.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import v29

from .texte import fr

XOF_PER_EUR = 655.957           # parité fixe du franc CFA (BCEAO et BEAC) depuis 1999
FCFA = ("FCFA", "XOF", "XAF")
# Cours lus chaque jour chez Yahoo (multi_actifs) : monnaie → (code du
# marché, True si le cours est en dollars pour une unité de la monnaie).
YAHOO: Dict[str, Tuple[str, bool]] = {
    "EUR": ("eurusd", True), "GBP": ("gbpusd", True), "AUD": ("audusd", True), "NZD": ("nzdusd", True),
    "JPY": ("usdjpy", False), "CHF": ("usdchf", False), "CAD": ("usdcad", False)}
MULTI_CACHE = os.path.join(v29.APP_DIR, "data_multi")
RATE_REFRESH_SEC = 3600


def _yahoo() -> Dict[str, Tuple[str, bool]]:
    """Les devises des pays, chacune face au dollar (suivi quotidien)."""
    from . import multi_actifs as ma
    return {**{c: (f"fx_{c.lower()}", False) for c in ma.CURRENCIES if c not in FCFA}, **YAHOO}


def codes() -> Tuple[str, ...]:
    """Les monnaies d'affichage possibles."""
    return ("USDT", "USD", *FCFA, *sorted(_yahoo()))


def label(code: str) -> str:
    """Nom affiché : « FCFA » pour le franc CFA, le code ISO sinon."""
    return "FCFA" if code in FCFA else code


def needs_eur(code: str) -> bool:
    return code == "EUR" or code in FCFA


def needs_fx(code: str) -> bool:
    return code not in ("USDT", "USD") and not needs_eur(code)


def eur_usdt(exchange: Any) -> Optional[float]:
    """USDT pour 1 euro (paire EUR/USDT de Binance), ou None."""
    try:
        px = float(exchange.fetch_ticker("EUR/USDT")["last"])
    except Exception:
        return None
    return px if px > 0 else None


def yahoo_rate(code: str, cache: str = MULTI_CACHE) -> Optional[float]:
    """Dernier cours Yahoo de la paire de cette monnaie face au dollar (fichier
    du suivi quotidien des marchés), ou None."""
    pair = _yahoo().get(code)
    if not pair:
        return None
    from . import multi_actifs as ma
    close = ma.load(cache, [pair[0]])
    s = close[pair[0]].dropna() if pair[0] in close.columns else close
    return float(s.iloc[-1]) if len(s) and float(s.iloc[-1]) > 0 else None


def per_usdt(code: str, eurusdt: Optional[float], fx: Optional[float] = None) -> Optional[float]:
    """Unités de la monnaie `code` pour 1 USDT. `eurusdt` : USDT pour 1 euro
    (Binance) ; `fx` : dernier cours Yahoo de la paire de cette monnaie."""
    if code in ("USDT", "USD"):
        return 1.0
    if needs_eur(code):
        if not eurusdt or eurusdt <= 0:
            return None
        return 1.0 / eurusdt if code == "EUR" else XOF_PER_EUR / eurusdt
    pair = _yahoo().get(code)
    if not pair or not fx or fx <= 0:
        return None
    return 1.0 / fx if pair[1] else fx


def decimals(code: str, rate: Optional[float]) -> int:
    """Décimales d'un montant : aucune pour le franc CFA et les monnaies où un
    dollar vaut 50 unités ou plus (yen, naira, shilling…)."""
    return 0 if code in FCFA or (rate or 0) >= 50 else 2


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
            "change_cost": change_cost, "tax_rate": tax_rate, "label": label(code),
            "decimals": decimals(code, rate_now)}


def amount(v: float, code: str, digits: Optional[int] = None) -> str:
    """Montant lisible : sans décimale pour le franc CFA et les monnaies à
    grosses unités, au centime sinon."""
    digits = digits if digits is not None else (0 if code in FCFA else 2)
    return f"{fr(v, f',.{digits}f')} {label(code)}"


def line(v: Optional[Dict[str, Any]]) -> Optional[str]:
    """Une ligne pour le résumé du jour, ou None."""
    if not v or v.get("code") in (None, "USDT"):
        return None
    c, digits = v["code"], v.get("decimals")
    return (f"  💰 ≈ {amount(v['equity'], c, digits)} (1 USDT = {fr(v['rate'], '.2f')} {label(c)}) ; retrait "
            f"complet : {amount(v['withdraw_net'], c, digits)} nets "
            f"({fr(v['gain_net_pct'], '+.1f')} % depuis le départ, frais de change "
            f"{fr(v['change_cost'] * 100, '.1f')} %, impôt {fr(v['tax_rate'] * 100, '.0f')} %)")
