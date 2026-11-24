#!/usr/bin/env python3
"""
Plus de cryptos que les 21 du bot (Solana, NEAR, Pepe, Hyperliquid…) ?
Étude reproductible (docs/UNIVERS_CRYPTO.md).

Candidats : les paires USDT de Binance échangées pour plus de 5 M$ sur
24 heures le jour où la liste est faite, hors stablecoins, monnaies,
jetons d'actions ou de matières premières, jetons « enveloppés » ou à levier,
et hors les 21 du bot. La liste est gardée (<cache>/candidats.json) : l'étude
se rejoue à l'identique.

Protocole fixé avant les mesures : même règle, mêmes paramètres gelés, même
régime (BTC au-dessus de sa moyenne de 150 jours), mêmes filtres (250 jours
d'historique, 5 M$ échangés par jour en moyenne sur 30 jours) ; univers des
21 contre 21 + candidats, sur 2018-2022, depuis 2023 et 24/05/2026 → ; ajout
proposé (en paper d'abord) seulement si l'univers élargi bat les 21 au
Calmar sur les deux périodes. Biais du survivant : les candidats sont, par
construction, des cryptos liquides aujourd'hui.

  python -m research.univers_crypto --cache data_univers                 # liste gardée
  python -m research.univers_crypto --cache data_univers --candidats     # nouvelle liste
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import evolution, savoir
from trendguard import trend_strategy as ts
from trendguard.config import LIVE_UNIVERSE_DEFAULT
from trendguard.texte import fr

TICKERS = "https://data-api.binance.vision/api/v3/ticker/24hr"
ASSETS_INFO = "https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset"
MIN_24H_USD = 5e6
NOT_CRYPTO_TAGS = ("bStocks", "tCommodities")
EXCLUDED = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "EURI", "AEUR", "XUSD", "USD1", "USDE",
            "BFUSD", "RLUSD", "PYUSD", "USDS", "FRAX", "PAXG", "XAUT", "WBTC", "WBETH", "BNSOL", "WETH",
            "STETH", "TRY", "BRL", "GBP", "JPY", "ARS", "MXN", "ZAR", "PLN", "RON", "UAH", "NGN", "COP", "CZK"}
DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"


def candidates(fetch: Any) -> List[Dict[str, Any]]:
    """Cryptos liquides de Binance hors des 21 du bot, avec leur nom et
    leur volume sur 24 heures, de la plus échangée à la moins échangée."""
    have = {b.upper() for b in LIVE_UNIVERSE_DEFAULT}
    info = {a.get("assetCode"): a for a in (json.loads(fetch(ASSETS_INFO)).get("data") or [])}
    out = []
    for r in json.loads(fetch(TICKERS)):
        s = r.get("symbol", "")
        base = s[:-4]
        if not s.endswith("USDT") or base in have or base in EXCLUDED:
            continue
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        tags = [str(t) for t in (info.get(base) or {}).get("tags") or []]
        if any(t in NOT_CRYPTO_TAGS or "stable" in t.lower() for t in tags):
            continue
        vol = float(r.get("quoteVolume") or 0)
        if vol >= MIN_24H_USD:
            out.append({"base": base.lower(), "name": (info.get(base) or {}).get("assetName") or base,
                        "volume_24h": vol})
    return sorted(out, key=lambda c: -c["volume_24h"])


def run(close: pd.DataFrame, volume: pd.DataFrame, universe: List[str], periods: List[Tuple[str, str]]
        ) -> List[ts.PortfolioResult]:
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p, universe)
    return [ts.backtest(close, volume, p, a, b, universe=universe, pre=pre) for a, b in periods]


def render(base: List[ts.PortfolioResult], wide: List[ts.PortfolioResult], cands: List[Dict[str, Any]],
           first: Dict[str, str], listed_on: str, end: str) -> str:
    """Rapport Markdown."""
    names = ["2018-2022", f"2023 → {end}", f"24/05/2026 → {end}"]
    lines = [
        "# Plus de cryptos que les 21 du bot ?", "",
        f"Historique Binance jusqu'au {end} ; règle, paramètres, régime BTC et filtres du bot inchangés. "
        "Régénérer : `python -m research.univers_crypto --cache data_univers`.", "",
        f"Candidats (liste du {listed_on}, paires USDT à plus de 5 M$ sur 24 heures, hors stablecoins, "
        f"monnaies, jetons d'actions ou de matières premières, jetons enveloppés ou à levier) : {len(cands)}.", "",
        "| Crypto | Volume sur 24 h | Premier jour de l'historique |", "|---|---|---|"]
    for c in cands:
        lines.append(f"| {c['name']} ({c['base'].upper()}) | {fr(c['volume_24h'] / 1e6, ',.0f')} M$ | "
                     f"{first.get(c['base'], 'illisible')} |")
    lines += ["", "| Univers | " + " | ".join(f"{n} : rendement | baisse | Calmar | trades" for n in names) + " |",
              "|---|" + "---|---|---|---|" * len(names)]
    for label, res in (("Les 21 du bot", base), (f"Les 21 + {len(cands)} candidats", wide)):
        lines.append(f"| {label} | " + " | ".join(
            f"{fr(r.metrics.get('total_return_pct', 0.0), '+.1f')} % | {fr(r.metrics.get('max_dd_pct', 0.0), '.1f')} % | "
            f"{fr(r.metrics.get('calmar', 0.0), '.2f')} | {r.metrics.get('trades', 0)}" for r in res) + " |")
    cand = {c["base"] for c in cands}
    lines += ["", "Trades des candidats dans l'univers élargi : " + " ; ".join(
        f"{n} : {sum(1 for t in r.trades if t['asset'] in cand)} trade(s), "
        f"{fr(sum(t['r'] for t in r.trades if t['asset'] in cand), '+.1f')} R" for n, r in zip(names, wide)) + "."]
    better = all(wide[k].metrics.get("calmar", 0) > base[k].metrics.get("calmar", 0) for k in (0, 1))
    lines += ["", "## Verdict (règle fixée avant les mesures)", "",
              f"- L'univers élargi bat les 21 au Calmar sur 2018-2022 et depuis 2023 : **{'oui' if better else 'non'}**.",
              ("- **Proposition** : ajouter ces cryptos au bot en paper d'abord, puis réexaminer : le biais du "
               "survivant flatte l'univers élargi." if better else
               "- **Pas de proposition** : les 21 cryptos du bot restent son univers."),
              "", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Biais du survivant : les candidats sont liquides aujourd'hui ; les cryptos mortes entre-temps "
              "n'y sont pas.",
              "- Beaucoup de candidats n'existaient pas avant 2021 : sur 2018-2022, l'univers élargi ressemble "
              "aux 21 ; la comparaison porte surtout sur la période depuis 2023."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Liste les candidats (ou relit la liste gardée), télécharge leur
    historique, mesure, écrit le rapport."""
    def extra(ap: Any) -> None:
        ap.add_argument("--binance", default="data_binance", help="historique des 21 cryptos du bot")
        ap.add_argument("--candidats", action="store_true", help="refaire la liste des candidats")
    args = study_args("Plus de cryptos", "docs/UNIVERS_CRYPTO.md", argv, extra)
    path = os.path.join(args.cache, "candidats.json")
    if args.candidats or not os.path.exists(path):
        os.makedirs(args.cache, exist_ok=True)
        listed = {"day": pd.Timestamp.now(tz="UTC").date().isoformat(),
                  "candidates": candidates(lambda url: savoir.http_get(url, timeout=60))}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(listed, fh, ensure_ascii=False, indent=1)
    with open(path, encoding="utf-8") as fh:
        listed = json.load(fh)
    cands = listed["candidates"]
    bases = [c["base"] for c in cands]
    close_c, volume_c = load_binance(args.binance)
    # BTC est requis par le chargeur (régime) ; les 21 ont déjà le leur.
    close_n, volume_n = evolution.load_history(args.cache, ["btc"] + bases, max_age_days=None)
    close_n, volume_n = close_n.drop(columns=["btc"]), volume_n.drop(columns=["btc"], errors="ignore")
    first = {a: str(close_n[a].first_valid_index().date()) for a in close_n.columns
             if close_n[a].first_valid_index() is not None}
    end = str(min(close_c.index[-1], close_n.index[-1]).date())
    close = pd.concat([close_c, close_n], axis=1).loc[:end]
    volume = pd.concat([volume_c, volume_n], axis=1).loc[:end]
    periods = [DESIGN, (VERIFY_START, end), (UNSEEN_START, end)]
    old = [a.lower() for a in LIVE_UNIVERSE_DEFAULT]
    base = run(close, volume, old, periods)
    wide = run(close, volume, old + [b for b in bases if b in close.columns], periods)
    write_report(render(base, wide, cands, first, listed["day"], end), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
