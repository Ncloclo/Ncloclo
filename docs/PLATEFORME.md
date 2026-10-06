# Le prompt maître appliqué à TrendGuard

Votre demande (6 octobre) : utiliser le « prompt maître ultime » (67
sections : plateforme IA cognitive, multi-IA, multi-agents, finance
quantitative, risque, trading) pour améliorer le bot.

Le prompt lui-même impose d'avancer par phases validées (§57, §58) et de ne
jamais passer du code au réel sans étapes (§54). Je l'ai donc appliqué comme
une **grille d'exigences** : ce que le bot faisait déjà, ce qui manquait et a
été ajouté le 6 octobre, ce qui viendra ensuite, et ce qui ne s'applique pas.
Pas de réécriture : le bot tourne, ses 560 tests passent, et ses règles sont
éprouvées sur 8 ans.

## Ce que le bot faisait déjà

| Exigence du prompt | Dans TrendGuard |
| --- | --- |
| Multi-IA derrière une même interface (§6) | veille : Claude, GPT, Gemini, DeepSeek, Mistral, Kimi, Perplexity, Grok ; poids de chaque IA selon sa fiabilité mesurée (`market_watch.py`) |
| Recherche, mémoire, apprentissage (§31, §39, §45) | noyau de savoir (`savoir.py`), apprentissage libre (`learning.py`), évolution encadrée (`evolution.py`) |
| Pas de fuite d'information (§16) | indicateurs causaux, décision à la clôture, exécution après ; mêmes fonctions pour le backtest et le bot ; actifs disparus inclus (biais du survivant) |
| Backtest réaliste (§19) | frais et glissement, plafonds du bot ; rendement, baisse maximale, Sharpe, Sortino, Calmar, espérance, facteur de profit |
| Hors échantillon, walk-forward (§20, §61) | choix sur 2018-2022, vérification depuis 2023, walk-forward ([`TRENDGUARD_REPORT.md`](TRENDGUARD_REPORT.md), [`ROBUSTESSE.md`](ROBUSTESSE.md)) |
| Monte-Carlo (§21) | épreuve du hasard (3 ans rejoués 1 000 fois), Monte-Carlo des trades |
| Moteur de risque (§23, §24, §30) | 1 % par achat, plafond cumulé, nombre de positions, 25 % par position, profil prudent, arrêt d'urgence |
| Modes et passage au réel (§28, §29) | rejeu, paper, testnet, réel verrouillé (deux réglages explicites), `verify` sans ordre |
| « NO TRADE » préféré (§25, §62) | aucun achat sans cassure, en marché baissier, budget plein, retrait annoncé par Binance, carnet d'ordres anormal |
| Exécution sûre (§27) | intention écrite avant chaque ordre, rapprochement au démarrage, stop de secours posé chez Binance |
| Recherche séparée de la production (§40, §64) | `research/`, laboratoire, épreuves de l'évolution ; bot libre en argent fictif |
| Familles de stratégies (§18) | tournoi de 12 stratégies, dont 5 de traders célèbres ([`STRATEGIES.md`](STRATEGIES.md)) |
| Tableau de bord, alertes, surveillance (§46, §47, §51) | panneau, alertes e-mail et WhatsApp, disponibilité, ressources du PC, plantages de Windows, rapport quotidien |
| Sécurité (§48) | clé au moindre privilège, secrets hors du dépôt public, panneau limité au PC, centre de sécurité |
| Tests et robustesse (§52, §53) | plus de 560 tests : pannes réseau, données manquantes, horloge, reprise après plantage ; contrôles GitHub |
| Pas d'invention (§60) | données réelles de Binance, études reproductibles, résultats écrits tels quels |

## Ajouté le 6 octobre

| Exigence du prompt | Ce qui a été fait | Effet sur les décisions |
| --- | --- | --- |
| Moteur de régimes de marché (§8), « dans quelles conditions la stratégie a-t-elle échoué ? » (§45) | `regimes.py` : tendance de BTC, volatilité, appétit pour le risque, phase (crise, reprise) ; étude [`REGIMES.md`](REGIMES.md) | aucun : information affichée et gardée avec chaque trade |
| Moteur « NO TRADE » et garde avant exécution (§29, §44, §53) | `garde.py` : données du jour, mouvement de BTC, perte du jour, place sur le disque ; un seul « non » et aucun achat ce jour-là | seulement dans l'anormal (voir l'étude ci-dessous) |
| Journal des trades et analyse après trade (§37, §38) | `postmortem.py` : régime à l'achat, meilleur et pire moment en R, glissement sous le stop, leçon en clair ; colonne « Leçon » dans le panneau, bilan dans le rapport | aucun : apprendre et expliquer |
| VaR et CVaR (§13, §23) | `risque.py` : risque d'un jour du portefeuille, année écoulée rejouée avec les positions actuelles | aucun : mesure affichée dans le raisonnement |

**Ce que dit l'étude des régimes** : depuis 2018, la stratégie n'a perdu en
moyenne dans aucun régime où elle achète. Elle gagne le moins en phase de
« reprise » (+0,29 R par trade) et quand l'appétit pour le risque est
« mitigé » (+0,51 R), le plus en tendance haussière et en « risk-on »
(+1,25 et +1,32 R).

**Seuils de la garde** (`python -m research.garde --cache data_binance`) :
une règle n'est gardée que si elle ne change rien à la stratégie sur 8 ans.

| Règle | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | hasard 1 fois sur 20 | jours sans achat |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bot sans garde | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 0 |
| perte du jour ≥ 3 % | +31,3 % | −23,4 % | 1,34 | +30,9 % | −24,2 % | 1,28 | −35 % | 66 |
| perte du jour ≥ 5 % | +31,8 % | −20,6 % | 1,54 | +30,9 % | −24,2 % | 1,28 | −35 % | 27 |
| **perte du jour ≥ 8 %** (retenue) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 5 |
| BTC bouge de ≥ 10 % en un jour | +32,1 % | −23,5 % | 1,37 | +32,4 % | −24,2 % | 1,34 | −35 % | 62 |
| **BTC bouge de ≥ 15 % en un jour** (retenue) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 8 |

Des seuils plus stricts font moins bien : s'interdire d'acheter après une
mauvaise journée fait manquer des reprises. Les seuils retenus ne changent
aucun résultat passé et bloquent seulement l'exceptionnel (krach, donnée
aberrante).

## Phases suivantes proposées (votre accord d'abord)

1. **Registre des expériences et des versions** (§41, §42, §50) : chaque
   épreuve de l'évolution et chaque étude enregistrées avec la version du
   code, les données, les réglages et les résultats, pour pouvoir tout
   rejouer.
2. **Débat des IA** (§43) : rôles haussier, baissier, critique et synthèse
   dans la veille, dès que des clés d'IA sont enregistrées.
3. **Calendrier d'événements** (§33) : décisions de la banque centrale
   américaine, inflation, emploi, affichés et notés (information).
4. **Tests de résistance élargis** (§35) : krach crypto corrélé, crise de
   liquidité (glissement multiplié par dix), pic de volatilité.
5. **Attribution de performance** (§36) : résultat par crypto, par régime,
   par type de sortie.
6. **Réel** (§22, §29) : seulement après des semaines de paper avec des
   trades vendus, des alertes qui arrivent, une machine fixe et de l'argent
   sur le compte.

## Ce qui ne s'applique pas, et pourquoi

| Exigence du prompt | Pourquoi pas ici |
| --- | --- |
| Analyse fondamentale d'entreprises (§12 : P/E, EBITDA, DCF…) | le bot trade des cryptos : ni bilan, ni bénéfice, ni dividende |
| Vente à découvert, couverture, levier (§17) | Binance Spot, achat seul, par choix de sécurité : on ne perd au pire que ce qu'on a acheté |
| Pile FastAPI, PostgreSQL, Redis, React, base vectorielle (§55) | pour un bot qui tourne sur un seul PC, SQLite et le panneau actuel suffisent ; tout réécrire coûterait des mois sans rendre le bot plus sûr ni plus rentable. L'architecture reste modulaire : un module par rôle |
| Agents « docteur en finance », « gérant de portefeuille »… (§3) | ces rôles sont tenus par des règles testées et par les IA consultées, pas par des promesses d'expertise |
| Macro complète (§7) | pas de données macro gratuites et fiables intégrées ; le régime de BTC et la veille en tiennent lieu |

Les règles d'or du prompt sont celles du bot depuis le début : préférer « NO
TRADE » à un trade mal justifié (§62), ne jamais croire un bon backtest seul
(§61), ne jamais modifier la production sans validation (§39, §64), ne rien
inventer (§60).
