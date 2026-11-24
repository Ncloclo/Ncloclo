# Diagnostic expert de l'intelligence du bot (24 novembre 2026)

Votre demande : vérifier l'intelligence du bot, ses compétences, ses
capacités, son autonomie et ses performances, corriger les manques, et le doter
d'un cerveau d'expert qui apprend seul. Mesuré sur le bot en marche (paper,
21 paires), avec `python trendguard_bot.py diagnose` et l'état du bot.

## Verdict

- **Le cerveau existe et il est bien conçu.** Il comprend une stratégie
  validée hors échantillon, quinze agents, un noyau de savoir qui lit Internet,
  une évolution encadrée, un bot libre et un modèle du monde.
- **Ses vrais manques étaient ailleurs.** Ils tenaient à des sources (aucune
  IA configurée, Reddit refusé, aucune recherche lue) et à l'autonomie (aucun
  redémarrage avec Windows).
- **Le marché baissier explique l'inactivité actuelle.** Aucun achat n'est
  permis tant que BTC reste sous sa moyenne de 150 jours (−16 % le 23/11,
  depuis 12 jours). C'est la règle qui protège le capital, pas une panne.

## Intelligence : la décision

| Mesure (prix Binance, frais et glissement compris) | Résultat |
| --- | --- |
| Backtest 2019 → 23/11/2026 | +42,6 %/an, baisse max −33,6 %, Sharpe 1,24, 318 trades |
| 12 derniers mois glissants | +13,6 % (médiane historique +30,4 %) |
| Fenêtres de 12 / 24 / 36 mois finies en gain | 80 % / 92 % / 100 % |
| 79 trades des 24 derniers mois | espérance +1,29 R (intervalle 90 % : +0,47 à +2,20 R) |
| Tournoi de 12 stratégies (`docs/STRATEGIES.md`) | aucune ne bat TrendGuard sur 2018-2022 puis depuis 2023 |
| Suivre la meilleure stratégie du moment | pas mieux que de s'en tenir à TrendGuard |

- Sur les 24 derniers mois seuls, TrendGuard n'est que 7e sur 12, derrière la
  cassure de Bollinger et les Tortues. Une fenêtre courte ne suffit pas pour
  conclure, et le bot a raison de ne pas en changer seul.
- Chaque règle compte (`docs/EXAMEN.md`). Le petit capital est réglé depuis
  le 24/11 (`docs/PETIT_CAPITAL.md`).
- Aucun résultat passé n'est une garantie : le bot traverse des baisses de
  25 à 35 % et des séries de 8 à 13 pertes.

## Compétences et capacités : état réel

| Module | État le 24/11/2026 |
| --- | --- |
| Comité de 15 agents | prêt ; n'a pas siégé (aucun candidat à l'achat en marché baissier) |
| Noyau de savoir | environ 590 textes par jour ; 7 sources sur 8 lues |
| Sources prouvées | aucune pour l'instant : il faut 20 semaines vérifiées par source |
| Conseil des IA | **inactif : aucune clé d'IA enregistrée** |
| Reddit | **refuse les lectures sans compte** (404, 429) |
| Recherche scientifique | **absente** jusqu'au 24/11 |
| Évolution encadrée | niveau Apprenti ; 10 réglages essayés, aucun n'a réussi toutes les épreuves |
| Bot libre (argent fictif) | 9 980 USDT sur 10 000 ; repart avec 100 USDT à la prochaine décision |

Le noyau de savoir ne laisse aucune source non prouvée influencer le bot.
C'est la prudence d'un expert : la plupart des avis lus sur Internet ne
prédisent pas mieux que le hasard.

## Autonomie

- Le superviseur relance le bot après un plantage, et l'anti-veille garde le
  PC éveillé pendant que le bot tourne.
- **Manque** : le démarrage avec Windows n'est pas activé. Le redémarrage du
  PC du 24/11 à 14:01 a laissé le bot arrêté jusqu'à une relance à la main.
- **Manque** : le service d'heure de Windows est arrêté. Le PC retarde de
  8 secondes ; le bot se règle sur l'heure de Binance.

## Corrigé le 24 novembre

- **Bibliothèque de recherche** (`docs/BIBLIOTHEQUE.md`) : chaque jour, les
  travaux de finance quantitative d'arXiv qui touchent aux règles du bot,
  chacun relié à la règle concernée et à la question à vérifier.
- **Agent « chercheur »** (douzième agent du comité) : il cite ces travaux à
  chaque achat étudié, sans voter.
- **Agents macro, dérivés et flux** : annonces économiques, levier des
  contrats à terme, argent qui entre ou sort par les stablecoins. Ils
  informent le comité ; ils ne gagneront un poids de vote qu'après une étude
  qui prouve leur utilité.
- **Pistes testées par le bot** : chaque semaine, la question de chaque thème
  est posée à ses données (`docs/BIBLIOTHEQUE.md`). Premier verdict : la règle
  actuelle tient partout.
- **Étude des combinaisons** (`docs/COMBINAISONS.md`) : TrendGuard avec les
  Tortues ou la cassure de Bollinger fait mieux depuis 2023, moins bien sur
  2018-2022 ; la règle fixée d'avance n'en retient aucune.
- **Levier étudié par le bot** (`docs/LEVIER.md`) : chaque semaine, il rejoue
  sa stratégie avec un levier fixe ou adaptatif, emprunt compté. Sa règle n'en
  permet aucun : sans levier, la baisse depuis 2023 atteint déjà −34 % ; à
  1,25×, l'arrêt d'urgence (−40 %) aurait été touché. Jamais appliqué : le bot
  trade au comptant.
- **Erreurs des sources** : Reddit n'est plus présenté comme une erreur de
  modèle d'IA ; le refus des lectures sans compte est dit tel quel.
- **Environnement** : aucune fenêtre de console vide, journal en direct,
  Python 3.12 indépendant de tout autre logiciel.

## À faire de votre côté

- **Démarrage avec Windows** : `python trendguard_bot.py autostart on`.
- **Heure de Windows**, dans un PowerShell administrateur : `Set-Service
  W32Time -StartupType Automatic; Start-Service W32Time; w32tm /resync`.
- **Conseil des IA** : enregistrer au moins une clé d'IA (tâche VS Code «
  Veille IA — enregistrer une clé d'IA »). Plusieurs IA proposent un accès
  gratuit limité.

## Ce que le bot ne fera pas, et pourquoi

- **Lire le dark web.** On y trouve des marchés illégaux, des arnaques de type
  « pump and dump » et des logiciels malveillants, pas de savoir financier
  fiable. Y connecter le bot exposerait le PC et les clés Binance. Le « web
  profond » utile est légal : bases de recherche (arXiv), données publiques
  des marchés et des banques centrales.
- **Réécrire seul son code ou ses règles.** Un bot qui modifie son propre code
  pourrait retirer ses stops ou ses verrous du réel. Il propose et éprouve :
  l'évolution encadrée règle certains paramètres sous épreuves
  (`docs/EVOLUTION.md`), le bot libre essaie tout en argent fictif, et toute règle
  nouvelle passe par une étude reproductible jugée hors échantillon.
- **Agir sur une information non prouvée.** Un texte lu, même d'une IA, est
  une donnée, jamais un ordre.
