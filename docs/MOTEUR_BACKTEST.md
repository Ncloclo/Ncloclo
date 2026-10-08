# Moteur de backtest (étape 10 du prompt maître)

Le backtest de la règle existait déjà : c'est la boucle du bot, avec les
mêmes fonctions de décision qu'en marche (`trend_strategy.backtest`). Le
moteur de backtest l'entoure de ce qui fait une **preuve** : un manifeste
pour la refaire à l'identique, des données contrôlées avant de simuler, des
épreuves (coûts, glissement, achats en retard, capacité, régimes, achats au
hasard), une statistique qui tient compte des essais, des contrôles de biais,
une note, un verdict et ses limites écrites.

```text
configuration versionnée → données validées → simulation (la boucle du bot)
→ performance, risque, coûts → hors échantillon, walk-forward, réglages voisins (PBO)
→ Monte-Carlo → stress des coûts, du glissement, du retard → capacité → régimes
→ références (BTC, panier, hasard) → statistique → biais → note → verdict
→ prête pour le moteur de risque, ou recherche seulement
```

Code : [`trendguard/moteur_backtest.py`](../trendguard/moteur_backtest.py).
Tests : [`tests/test_moteur_backtest.py`](../tests/test_moteur_backtest.py).
Le dernier rapport : [`VALIDATION.md`](VALIDATION.md).

```text
python trendguard_bot.py validation                  # rapport complet (≈ 3 minutes)
python trendguard_bot.py validation --rapide         # sans walk-forward ni réglages voisins
python trendguard_bot.py validation --cache data_binance --out docs/VALIDATION.md
```

## Ce que dit la validation de la règle (données Binance jusqu'au 27/09/2026)

Ce sont les réglages en vigueur qui sont validés : profil prudent, 20
positions et 10 % de risque cumulé au plus (vos choix ; la recherche avait
retenu 8 et 6 %). Verdict **VALIDE**, **prête pour le moteur de risque**,
note de qualité 98/100. Le détail est dans [`VALIDATION.md`](VALIDATION.md) :

- refaite, elle donne exactement le même résultat (empreinte identique) ;
- données : 94/100 (seule réserve : les cryptos retirées de la cote manquent) ;
- 2018-2022 : +44,0 % par an, pire baisse −32,3 % ; depuis 2023, période qui
  n'a servi à aucun choix : +34,5 % par an, pire baisse −25,2 % (frais
  compris) ;
- les 27 réglages voisins gagnent sur les deux époques ; probabilité de
  sur-ajustement (PBO) 12 % ; Sharpe dégonflé : 99,7 % pour 27 essais,
  99,4 % pour 100 ;
- walk-forward 2020 → 2026 : 10 fenêtres de 6 mois sur 14 en gain ;
- frais et glissement triplés : encore gagnante (+35,1 % et +24,8 % par an) ;
- **acheter en retard coûte cher** : depuis 2023, +21,5 % par an avec un
  jour de retard et +12,0 % avec deux, au lieu de +34,5 % ; la meilleure
  raison de garder le PC allumé à minuit ;
- capacité estimée : environ 1 million d'USDT avant que l'impact de marché ne
  dégrade nettement le résultat ;
- Monte-Carlo : 6 % de chances d'atteindre −40 % (l'arrêt d'urgence) en
  trois ans ;
- des achats au hasard au même rythme, avec les mêmes stops et la même taille,
  font moins bien 10 fois sur 10.

Un backtest n'est jamais une garantie de performance future : le rapport le
dit en premier et le contrat du résultat le refuse sans cette phrase.

## Exigences de l'étape 10 → TrendGuard

| Exigence de l'étape 10 | Dans TrendGuard |
| --- | --- |
| Contrôleur, configuration (§4-5) | une fonction lance tout dans l'ordre du pipeline (§78) ; configuration écrite en données (règle, réglages, période, capital, frais, glissement, écart, impact, délai, exécution, liquidité ; marge, financement, emprunt : sans objet en Spot), son empreinte change à la moindre modification |
| Reproductibilité (§6, §73) | manifeste `BacktestManifest.v1` : version git, empreintes des données, de la configuration, du code, de l'environnement, graine, empreinte du résultat ; le backtest est refait et comparé |
| Données validées, anti-fuite (§7-9, §71) | schéma, dates, jours manquants, prix impossibles, trous, sauts, cours figés, volumes, survivants, regard vers le futur ; note sur 100 ; sous 70 ou contrôle critique raté : on ne conclut pas |
| Rejeu, moteur événementiel, ordres (§10-13) | la boucle du bot, jour après jour, à la clôture ; ordre au marché à la clôture, comme en marche |
| Exécution réaliste (§14-21) | achat au-dessus et vente au-dessous de la clôture (glissement), frais des deux côtés, jamais au prix du signal ; retard d'un et deux jours simulé (même stop, risque jamais plus grand, achat annulé si le cours est retombé) ; impact de marché par la loi en racine carrée |
| Comptabilité, P&L, capital (§22-24) | conservation de l'argent vérifiée : un jour tout en liquide, capital = départ + gains des trades (écart ≈ 3·10⁻¹¹) |
| Performance, drawdown, références (§25-28, §49) | CAGR, Sharpe, Sortino, Calmar, gagnants, espérance en R, facteur de profit ; face à BTC : alpha, bêta, corrélation, captures ; panier à parts égales ; achats au hasard au même rythme |
| Walk-forward, hors échantillon, purge (§31-34) | apprentissage 2018-2022, test depuis 2023 ; walk-forward glissant (3 ans, 6 mois) ; validation croisée combinatoire avec 20 jours écartés à chaque bloc |
| Robustesse, sur-ajustement, PBO (§35-38) | 27 réglages voisins (plateau, pas pic) ; probabilité de sur-ajustement par validation croisée combinatoire symétrique |
| Statistique, tests multiples (§39-40) | statistique t, p-valeur, Sharpe probabiliste, Sharpe dégonflé (27 essais, et 100 par prudence), intervalles par blocs ; corrections de Bonferroni, Holm et Benjamini-Hochberg |
| Monte-Carlo, stress (§41-43, §46-48) | trajectoires de trois ans par blocs de 30 jours ; trades rééchantillonnés ; frais × 0,5 à × 3, glissement + 25 % à + 200 %, achats 1 ou 2 jours en retard |
| Régimes, capacité (§44-45) | BTC haussier ou baissier, volatilité haute ou basse ; capital de 10 000 à 100 millions d'USDT |
| Verdict, note, prêt pour le risque (§51-53, §77, §81) | VALID, VALID_WITH_WARNINGS, INVALID ou REJECTED avec les raisons ; note de qualité aux pondérations versionnées (ce qui n'est pas mesuré compte zéro) ; liste des 14 critères « prête pour le moteur de risque » ; un rapport rapide n'est jamais « prêt » |
| Rapport, explication, limites (§55-57) | 16 sections, limites obligatoires ; contrat `BacktestResult.v1` |
| Équipe rouge (§60) | regard vers le futur, survivants, exécutions impossibles, coûts oubliés, fuseau, comptabilité, taille et levier, liquidité imaginaire, sur-optimisation |
| Sécurité (§64) | étude hors ligne : aucun ordre, aucun compte, aucune clé |
| Tests (§68-73) | 13 tests : manifeste, données (doublons, prix, trous, cours figés, fuseau, volumes, fuite), formules du Sharpe, tests multiples sur un cas connu, PBO (hasard, talent, anti-persistance), retard nul = résultat identique, capacité, références et comptabilité, verdicts, contrat, validation complète, Rachelle |

## Ce qui ne s'applique pas

- **Ticks, carnet d'ordres, ordres limites, exécutions partielles** (§10,
  §13-15) : le bot décide une fois par jour sur des bougies journalières et
  achète au marché ; à sa taille, un ordre représente moins de 0,03 % du
  volume du jour (la capacité le mesure).
- **Backtest vectorisé séparé** (§12) : la boucle du bot rejoue neuf ans en moins
  d'une seconde ; une seconde boucle serait une seconde vérité à tenir.
- **Marge, financement, emprunt, opérations sur titres** (§18) : Binance Spot,
  sans levier ni vente à découvert ; un retrait de la cote est simulé (vente
  avec décote de 50 %).
- **Base de données des backtests** (§61) : le rapport et son manifeste sont
  versionnés dans le dépôt ; le registre des expériences garde déjà les rejeux
  du bot.
