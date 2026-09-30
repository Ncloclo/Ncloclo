# TrendGuard — évolution encadrée

Depuis le 29 septembre 2026, le bot a le droit de modifier lui-même certains de
ses réglages. Il gagne en indépendance à mesure qu'il réussit des épreuves de
plus en plus difficiles. Activée par défaut (`TG_EVOLUTION=true` ;
`TG_EVOLUTION=false` dans `.env` pour garder des réglages fixes). Code :
`trendguard/evolution.py`.

C'est la partie encadrée de la charte du bot : apprendre, s'adapter, ruser et
s'informer sont libres, rapides et précis
([`APPRENTISSAGE.md`](APPRENTISSAGE.md)) ; changer une règle passe par les
épreuves et la sagesse décrites ici.

## Ce qu'il peut changer, et ce qui reste hors de sa portée

| Réglage | Valeurs permises | D'origine |
| --- | --- | --- |
| Cassure : plus haut de N jours | 20, 25, 30, 35, 40, 50 | 30 |
| Stop initial (× volatilité) | 2 ; 2,5 ; 3 ; 3,5 ; 4 | 3 |
| Stop suiveur (× volatilité) | 3, 4, 5, 6, 7 | 5 |
| Stop en marché baissier (× volatilité ; 0 = non resserré) | 0 ; 1,5 ; 2 ; 2,5 ; 3 | 2 |
| Lecture du marché : moyenne de BTC (jours) | 100, 125, 150, 175, 200 | 150 |

Depuis le 30 septembre 2026, il peut aussi régler son **palier de risque** : de
1 % à 2 % par achat, un cran de 0,25 % à la fois (section suivante).

**Hors de sa portée, à tout niveau** : tout risque par achat au-delà de 2 % (ou
de `TG_RISK_MAX_PCT`), nombre de positions (8), arrêt d'urgence (−40 %),
filtres de liquidité et d'ancienneté, frais, profil prudent, sélection des
cryptos, passage en réel, clés API. Même un fichier d'état modifié à la main ne
peut rien forcer d'autre : seules les valeurs du tableau et les paliers permis
sont acceptés.

## Le palier de risque : de 1 % à 2 % par achat

Le risque par achat part de `TG_RISK_PCT` (1 %) et peut monter jusqu'au double,
jamais au-delà de 2 % ni de `TG_RISK_MAX_PCT` (`0.01` : jamais plus de 1 %).
Le risque cumulé monte d'autant : le nombre de positions ne change pas, chacune
est un peu plus grosse.

**Monter d'un cran** (+0,25 %), seulement si tout est réuni :

- marché haussier, capital à moins de 5 % de son plus haut, aucun réglage ni
  palier à l'essai ;
- trois épreuves face au palier actuel : **deux époques** (Calmar au moins égal
  et rendement supérieur, sur 2018-2022 et depuis 2023), **pire baisse**
  rejouée depuis 2018 d'au plus 30 %, **hasard** : pire baisse atteinte 1 fois
  sur 20 en trois ans d'au plus 35 %, 5 points sous l'arrêt d'urgence ;
- puis 30 jours d'essai sur le vrai marché, jugés comme ceux des réglages.

**Redescendre** :

- aussitôt au premier palier, à la décision même : baisse de 10 % depuis le
  plus haut, marché baissier, arrêt d'urgence ou reprise en douceur ; 60 jours
  de repos après une alerte ;
- d'un cran quand l'analyse de la nuit ne justifie plus le palier (30 jours de
  repos), ou après un essai raté (60 jours).

**Premier examen** (30 septembre 2026) : 1,25 % réussit les deux époques et la
pire baisse (−28 %), mais pas le hasard (−39 % pour une limite de −35 %). Le
bot garde 1 %. Rejoué pas à pas de 2020 à 2026, ce palier ne serait jamais
monté ; sans garde-fou, il aurait fait moins bien depuis 2023
([`ADAPTATION.md`](ADAPTATION.md), section 6).

## La routine de chaque jour

Après la décision de 00:02 UTC, le bot lance la routine dans un processus
séparé : la surveillance des stops n'est jamais ralentie.

1. **Tempête** (arrêt d'urgence, ou capital à plus de 15 % sous son plus haut) :
   aucun changement ce jour-là.
2. **Réglage en essai** : au bout de 30 jours, il est jugé sur ce qui s'est
   vraiment passé depuis son adoption, comparé à ce qu'auraient fait les anciens
   réglages. S'il n'a pas fait moins bien (2 points de tolérance), il est
   confirmé et le bot gagne de l'expérience. Sinon, retour aux anciens réglages
   et un niveau de moins.
3. **Repos** après un changement : 7 jours après un essai réussi, 60 jours après
   un essai raté.
4. **Recherche** : les réglages permis par le niveau, du plus simple au plus
   grand, passent les épreuves. Le premier qui les réussit toutes est adopté à
   la décision suivante, jamais en cours de journée, avec une alerte qui
   explique le choix.

## Les niveaux : plus de liberté, épreuves plus dures

| Niveau | Liberté | Amélioration exigée (Calmar) | Perte en plus tolérée dans une crise | Pire baisse en plus tolérée (hasard) | Pour monter |
| --- | --- | --- | --- | --- | --- |
| 1. Apprenti | 1 réglage, d'un cran | +5 % | 1 point | 2 points | 1 réglage confirmé |
| 2. Compagnon | 1 réglage, jusqu'à 2 crans | +7 % | 0,5 point | 1,5 point | 2 réglages confirmés |
| 3. Expert | 2 réglages à la fois, d'un cran | +10 % | 0,25 point | 1 point | 3 réglages confirmés |
| 4. Maître | 2 réglages à la fois, jusqu'à 2 crans | +12 % | aucune | 0,5 point | niveau le plus haut |

Un essai raté coûte un niveau.

## Les épreuves (toutes, à chaque niveau)

1. **Deux époques** : faire mieux, de l'amélioration exigée par le niveau, sur
   2018-2022 **et** depuis 2023, en gardant au moins 80 % du rendement. Calmar :
   rendement annuel divisé par la pire baisse.
2. **Frais doublés** : l'avantage doit survivre à une exécution deux fois plus
   chère.
3. **Énigmes des crises** : ne pas perdre plus que les réglages actuels
   (tolérance du niveau) pendant le marché baissier de 2018, le krach du Covid
   (mars 2020), la chute de mai 2021, l'effondrement de LUNA (mai 2022) et la
   faillite de FTX (novembre 2022) ; garder au moins 90 % des hausses de
   2020-2021 et de 2023-2024.
4. **Plateau** : les réglages voisins du candidat gardent au moins 90 % du
   Calmar actuel. Sinon, c'est un pic trouvé par hasard.
5. **Hasard** : trois ans rejoués 1 000 fois par blocs de 30 jours ; pas de pire
   baisse (1 fois sur 20) plus forte que la tolérance du niveau, et au moins 95
   % du résultat médian.

## Les règles de sagesse

1. Dans le doute, ne rien changer : un nouveau réglage doit faire nettement
   mieux, pas de justesse.
2. Un seul changement à la fois, puis 30 jours d'essai sur le vrai marché.
3. Le risque ne monte que par petits paliers, de 1 % à 2 % par achat au plus,
   quand l'analyse le justifie, et redescend aussitôt à la première alerte ;
   nombre de positions, arrêt d'urgence et passage en réel restent hors de
   portée.
4. Un plateau, pas un pic : les réglages voisins doivent aussi tenir.
5. Les crises passées sont des énigmes : ne pas y perdre plus que les réglages
   actuels.
6. Revenir en arrière sans honte : un essai raté est annulé et coûte un niveau.
7. On ne change pas de cap dans la tempête : aucun changement pendant une forte
   baisse ou un arrêt d'urgence.
8. Le plus simple d'abord : le plus petit changement qui réussit toutes les
   épreuves.
9. Expliquer chaque choix en mots simples.

## Pourquoi tant d'exigence

Sur l'historique Binance, les bots qui changent leurs règles d'après leurs
résultats récents font en moyenne moins bien sur les années qu'ils n'ont pas
vues ([`ADAPTATION.md`](ADAPTATION.md), [`STRATEGIES.md`](STRATEGIES.md)). Les
épreuves sont là pour que le bot n'adopte que ce qui tient partout : le plus
souvent, la sagesse sera de ne rien changer.

**Premier examen (29 septembre 2026, niveau Apprenti)** : 10 réglages essayés,
aucun ne réussit toutes les épreuves ; le bot garde les siens. Le plus proche :
un stop un peu plus serré en marché baissier (1,5 au lieu de 2), meilleur sur
2018-2022 mais pas assez depuis 2023.

## Suivre et reprendre la main

- Panneau : Réglages ▸ Autonomie (ligne « Évolution encadrée ») et centre de
  sécurité ; Rachelle répond à « Le bot peut-il évoluer ? ».
- `python trendguard_bot.py evolution` : niveau, réglages changés, palier de
  risque, historique et règles.
- `python trendguard_bot.py evolution examen` : les épreuves du jour, réglages
  et palier de risque, sans rien changer.
- `python trendguard_bot.py evolution revenir` : retour aux réglages d'origine
  et à 1 % par achat (niveau 1, 30 jours de repos).
- Panneau : Réglages ▸ Bot (ligne « Palier de risque », dernière analyse dans
  « Détails ») ; le raisonnement du jour dit quand le palier dépasse 1 %.
- `TG_EVOLUTION=false` dans `.env`, puis ARRÊTER et AUTO : réglages fixes.
- Journal : `trendguard_paper.evolution.log` ; état :
  `trendguard_paper.evolution.json`.
