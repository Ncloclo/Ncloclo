# Acceptation du paper (étape 13 du prompt maître)

Le treizième document fixe des critères d'acceptation mesurables pour le paper :
44 critères, AC-001 à AC-044, chacun avec une priorité (P0 : bloquant absolu),
une note de préparation pondérée, une période d'observation minimale, une
comparaison entre le paper et le backtest, et un verdict. Le bot les mesure
lui-même : `python trendguard_bot.py acceptation`, chaque nuit dans le rapport,
et Rachelle le dit (« le paper est-il accepté ? »). Dernier verdict :
[`ACCEPTATION.md`](ACCEPTATION.md).

Code : [`trendguard/acceptation.py`](../trendguard/acceptation.py).
Tests : [`tests/test_acceptation.py`](../tests/test_acceptation.py).

## Comment chaque critère est prouvé

- **mesuré sur le bot** : capital de départ et date de l'essai, comptabilité
  recalculée (liquidités + coût des positions = capital de départ + résultat
  réalisé, à 0,01 près), coût de chaque position, résultat de chaque trade
  recalculé, glissement noté, ordre des dates, limites de risque, levier,
  journal financier intact et lignée de chaque trade, audit chaîné intact,
  qualité des données, disponibilité, latence du contrôle du risque (mesurée,
  cible 50 ms au 95e centile) et blocage par l'arrêt d'urgence (cible 100 ms) ;
- **prouvé par un test du dépôt** : chaque critère cite ses tests, que GitHub
  rejoue à chaque envoi ; un test introuvable n'est jamais compté ;
- **sans objet** : dit pourquoi, et ne compte pas dans la note (marge : Spot
  sans emprunt ; carnet d'ordres simulé : ordres au marché sur bougies
  journalières).

Une mesure impossible n'est jamais une réussite : pour un P0, elle bloque.

## Le verdict

```text
ACCEPTÉ (prêt pour le moteur de politique) =
  aucun P0 raté ni impossible à mesurer
  ET note ≥ 95/100 (sécurité 20 %, comptabilité 15 %, exécution 15 %, risque 15 %,
     données 10 %, reproductibilité 10 %, observabilité 5 %, sécurité informatique 5 %,
     performance 5 %)
  ET observation : au moins 30 jours ET 30 événements (achats et ventes)
  ET paper identique au backtest de la même période (écarts expliqués)
  ET backtest validé, moteur de risque sans blocage
sinon BLOQUÉ, avec ce qui manque
```

**Pourquoi 30 événements et pas 100** : le prompt l'admet pour une stratégie
peu fréquente, à condition de le justifier. La règle fait environ 50 trades par
an (430 depuis 2018) : 100 événements demanderaient près d'un an. Le prompt
recommande 90 jours ; le minimum est de 30.

**Le paper contre le backtest** : les achats de l'essai sont rejoués par la
boucle de backtest du bot, même capital, même départ, mêmes réglages. Chaque
écart est classé : expliqué (par exemple un stop de secours touché dans la
journée, que le backtest, qui ne voit que les clôtures, ne peut pas voir) ou
inexpliqué (bloquant).

**Le premier essai** : les achats du 6 octobre à 00:02 précèdent la mise en
service du journal financier (la première décision journalisée est celle du 7) ;
ils sont signalés et exclus de la lignée, jamais comptés comme tracés.

## Exigences de l'étape 13 → TrendGuard

| Exigence | Dans TrendGuard |
| --- | --- |
| Critères AC-001 à AC-044, priorités P0 à P3 (§2-46) | chacun mesuré ou prouvé, avec son état : conforme, non conforme, non mesurable, sans objet |
| Note de préparation (§47) | pondérée par famille ; bandes : rejeté, en développement, en validation, presque prêt, prêt ; un P0 raté bloque même avec 99/100 |
| Durée d'observation (§48) | 30 jours et 30 événements au moins (justifié), 90 jours recommandés |
| Performance observée, divergence (§49-50) | aucun incident critique inexpliqué ; écart paper/backtest classé et expliqué |
| Gouvernance (§51) | fiche de la règle en essai, backtest validé, moteur de risque, moteur de portefeuille |
| Verdict et formule (§52-53) | `PaperAcceptanceReport.v1` : ACCEPTED et POLICY_ENGINE, ou BLOCKED et CORRECTION, avec ce qui manque |
| Principe absolu (§54) | le paper valide la préparation ; il n'autorise jamais le réel (le contrat le refuse) |

## Ce qui ne s'applique pas

- **Carnet d'ordres simulé, exécutions partielles en paper** (AC-012, AC-017) :
  ordres au marché à la clôture, moins de 0,03 % du volume du jour ; les
  exécutions partielles du réel sont éprouvées contre un faux Binance.
- **Marge, levier** (AC-021, AC-022) : Binance Spot, sans emprunt.
- **Plusieurs comptes** (AC-033) : une base par mode (paper, testnet, réel), et
  le
  bot libre dans sa propre base.
- **Couverture des tests en lignes** (AC-041) : non mesurée (pas d'outil de
  couverture installé) ; le critère est dit « non mesurable », jamais conforme.
- **Disponibilité de 99,9 %** (AC-030) : mesurée honnêtement sur 7 jours ; un PC
  personnel qui dort ou se met à jour ne l'atteint pas toujours.
