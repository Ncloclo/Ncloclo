# Le prompt maître appliqué à TrendGuard

Votre demande (6 octobre) : utiliser le « prompt maître ultime » (67
sections : plateforme IA cognitive, multi-IA, multi-agents, finance
quantitative, risque, trading) pour améliorer le bot.

Le prompt lui-même impose d'avancer par phases validées (§57, §58) et de ne
jamais passer du code au réel sans étapes (§54). Je l'ai donc appliqué comme
une **grille d'exigences** : ce que le bot faisait déjà, ce qui manquait et a
été ajouté le 6 octobre (en deux phases, à votre deuxième demande « améliore
avec ce prompt »), ce qui viendra ensuite, et ce qui ne s'applique pas. Pas de
réécriture : le bot tourne, ses tests passent, et ses règles sont éprouvées
sur 8 ans.

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

## Ajouté le 6 octobre, deuxième phase

| Exigence du prompt | Ce qui a été fait | Effet sur les décisions |
| --- | --- | --- |
| Qualité des données (§11) | `qualite.py` : note sur 100 des clôtures de l'année avant chaque décision (dernière bougie du jour, une bougie par jour, ni doublon ni trou, aucun prix manquant, nul ou négatif, pas de cours figé trois jours, mouvements de plus de 50 % en un jour signalés) | aucun : la garde bloque déjà les achats quand trop de clôtures manquent ; la note s'affiche dans le rapport, et dans le raisonnement dès qu'elle baisse |
| Tests de résistance élargis (§35) | `stress.py` : à chaque décision, ce que coûteraient aux positions du moment un krach de 20, 35 ou 50 % sans exécution des stops (ou Binance en panne pendant la chute), une crise de liquidité (ventes 10 % sous les stops), un pic de volatilité (tous les stops touchés, 2 % de glissement), le retrait de la cote de la plus grosse position, un décrochage de l'USDT de 10 % ; et si l'arrêt d'urgence se déclencherait | aucun : mesure (raisonnement, page Positions, rapport) |
| Attribution de performance (§36) | `attribution.py` : résultat par crypto (réalisé et en cours), par régime à l'achat, par type de sortie, par leçon ; part des gains venue des trois meilleurs trades ; « pourquoi ai-je gagné, pourquoi ai-je perdu ? » en clair | aucun : page Positions et rapport |
| Calendrier d'événements (§33) | `evenements.py` : annonces américaines à fort impact (Fed, inflation, emploi…) d'après le calendrier public de ForexFactory, relu au plus toutes les 6 heures par le noyau de savoir ; montrées dans la page Veille et dans le raisonnement 48 heures avant ; gardées pour mesurer, avec le temps, combien le bitcoin bouge ces jours-là | aucun : aucun achat n'est bloqué tant qu'un effet n'est pas prouvé (au moins 10 jours d'annonce mesurés, puis une étude) |
| Registre des expériences et des versions (§41, §42), carte du modèle (§50) | `registre.py` : chaque épreuve quotidienne de l'évolution et chaque fin d'essai notées (numéro, date, version du code, empreinte des données, réglages, résultats, conclusion) ; `registre rejouer <n°>` refait l'expérience et dit si les résultats sont identiques ; `registre carte` : usage, règles en vigueur, validation, limites, garde-fous | aucun : traçabilité ; l'évolution décide comme avant |

Commandes :

    python trendguard_bot.py registre                 # les dernières expériences
    python trendguard_bot.py registre rejouer E0001   # refaite à l'identique ?
    python trendguard_bot.py registre controle        # rejeu des réglages en vigueur, noté
    python trendguard_bot.py registre carte           # la carte du modèle

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

## Ce qui reste (votre accord d'abord)

1. **Débat des IA** (§43) : rôles haussier, baissier, critique et synthèse
   dans la veille. Il attend des clés d'IA : sans elles, il n'y a personne
   pour débattre (`python trendguard_bot.py watch set-key claude`, clé
   saisie masquée).
2. **Effet des annonces économiques** : quand le calendrier aura mesuré au
   moins 10 jours d'annonce, une étude dira si s'abstenir d'acheter ces
   jours-là aurait aidé ; une règle ne sera proposée que si elle est prouvée.
3. **Réel** (§22, §29) : seulement après des semaines de paper avec des
   trades vendus, des alertes qui arrivent, une machine fixe et de l'argent
   sur le compte.

## Ce qui ne s'applique pas, et pourquoi

| Exigence du prompt | Pourquoi pas ici |
| --- | --- |
| Analyse fondamentale d'entreprises (§12 : P/E, EBITDA, DCF…) | le bot trade des cryptos : ni bilan, ni bénéfice, ni dividende |
| Vente à découvert, couverture, levier (§17) | Binance Spot, achat seul, par choix de sécurité : on ne perd au pire que ce qu'on a acheté |
| Pile FastAPI, PostgreSQL, Redis, React, base vectorielle (§55) | pour un bot qui tourne sur un seul PC, SQLite et le panneau actuel suffisent ; tout réécrire coûterait des mois sans rendre le bot plus sûr ni plus rentable. L'architecture reste modulaire : un module par rôle |
| Agents « docteur en finance », « gérant de portefeuille »… (§3) | ces rôles sont tenus par des règles testées et par les IA consultées, pas par des promesses d'expertise |
| Macro complète (§7) | pas de données macro gratuites et fiables intégrées ; le régime de BTC, la veille et le calendrier des annonces américaines en tiennent lieu |

Les règles d'or du prompt sont celles du bot depuis le début : préférer « NO
TRADE » à un trade mal justifié (§62), ne jamais croire un bon backtest seul
(§61), ne jamais modifier la production sans validation (§39, §64), ne rien
inventer (§60).
