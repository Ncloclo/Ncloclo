#!/usr/bin/env python3
"""
Le bot gagnerait-il à utiliser un levier ? Étude reproductible
(docs/LEVIER.md), avec les politiques et la règle du bot (trendguard/levier.py),
sur l'historique Binance des 21 cryptos du bot.

Protocole fixé avant les mesures : celui de trendguard/levier.py (politiques,
coût d'emprunt de 10 % par an, règle jugée sur 2018-2022 et depuis 2023) ; la
période jamais vue (24/05/2026 →) est montrée à part et ne juge pas. Test de
résistance déclaré d'avance : coût d'emprunt doublé (20 % par an), car le
financement des contrats perpétuels monte bien plus haut dans l'euphorie.

  python -m research.levier --cache data_binance
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from research.commun import load_binance, study_args, write_report
from trendguard import levier
from trendguard.texte import fr

UNSEEN_START = "2026-05-24"
STRESS_RATE = 0.20


def _cell(p: Dict[str, float]) -> str:
    return (f"{fr(p['cagr_pct'], '+.1f')} % | {fr(p['max_dd_pct'], '.1f')} % | {fr(p['calmar'], '.2f')} | "
            f"{fr(p['borrow_pct'], '.0f')} %")


def _fixed(book: Dict[str, Any], lev: float) -> Dict[str, Any]:
    return next(r for r in book["rows"] if r["lev"] == lev and not r["adaptive"])


def reading(book: Dict[str, Any]) -> List[str]:
    """Ce que disent les chiffres, calculé (rien d'écrit à la main)."""
    base, small = book["rows"][0], _fixed(book, levier.FIXED[0])
    adapt = next(r for r in book["rows"] if r["adaptive"])
    floor = -(levier.KILL - levier.ROOM) * 100
    fixed = [r for r in book["rows"] if not r["adaptive"]]
    out = ["- Le levier fixe multiplie le rendement et la pire baisse ensemble : le Calmar va de "
           + " et de ".join(f"{fr(min(r['periods'][k]['calmar'] for r in fixed), '.2f')} à "
                            f"{fr(max(r['periods'][k]['calmar'] for r in fixed), '.2f')} {name}"
                            for k, name in enumerate(("sur 2018-2022", "depuis 2023"))) + " (sans levier : "
           + " et ".join(fr(p["calmar"], ".2f") for p in base["periods"][:2]) + ")."]
    worst = min(p["max_dd_pct"] for p in base["periods"][:2])
    if worst < floor:
        out.append(f"- Sans levier, la pire baisse atteint déjà {fr(worst, '.1f')} % : au-delà de "
                   f"{fr(floor, '.0f')} %, il ne reste pas la marge exigée avant l'arrêt d'urgence (−40 %).")
    small_worst = min(p["max_dd_pct"] for p in small["periods"][:2])
    if small_worst <= -levier.KILL * 100:
        out.append(f"- À {fr(small['lev'], 'g')}× seulement, la pire baisse aurait été de "
                   f"{fr(small_worst, '.1f')} % : l'arrêt d'urgence du bot aurait été touché.")
    worse = [k for k in (0, 1) if adapt["periods"][k]["calmar"] < base["periods"][k]["calmar"]]
    if worse:
        out.append("- La politique adaptative fait moins bien que sans levier au Calmar "
                   + " et ".join(("sur 2018-2022", "depuis 2023")[k] for k in worse)
                   + ". Explication probable, non vérifiée : elle emprunte près des sommets, là où "
                   "arrivent les retournements que subit la règle de tendance.")
    if len(base["periods"]) > 2:
        out.append(f"- Sur la période jamais vue, {fr(small['lev'], 'g')}× aurait rapporté "
                   f"{fr(small['periods'][2]['cagr_pct'], '+.1f')} % par an contre "
                   f"{fr(base['periods'][2]['cagr_pct'], '+.1f')} % sans levier : six mois, trop peu pour "
                   "juger, et la règle ne la regarde pas.")
    return out


def render(book: Dict[str, Any], stressed: Dict[str, Any], end: str) -> str:
    """Rapport Markdown."""
    names = ["2018-2022", f"2023 → {end}", f"24/05/2026 → {end}"][:len(book["periods"])]
    floor = -(levier.KILL - levier.ROOM) * 100
    lines = [
        "# Le bot gagnerait-il à utiliser un levier ?", "",
        f"Historique Binance des 21 cryptos du bot jusqu'au {end} ; règle, paramètres et régime BTC inchangés. "
        "Régénérer : `python -m research.levier --cache data_binance`. Le bot refait la même étude chaque "
        "semaine (`python trendguard_bot.py levier etude`), sur son univers et l'historique le plus récent ; "
        "son verdict s'affiche dans la page Veille du panneau.", "",
        "## Ce que fait le levier", "",
        "Le levier multiplie la taille de chaque position : à 2×, une perte au stop coûte 2 % du capital au "
        "lieu de 1 %, et le bot emprunte dès que ses positions dépassent son capital. Gains et pertes sont "
        "multipliés ensemble, et l'emprunt coûte des intérêts (marge) ou un financement (contrats perpétuels).",
        "",
        "Mesure : la stratégie rejouée sans levier, puis ses rendements journaliers multipliés par le levier "
        f"de la veille, moins le coût de l'emprunt ({fr(book['rate'] * 100, 'g')} % par an sur la part "
        "empruntée). Exposition moyenne sans levier : " + " ; ".join(
            f"{n} : {fr(e, '.0f')} %" for n, e in zip(names, book["exposure_pct"])) + ".", "",
        "## Politiques et règle, fixées avant les mesures", "",
        "- Levier fixe : " + ", ".join(f"{fr(lev, 'g')}×" for lev in levier.FIXED) + ".",
        f"- Adaptatif : {fr(levier.ADAPTIVE, 'g')}× seulement en marché haussier (BTC au-dessus de sa moyenne "
        f"de 150 jours) et tant que la stratégie sans levier est à moins de "
        f"{fr(levier.ADAPTIVE_DD * 100, '.0f')} % de son sommet, 1× sinon ; décidé à la clôture de la veille.",
        "- Une politique n'est permise que si, sur 2018-2022 ET depuis 2023, son Calmar n'est pas moins bon "
        f"que sans levier et sa pire baisse reste au-dessus de {fr(floor, '.0f')} % (10 points avant "
        "l'arrêt d'urgence du bot, à −40 %). Parmi les politiques permises : celle dont le plus faible des "
        "deux Calmar est le meilleur. La période jamais vue informe et ne juge pas.", "",
        "## Résultats", "",
        "| Politique | " + " | ".join(f"{n} : CAGR | pire baisse | Calmar | jours d'emprunt" for n in names)
        + " | Verdict de la règle |",
        "|---|" + "---|---|---|---|" * len(names) + "---|"]
    for i, r in enumerate(book["rows"]):
        state = "référence" if i == 0 else f"refusé : {r['why']}" if r["why"] else "**permis**"
        lines.append(f"| {r['name']} | " + " | ".join(_cell(p) for p in r["periods"]) + f" | {state} |")
    lines += ["", "## Verdict (règle fixée avant les mesures)", "",
              f"- **{book['text'][0].upper() + book['text'][1:]}**.",
              f"- Coût d'emprunt doublé ({fr(STRESS_RATE * 100, 'g')} % par an) : {stressed['text']}.",
              "- Rien n'est appliqué : le bot trade au comptant, sans emprunt. Un levier réel exigerait votre "
              "accord exprès, le testnet d'abord et un moteur d'emprunt (marge ou contrats à terme) qui "
              "n'existe pas encore ; les verrous du réel ne changent pas.",
              "", "## Lecture", ""] + reading(book)
    lines += ["", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Approximation : des positions multipliées par le levier et rééquilibrées chaque jour. Les "
              "chutes dans la journée ne se voient pas à la clôture : une position à levier peut être "
              "liquidée avant que le stop ne joue.",
              "- Le coût de l'emprunt varie : le financement des contrats perpétuels dépasse souvent 30 % par "
              "an dans l'euphorie ; 10 % est une moyenne supposée, 20 % le test de résistance.",
              "- Le levier multiplie aussi les plafonds de risque gelés du bot (1 % de risque par trade, 6 % au "
              "total, 25 % du capital par position) : c'est un changement de la règle, pas un réglage."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Mesure sur l'historique gardé, écrit le rapport."""
    args = study_args("Levier", "docs/LEVIER.md", argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    periods = [levier.DESIGN, (levier.VERIFY_START, end), (UNSEEN_START, end)]
    book = levier.evaluate(close, volume, periods)
    stressed = levier.evaluate(close, volume, periods, rate=STRESS_RATE)
    write_report(render(book, stressed, end), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
