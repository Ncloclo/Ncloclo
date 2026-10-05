# Données de la revue hebdomadaire TrendGuard

Produites chaque lundi à 00:40 UTC par `.github/workflows/donnees.yml`
(date exacte dans `DATE`) et lues par l'agent Claude Code de 02:00.

- `veille.txt` : annonces officielles Binance, actualités, Fear & Greed.
- `diagnostic.txt` : `trendguard_bot.py diagnose` (données publiques).
- `STRATEGIES.md` : laboratoire des stratégies (`trendguard_bot.py lab`).
- `SELECTION.md` : auto-sélection des 10 plus rentables et prises de
  bénéfice fixes contre la référence (`python -m research.selection`), à
  comparer à `docs/SELECTION.md` : le réglage par défaut (21
  cryptos, gain laissé courir) ne change que si une variante gagne
  sur les deux périodes.
- `data_binance/` : historique Binance, à passer en `--cache` pour
  tester des variantes sans réseau.
- `*.log` : sorties complètes, en cas d'échec.
