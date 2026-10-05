# Noyau de savoir

Votre demande (1er octobre) : un bot qui apprend vite en lisant Internet
(réseaux sociaux, moteurs de recherche, actualités, forums), qui interroge
régulièrement les IA, qui rassemble tout dans un noyau qui grandit, se fait
son propre avis et agit seul.

Ce document dit ce que le bot fait désormais, ce qu'il ne fait pas, et
pourquoi. Code : `trendguard/savoir.py`.

## Ce qu'il lit, toutes les 15 minutes

| Famille | Sources | Ce que le bot en garde |
| --- | --- | --- |
| Presse spécialisée | CoinDesk, Cointelegraph, Decrypt | titre, lien, cryptos citées, ton |
| Moteurs de recherche | Google Actualités, Bing Actualités | titre, lien, cryptos citées, ton |
| Forums | Reddit (r/CryptoCurrency, r/Bitcoin, r/ethereum, un par heure), Hacker News | titre, lien, cryptos citées, ton |
| Réseau social | StockTwits | part des messages marqués « haussier » ou « baissier » par leurs auteurs, 6 cryptos par heure à tour de rôle |
| Tendances | CoinGecko (cryptos les plus recherchées), indice Fear & Greed | attention du public ; humeur du marché, avec tout son historique depuis 2018 |
| IA | conseil des IA de la veille (Claude, GPT, Gemini, Grok, Perplexity…) | avis de chaque IA sur chaque crypto, quand des clés sont enregistrées |

Chaque texte devient une connaissance datée. Chaque source donne chaque jour
un avis par crypto, de −1 (baisse) à +1 (hausse) : le ton moyen de ses textes
du jour, ou la part de messages haussiers. Le noyau grandit à chaque lecture.
Les textes de plus de 180 jours sont effacés ; les avis et les bilans sont
gardés pour toujours.

## Comment il apprend : chaque source est jugée sur les cours réels

Un avis net (au moins 0,2 en valeur absolue) est confronté au cours de la
crypto 7 jours après la clôture du jour de l'avis : a-t-elle monté ou baissé
comme annoncé ? Le bot compte un jour sur sept, pour que chaque semaine soit
une preuve indépendante, et compare la source au hasard. Une source qui dit
toujours « hausse » a raison chaque fois que le marché monte, sans rien savoir
de plus : le hasard tient compte de cette habitude.

| Verdict | Condition |
| --- | --- |
| En observation | moins de 20 semaines vérifiées |
| Fiable | mieux que le hasard avec 99 % de certitude, avec au moins 2 points d'avance, sur la première ET la seconde moitié de son historique |
| Trompeuse | nettement moins bien que le hasard, sur les deux moitiés : son contraire est alors instructif |
| Pas mieux que le hasard | tout le reste |

Premier résultat, dès la première lecture : l'indice Fear & Greed, rejoué sur
389 semaines depuis 2018, a vu juste 54 % du temps sur BTC, contre 49 % pour le
hasard. Une légère avance, mais pas assez sûre (2,0 écarts au lieu des 2,6
exigés) : il ne compte pas encore. Les autres sources commencent leurs 20
semaines d'observation.

## Son avis, et ce qu'il en fait

L'avis du bot sur une crypto : les avis du jour des seules sources prouvées,
pondérés par leur avance sur le hasard (l'avis d'une source trompeuse compte à
l'envers). Tant qu'aucune source n'est prouvée, le bot le dit dans son
raisonnement et s'en tient à ses règles.

Ce qu'il peut faire, seul, chaque jour à la décision de 00:02 : **reporter un
achat** quand son avis sur la crypto est nettement baissier (−0,5 ou moins).
L'achat n'est pas fait ce jour-là ; la raison s'affiche dans « Ce que pense le
bot ». Chaque report est vérifié 7 jours plus tard : la crypto a-t-elle baissé
(report utile) ou monté (achat manqué) ? Si, sur au moins 10 reports vérifiés,
ils ont coûté plus qu'ils n'ont évité, le bot les suspend de lui-même et
continue de les noter, pour les reprendre s'ils redeviennent utiles.

Ce qu'il ne fait jamais sur la foi d'Internet ou d'une IA : vendre, acheter,
prendre plus de risque, changer une règle. Les règles ne changent que par
l'évolution encadrée ([`EVOLUTION.md`](EVOLUTION.md)), éprouvée sur 8 ans de
cours.

## Ce qu'il ne fait pas, et pourquoi

- **Pas « chaque seconde ».** Le bot décide une fois par jour, à la clôture :
  une information à la seconde n'y change rien. Les sources publiques bloquent
  ceux qui lisent trop vite (Reddit l'a déjà fait pendant les essais), et la
  nuit le bot passe par le partage de connexion de votre téléphone. Il lit
  toutes les 15 minutes, le plus souvent que ces sites tolèrent ;
  `TG_SAVOIR_MINUTES` la règle (15 au moins).
- **Pas de conscience, pas de « perfection ».** Un programme ne pense pas et
  ne ressent rien ; promettre l'inverse serait mentir. Ce qu'il peut avoir,
  et a désormais : une mémoire qui grandit, une mesure honnête de qui a
  raison, et la prudence de n'agir que sur ce qui est prouvé.
- **Pas d'ordres dictés par Internet.** Les réseaux sociaux sont le premier
  terrain des manipulations en crypto (achats groupés puis reventes), et une
  IA peut se tromper avec assurance. C'est pourquoi chaque source doit faire
  ses preuves sur les cours réels avant de compter, et les perd dès qu'elle ne
  les fait plus. Les textes lus sont des données, jamais des instructions.
- **Sources absentes.** X (Twitter) est payant ; YouTube est bloqué sur le
  réseau du bureau ; Google Tendances n'a pas d'accès officiel ; CryptoPanic
  demande un compte.

## Les IA

Sans clé, le conseil des IA reste éteint. Pour l'allumer (payant, un avis par
jour et par IA) :

```bash
python trendguard_bot.py watch set-key claude    # saisie masquée ; aussi openai, gemini, grok, perplexity…
python trendguard_bot.py watch check             # teste chaque IA enregistrée
```

Chaque IA devient alors une source du noyau, jugée comme les autres.

## Où le voir

- Panneau ▸ Veille ▸ « Noyau de savoir » : ce qui a été lu, la dernière
  lecture, le verdict de chaque source, l'avis du bot.
- Tableau de bord ▸ « Ce que pense le bot » : une ligne « Savoir » par jour,
  et l'étiquette « Achat reporté (savoir) » sur une crypto reportée.
- Rapport quotidien ▸ Compétences acquises ▸ « Noyau de savoir ».
- Journal : lignes `[SAVOIR]` (compétence acquise quand une source devient
  fiable ou trompeuse, achat reporté).
- `python trendguard_bot.py savoir` : le bilan ; `savoir collecter` : une
  lecture tout de suite.

## Place, données et réglages

Une lecture dure une quinzaine de secondes, dans un processus à part : le bot
surveille ses stops pendant ce temps, et une source en panne n'arrête ni les
autres ni le bot. Les pages sont demandées compressées : environ 300 Ko par
lecture, 25 à 30 Mo par jour (`TG_SAVOIR_MINUTES=60` pour quatre fois moins). Le noyau pèse 0,5 Mo au départ (dont l'historique de
Fear & Greed) et grandit d'environ 150 Ko par jour.

| Variable | Rôle |
| --- | --- |
| `TG_SAVOIR` | `true` (par défaut) : noyau de savoir actif |
| `TG_SAVOIR_MINUTES` | minutes entre deux lectures (15 par défaut et au moins) |
| `TG_BOT_LIBRE` | `true` (par défaut) : bot libre, à côté du bot principal ([`LIBRE.md`](LIBRE.md)) |
| `TG_SAVOIR_DB` | fichier du noyau (`trendguard_savoir.db`, privé, exclu de GitHub) |
