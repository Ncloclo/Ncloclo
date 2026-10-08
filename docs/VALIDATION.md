# Validation du backtest de la règle (moteur de backtest, étape 10)

Rapport reproductible : `python trendguard_bot.py validation --cache data_binance --out docs/VALIDATION.md`. Données Binance jusqu'au 2026-09-27.

## 1. Résumé

Verdict : **VALIDE**, prête pour le moteur de risque. Note de qualité 98/100.


> Un backtest n'est pas une garantie de performance future.

## 2. Stratégie et données

Fiche `trendguard.cassure.prudent` v1.0.0 (empreinte d50fc41d42bd0ec0) : prête pour le backtest.
Données : 2017-08-17 → 2026-09-27, 21 cryptos, empreinte e0264fffe6b7bbd9 ; note 94/100 (PASS).
Réglages en vigueur différents de ceux de la recherche : max_positions 20 (recherche : 8), max_total_risk 0,1 (recherche : 0,06), dd_throttle [[0.1, 0.5]] (recherche : []).

| Contrôle des données | Résultat | Détail |
| --- | --- | --- |
| Schéma | ✓ | dates UTC, BTC présent, valeurs numériques |
| Dates | ✓ | 0 doublon(s), 0 hors de 00:00 UTC |
| Jours manquants | ✓ | 0 jour(s) sur 3329 |
| Prix impossibles | ✓ | 0 prix nul(s), négatif(s) ou infini(s) |
| Trous après cotation | ✓ | 0 clôture(s) manquante(s) |
| Sauts extrêmes | ✓ | 2 variation(s) de plus de ×2 ou ÷2 en un jour |
| Cours figés | ✓ | 0 série(s) de 5 clôtures identiques ou plus |
| Volumes | ✓ | connus 100,0 % des jours cotés, 0 négatif(s) |
| Survivants | ! | seules les cryptos encore cotées : biais du survivant possible |
| Fuite vers le futur | ✓ | indicateurs et régime identiques sans l'avenir |

## 3. Configuration et manifeste

Moteur 1.0.0, code `e0d242d` (empreinte 8e177dacdd5e0f19), configuration abd58fd7597a8006, environnement 8a1837b9c120db62 (Python 3.13.2, numpy 2.5.3, pandas 3.0.6), graine 7. Résultat a6d598391bbbb3c3 ; refait : identique.

## 4. Exécution et coûts

- frais : fixe, 0,1 % par côté
- glissement : fixe, 0,1 % par côté
- écart achat-vente : compris dans le glissement (achat au-dessus, vente au-dessous de la clôture)
- impact de marché : aucun à la taille du bot ; estimé par la capacité (loi en racine carrée)
- délai d'exécution : décision à la clôture, achat dans les minutes qui suivent ; retard d'un ou deux jours éprouvé à part
- exécution : ordre au marché à la clôture, Binance Spot
- liquidité : volume moyen de 30 jours d'au moins 5 000 000 dollars
- marge : sans objet (Spot, sans levier)
- financement : sans objet (Spot)
- emprunt : sans objet (pas de vente à découvert)
- opérations sur titres : sans objet (cryptos) ; retrait de la cote : vente avec décote de 50 %

## 5. Performance et risque

| Période | CAGR | Pire baisse | Sharpe | Calmar | Trades |
| --- | --- | --- | --- | --- | --- |
| 2018-2022 (apprentissage) | +44,0 % | −32,3 % | 1,19 | 1,36 | 196 |
| Depuis 2023 (hors échantillon) | +34,5 % | −25,2 % | 1,20 | 1,37 | 218 |
| 2018 → aujourd'hui | +39,2 % | −33,0 % | 1,19 | 1,19 | 427 |

Gagnants 40 %, gain moyen 4,34 R, perte moyenne −0,97 R, espérance +1,17 R par trade (frais compris), facteur de profit 2,34, durée moyenne 18 jours.

## 6. Hors échantillon et walk-forward

Sharpe depuis 2023 / Sharpe 2018-2022 : 1,01 ; risque de sur-ajustement LOW.
Walk-forward 2020 → aujourd'hui (réglages choisis sur les 3 années précédentes, testés 6 mois) : 10/14 fenêtres en gain, CAGR enchaîné +57,2 %, pire baisse −41,5 %, réglages changés 7 fois.

## 7. Robustesse : réglages voisins

27 réglages voisins : 100 % gagnants sur les deux époques ; probabilité de sur-ajustement (PBO, 252 découpages) 12 % ; après correction des tests multiples, 27 voisins restent significatifs (Holm), 27 (BH).

## 8. Monte-Carlo

2000 trajectoires de trois ans par blocs de 30 jours : rendement médian +153 % (5 % pires : +4 %), en perte 4 % des fois ; pire baisse médiane 27 %, 41 % une fois sur 20 ; −40 % (arrêt d'urgence) atteint 6 % des fois.

## 9. Stress : coûts, glissement, retard

| Épreuve | CAGR 2018-2022 | CAGR depuis 2023 | Sharpe depuis 2023 |
| --- | --- | --- | --- |
| Frais et glissement × 0,5 | +46,0 % | +38,0 % | 1,28 |
| Frais et glissement × 1 | +44,0 % | +34,5 % | 1,20 |
| Frais et glissement × 1,5 | +41,3 % | +31,8 % | 1,14 |
| Frais et glissement × 2 | +40,0 % | +30,2 % | 1,10 |
| Frais et glissement × 3 | +35,1 % | +24,8 % | 0,95 |
| Glissement + 25 % | +44,3 % | +35,7 % | 1,22 |
| Glissement + 50 % | +43,2 % | +32,7 % | 1,17 |
| Glissement + 100 % | +41,3 % | +31,8 % | 1,14 |
| Glissement + 200 % | +40,0 % | +30,2 % | 1,10 |
| Achats 1 jour(s) en retard | +38,3 % | +21,5 % | 0,87 |
| Achats 2 jour(s) en retard | +30,0 % | +12,0 % | 0,60 |

## 10. Capacité (depuis 2023)

| Capital | Part du volume (médiane, 95 %) | Impact estimé | CAGR | Sharpe | Réaliste |
| --- | --- | --- | --- | --- | --- |
| 10 000 USDT | 0,002 % ; 0,021 % | 0,021 % | +35,8 % | 1,23 | oui |
| 100 000 USDT | 0,024 % ; 0,213 % | 0,066 % | +32,4 % | 1,16 | oui |
| 1 000 000 USDT | 0,241 % ; 2,125 % | 0,210 % | +29,7 % | 1,09 | oui |
| 10 000 000 USDT | 2,408 % ; 21,252 % | 0,664 % | +18,6 % | 0,79 | non |
| 100 000 000 USDT | 24,078 % ; 212,524 % | 2,099 % | +4,9 % | 0,33 | non |

## 11. Régimes

| Régime | Jours | Rendement | Sharpe | Pire jour | Achats |
| --- | --- | --- | --- | --- | --- |
| BTC haussier | 1651 | +1994 % | 1,74 | −17,3 % | 427 |
| BTC baissier | 1540 | −14 % | −0,98 | −3,2 % | 0 |
| volatilité haute | 842 | +64 % | 0,72 | −17,3 % | 91 |
| volatilité basse | 2349 | +996 % | 1,47 | −14,3 % | 336 |

## 12. Références

BTC acheté et gardé : CAGR +23,5 %, pire baisse −81,2 % ; face à lui, la règle : alpha +31,0 %/an, bêta 0,17, corrélation 0,34, capture haussière 23 %, baissière 16 %.
Panier des cryptos à parts égales : CAGR +29,8 %, pire baisse −86,2 %.
Achats au hasard au même rythme (10 tirages) : CAGR médian +27,9 %, Sharpe médian 0,88 ; la règle fait mieux 10 fois sur 10.

## 13. Statistique

Sharpe 1,19 (intervalle à 95 % par blocs : 0,52 à 1,85), t = 3,51, p-valeur 0,0002 ; Sharpe probabiliste 100,0 % ; dégonflé pour 27 essais 99,7 % (seuil du hasard 0,28), pour 100 essais (compte prudent) 99,4 %.

## 14. Contrôles de biais (équipe rouge)

- ✓ Regard vers le futur : indicateurs et régime identiques sans l'avenir
- ✗ Survivants : cryptos retirées de la cote absentes : biais possible, mesuré à part (--cm)
- ✓ Exécutions impossibles : achats et ventes à la clôture, glissement contre le bot, jamais au prix du signal ; sorties sous le stop au cours de clôture, pas au stop
- ✓ Coûts oubliés : frais et glissement à l'achat et à la vente ; stress jusqu'à × 3
- ✓ Fuseau horaire : 0 doublon(s), 0 hors de 00:00 UTC
- ✓ Comptabilité (gains et pertes) : au 2026-08-18, capital − départ − gains des trades = −2,91e−11
- ✓ Taille et levier : taille par le risque jusqu'au stop, plafonds, sans levier (Spot)
- ✓ Liquidité imaginaire : volume minimum de 5 M$ ; capacité estimée à part
- ✓ Sur-optimisation : voisins et PBO

## 15. Prêt pour le moteur de risque ?

| Critère | Résultat |
| --- | --- |
| Validation des données | PASS |
| Anti-regard vers le futur | PASS |
| Validation de la stratégie | PASS |
| Réalisme de l'exécution | ACCEPTABLE |
| Backtest | PASS |
| Hors échantillon | ACCEPTABLE |
| Walk-forward | ACCEPTABLE |
| Robustesse (voisins, PBO) | ACCEPTABLE |
| Validation statistique | ACCEPTABLE |
| Stress (Monte-Carlo, −40 %) | ACCEPTABLE |
| Coûts (frais doublés) | ACCEPTABLE |
| Liquidité (capacité à 100 000 USDT) | ACCEPTABLE |
| Contrôles de biais | PASS |
| Reproductibilité | PASS |

Note de qualité 98/100 (données 94, exécution 100, échantillon 100, hors échantillon 100, robustesse 94, statistique 100, reproductibilité 100 ; qualite-backtest-1.0.0).

## 16. Limites

- Un backtest n'est pas une garantie de performance future.
- Bougies journalières de Binance seulement : ni carnet d'ordres, ni ticks ; achats et ventes simulés à la clôture, glissement et frais fixes par côté.
- Cryptos encore cotées aujourd'hui : celles retirées de la cote (FTT, LUNA…) manquent, un biais du survivant est possible (le laboratoire le mesure avec les données Coin Metrics : --cm).
- Deux époques de marché (2018-2022, 2023 → aujourd'hui) : un régime jamais vu reste possible.
- Réglages choisis sur 2018-2022 parmi d'autres essais : le Sharpe dégonflé en tient compte, pas entièrement.
- Capacité et impact de marché estimés (loi en racine carrée), pas observés.
