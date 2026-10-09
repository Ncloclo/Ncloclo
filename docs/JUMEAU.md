# Jumeau numérique et simulation (étape 28 du prompt maître)

Le jumeau est une copie du bot qui tourne dans le simulateur : la même boucle
que le backtest, les mêmes réglages, le même capital. Il sert à voir si le
bot fait bien ce que le simulateur prévoit, et à mesurer ce qui pourrait
arriver (crises, hasard, pannes, réglages un peu différents). Une simulation
n'est jamais une observation : le jumeau n'écrit rien dans le bot et ne parle
pas à Binance.

Code : [`trendguard/jumeau.py`](../trendguard/jumeau.py). Tests :
[`tests/test_jumeau.py`](../tests/test_jumeau.py).

```text
python trendguard_bot.py jumeau                      # synchronisation, Monte-Carlo, crises, sensibilité
python trendguard_bot.py jumeau --out docs/JUMEAU_ETAT.md
```

Dernier résultat : [`JUMEAU_ETAT.md`](JUMEAU_ETAT.md). Chaque nuit, le rapport
donne la synchronisation (ligne « Jumeau numérique »).

## Ce que fait le jumeau

| Exigence de l'étape 28 | Dans TrendGuard |
| --- | --- |
| Synchronisation, état du jumeau (§9-10) | les achats de l'essai paper rejoués par la boucle de backtest depuis le début de l'essai ; part des achats identiques, chaque écart expliqué ou non |
| Monte-Carlo (§14) | trois ans tirés au hasard par blocs de 30 jours ; 250, 500, 1 000, 2 000 puis 4 000 tirages ; convergé quand deux niveaux successifs diffèrent de moins de 1 % sur la pire baisse atteinte une fois sur vingt ; sinon MONTE_CARLO_NOT_CONVERGED ; graine notée |
| Scénarios (§15) | les crises et les hausses passées rejouées (2018, Covid, mai 2021, LUNA, FTX, hausses de 2020-2021 et 2023-2024) ; une période hors des données est dite absente, jamais inventée |
| Panne injectée (§17) | trois jours de cours effacés : le jumeau doit tourner quand même ; l'écart est mesuré |
| Sensibilité (§23) | stop initial, stop suiveur, cassure, filtre BTC, chacun à −10 % et +10 % ; effet sur le rendement annuel et la pire baisse |
| Données synthétiques (§25) | chaque résultat dit si ses données sont réelles ou synthétiques, avec leur empreinte |
| Isolation (§27, §37) | aucune bibliothèque réseau dans le module (vérifié par lecture du code), aucune écriture dans le bot |
| Reproductibilité (§42) | même graine, mêmes données : même résultat (vérifié à chaque appel) |

Un réglage n'est jamais changé d'après le jumeau seul : l'évolution encadrée
l'éprouve d'abord sur deux époques, puis 30 jours sur le vrai marché.

## La note (§44, §46)

Fidélité du modèle 20 %, synchronisation 15 %, validation et calibrage 15 %,
justesse de la simulation 15 %, scénarios et crises 10 %, reproductibilité
10 %, sécurité et isolation 5 %, performance 5 %, observabilité 5 %. Prêt
(READY) seulement avec au moins 95, un Monte-Carlo convergé et aucun défaut
P0 (justesse, reproductibilité, isolation) ; sinon REJECTED. Le contrat
`TwinReport.v1` refuse un jumeau qui écrirait dans la production.

## Ce qui ne s'applique pas

- **Jumeaux d'énergie, d'équipements, de réseaux électriques, matériel dans la
  boucle** (§8.1, §18, §28) : le bot n'a ni capteur ni machine.
- **Co-simulation, simulation multi-fidélité, orchestrateur de simulations**
  (§12-13, §26) : un seul simulateur, la boucle de backtest du dépôt.
- **Jumeau cyber** (§20) : la posture de sécurité est mesurée par
  [`CYBERSECURITE.md`](CYBERSECURITE.md), sans simulation d'attaque.
- **Contrefactuels** (§16) : faits par l'intelligence causale
  ([`CAUSAL.md`](CAUSAL.md)), interventions dans le même simulateur.
