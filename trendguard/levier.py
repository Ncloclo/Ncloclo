"""
Levier (docs/LEVIER.md) : le bot gagnerait-il à s'exposer plus que sa règle
ne le fait, en empruntant au besoin ? Il ne le décide qu'après étude, et
refait l'étude chaque semaine sur l'historique le plus récent.

Le levier multiplie les gains ET les pertes : à 2×, chaque position est deux
fois plus grosse, une perte au stop coûte 2 % du capital au lieu de 1 %, et
le bot emprunte dès que ses positions dépassent son capital. Mesure : la
stratégie rejouée sans levier, puis ses rendements journaliers multipliés par
le levier de la veille, moins le coût de l'emprunt (BORROW_RATE par an sur la
part empruntée : exposition × levier − 1, si elle est positive).

Politiques étudiées, fixées avant les mesures :
  - levier fixe : 1,25×, 1,5×, 2×, 3× ;
  - adaptative : 1,5× seulement en marché haussier (BTC au-dessus de sa
    moyenne de 150 jours) et tant que la stratégie sans levier est à moins de
    10 % de son sommet, 1× sinon ; décidé à la clôture de la veille, sans
    regard vers le futur.

Règle fixée avant les mesures : une politique n'est permise que si, sur
2018-2022 ET depuis 2023,
  1. son Calmar n'est pas moins bon que sans levier, et
  2. sa pire baisse reste à au moins 10 points de l'arrêt d'urgence du bot
     (−40 %), c'est-à-dire au-dessus de −30 %.
Parmi les politiques permises, le bot retient celle dont le plus faible des
deux Calmar est le meilleur. Aucune permise : pas de levier (1×).

Le levier permis n'est appliqué nulle part tant que le bot n'a pas de moteur
d'emprunt : il trade au comptant. Un levier réel exigerait votre accord exprès,
le testnet d'abord et un moteur d'emprunt (marge ou contrats à terme) qui
n'existe pas encore. Les verrous du réel ne changent pas.

    python trendguard_bot.py levier            # dernier verdict
    python trendguard_bot.py levier etude      # nouvelle étude
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from . import trend_strategy as ts
from .texte import fr

FIXED = (1.25, 1.5, 2.0, 3.0)   # leviers fixes étudiés (sans levier : la référence)
ADAPTIVE = 1.5                  # levier de la politique adaptative…
ADAPTIVE_DD = 0.10              # … tant que la stratégie est à moins de 10 % de son sommet
BORROW_RATE = 0.10              # coût d'emprunt par an (marge, financement des contrats perpétuels)
KILL = 0.40                     # arrêt d'urgence du bot
ROOM = 0.10                     # marge exigée avant l'arrêt d'urgence
RULE_PERIODS = 2                # la règle juge les deux premières périodes (les suivantes : information)
DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
KEY = "levier"                  # méta du noyau de savoir
EVERY_DAYS = 7
BASE = "sans levier"
ADAPTIVE_NAME = (f"adaptatif : {fr(ADAPTIVE, 'g')}× en marché haussier, à moins de "
                 f"{fr(ADAPTIVE_DD * 100, '.0f')} % du sommet")

Lever = Union[float, pd.Series]


def policies() -> List[Tuple[str, float, bool]]:
    """(nom, levier maximal, adaptative ?), dans l'ordre déclaré ; la
    première est la référence sans levier."""
    return ([(BASE, 1.0, False)] + [(f"{fr(lev, 'g')}× fixe", lev, False) for lev in FIXED]
            + [(ADAPTIVE_NAME, ADAPTIVE, True)])


def _aligned(equity: pd.Series, exposure: pd.Series, lev: Lever) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Rendement de chaque jour, et exposition et levier de la veille (un
    levier en série est celui décidé à chaque clôture, appliqué au lendemain)."""
    r = equity.pct_change().fillna(0.0)
    expo = exposure.reindex(r.index).shift(1).fillna(0.0)
    k = (lev.reindex(r.index).shift(1).fillna(1.0) if isinstance(lev, pd.Series)
         else pd.Series(float(lev), index=r.index))
    return r, expo, k


def levered(equity: pd.Series, exposure: pd.Series, lev: Lever, rate: float = BORROW_RATE) -> pd.Series:
    """Rendements journaliers de la stratégie avec un levier : ceux sans levier
    multipliés par le levier de la veille, moins le coût de la part empruntée
    (exposition de la veille × levier − 1, si elle est positive)."""
    r, expo, k = _aligned(equity, exposure, lev)
    return k * r - (expo * k - 1.0).clip(lower=0.0) * rate / 365.0


def borrowing_days(equity: pd.Series, exposure: pd.Series, lev: Lever) -> float:
    """Part des jours (en %) où le bot emprunterait : exposition × levier > 1."""
    _r, expo, k = _aligned(equity, exposure, lev)
    return float((expo * k > 1.0).mean() * 100) if len(expo) else 0.0


def adaptive(equity: pd.Series, bull: pd.Series) -> pd.Series:
    """Levier décidé à chaque clôture par la politique adaptative : ADAPTIVE
    en marché haussier tant que la stratégie sans levier est à moins de
    ADAPTIVE_DD de son sommet, 1 sinon."""
    near_top = (equity / equity.cummax() - 1) > -ADAPTIVE_DD
    up = bull.reindex(equity.index, fill_value=False).astype(bool)
    return pd.Series(np.where(up & near_top, ADAPTIVE, 1.0), index=equity.index)


def stats(ret: pd.Series) -> Dict[str, float]:
    """CAGR, pire baisse, Calmar d'une série de rendements journaliers ; une
    perte de tout le capital est la ruine."""
    eq = (1 + ret).cumprod()
    years = max((ret.index[-1] - ret.index[0]).days / 365.25, 1e-9)
    if (eq <= 0).any():
        return {"cagr_pct": -100.0, "max_dd_pct": -100.0, "calmar": -1.0}
    dd = float((eq / eq.cummax() - 1).min())
    cagr = float(eq.iloc[-1] ** (1 / years) - 1)
    return {"cagr_pct": cagr * 100, "max_dd_pct": dd * 100, "calmar": cagr / abs(dd) if dd < 0 else 0.0}


def refusal(row: Dict[str, Any], base: Dict[str, Any]) -> str:
    """Pourquoi la règle fixée d'avance refuse cette politique ('' : permise)."""
    floor = -(KILL - ROOM) * 100
    per, ref = row["periods"][:RULE_PERIODS], base["periods"][:RULE_PERIODS]
    why = []
    if any(p["calmar"] < b["calmar"] for p, b in zip(per, ref)):
        why.append("Calmar moins bon que sans levier")
    worst = min(p["max_dd_pct"] for p in per)
    if worst < floor:
        why.append(f"pire baisse {fr(worst, '.0f')} %, sous {fr(floor, '.0f')} %")
    return " ; ".join(why)


def decide(rows: List[Dict[str, Any]]) -> Tuple[float, str, str]:
    """(levier maximal, politique retenue, phrase) selon la règle : parmi les
    politiques permises, celle dont le plus faible Calmar est le meilleur ; à
    égalité, le moindre levier."""
    allowed = [r for r in rows[1:] if not r["why"]]
    if not allowed:
        return 1.0, BASE, "aucun levier (1×) : aucune politique ne passe la règle"
    best = max(allowed, key=lambda r: (min(p["calmar"] for p in r["periods"][:RULE_PERIODS]), -r["lev"]))
    return best["lev"], best["name"], f"levier permis : {best['name']}"


def evaluate(close: pd.DataFrame, volume: pd.DataFrame, periods: Optional[List[Tuple[str, str]]] = None,
             rate: float = BORROW_RATE) -> Dict[str, Any]:
    """La stratégie rejouée une fois sans levier sur chaque période, puis
    chaque politique de levier ; verdict de la règle sur les deux premières
    périodes."""
    end = str(close.index[-1].date())
    periods = periods or [DESIGN, (VERIFY_START, end)]
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    runs = [ts.backtest(close, volume, p, a, b, pre=pre) for a, b in periods]
    bull = ts.btc_regime(close["btc"], p)
    rows: List[Dict[str, Any]] = []
    for name, lev, adapt in policies():
        per = []
        for run in runs:
            k: Lever = adaptive(run.equity, bull) if adapt else lev
            per.append(dict(stats(levered(run.equity, run.exposure, k, rate)),
                            borrow_pct=borrowing_days(run.equity, run.exposure, k)))
        rows.append({"name": name, "lev": lev, "adaptive": adapt, "periods": per})
    for row in rows:
        row["why"] = "" if row is rows[0] else refusal(row, rows[0])
    allowed, policy, text = decide(rows)
    return {"rows": rows, "allowed": allowed, "policy": policy, "text": text,
            "periods": [list(x) for x in periods], "rate": rate,
            "exposure_pct": [float(run.exposure.mean() * 100) for run in runs]}


def due(book: Any, today: str) -> bool:
    day = (book or {}).get("day") if isinstance(book, dict) else None
    if not day:
        return True
    try:
        return (datetime.fromisoformat(today) - datetime.fromisoformat(day)).days >= EVERY_DAYS
    except ValueError:
        return True


def render(book: Any) -> str:
    """Le dernier verdict et chaque politique, une par ligne."""
    if not isinstance(book, dict) or not book.get("rows"):
        return "Levier : pas encore étudié (aucun levier)."
    lines = [f"Levier, étude du {book.get('day')} (coût d'emprunt {fr(book['rate'] * 100, 'g')} % par an) : "
             f"{book['text']}."]
    for i, r in enumerate(book["rows"]):
        state = "référence" if i == 0 else f"refusé ({r['why']})" if r["why"] else "permis"
        lines.append(f"- {r['name']}, {state} : " + " ; ".join(
            f"{a} → {b} CAGR {fr(p['cagr_pct'], '+.1f')} %, pire baisse {fr(p['max_dd_pct'], '.1f')} %, "
            f"Calmar {fr(p['calmar'], '.2f')}, emprunt {fr(p.get('borrow_pct', 0.0), '.0f')} % des jours"
            for (a, b), p in zip(book["periods"], r["periods"])))
    lines.append("Appliqué nulle part : le bot trade au comptant, sans emprunt ; un levier réel exigerait votre "
                 "accord exprès, le testnet d'abord et un moteur d'emprunt qui n'existe pas encore.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """`levier` : dernier verdict ; `levier etude` : nouvelle étude, gardée
    dans le noyau de savoir."""
    import v29

    from . import savoir
    from .config import load_guard_config_from_env
    from .evolution import load_history
    ap = argparse.ArgumentParser(description="Levier de TrendGuard : étude et verdict")
    ap.add_argument("action", nargs="?", default="statut", choices=["statut", "etude"])
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    memory = savoir.Memory(gcfg.savoir_db or savoir.DEFAULT_DB)
    try:
        if args.action == "etude":
            close, volume = load_history(args.cache, list(gcfg.universe))
            book = dict(evaluate(close, volume), day=datetime.now(timezone.utc).date().isoformat())
            memory.put(KEY, book)
        print(render(memory.get(KEY)))
        return 0
    finally:
        memory.close()
