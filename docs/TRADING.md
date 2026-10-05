# Ce que le bot sait du trading

Votre demande (1er octobre) : que le bot fasse des recherches sur les
meilleurs traders, leurs stratégies, l'organisation des plateformes de
trading et tout ce qui touche au trading. Ce document rassemble ce savoir ;
les stratégies publiées des traders célèbres sont aussi **testées** par le
bot sur ses propres données, dans son tournoi ([`STRATEGIES.md`](STRATEGIES.md)).

## Les grands traders, et ce que le bot en a appris

| Trader | Ce qu'il a fait | Ce que le bot en retient |
| --- | --- | --- |
| Richard Dennis et William Eckhardt | En 1983-1984, ils forment une vingtaine de débutants (les « Tortues ») à des règles écrites : acheter la cassure du plus haut de 20 ou 55 jours, sortir sous le plus bas de 10 ou 20 jours, stop à 2 fois la volatilité, taille réglée sur la volatilité. Plusieurs Tortues ont ensuite géré des fonds | des règles simples, suivies sans émotion, battent l'intuition. TrendGuard en est un descendant direct : cassure, stop selon la volatilité, 1 % de risque par achat |
| Ed Seykota | Pionnier du suivi de tendance par ordinateur dans les années 1970 (cité dans *Market Wizards*, Jack Schwager, 1989) | « couper les pertes, laisser courir les gains » ; le risque d'abord |
| Paul Tudor Jones | Gérant célèbre pour sa gestion du risque ; il dit juger tout marché à sa moyenne des 200 derniers jours | un filtre de marché : TrendGuard n'achète que si BTC est au-dessus de sa moyenne de 150 jours |
| John W. Henry | Gérant de fonds de tendance pendant 30 ans ; sa société ferme en 2012 après plusieurs années de pertes | même une bonne méthode traverse de longues baisses : d'où l'arrêt d'urgence et le profil prudent |
| Larry Connors | Stratégies de retour à la moyenne à court terme (RSI sur 2 jours) | un taux de réussite élevé ne fait pas un bénéfice : testé par le bot, 61 à 69 % de trades gagnants, mais presque rien gagné |
| John Bollinger | Inventeur des bandes de Bollinger (années 1980) | testées en cassure : brillantes depuis 2023, médiocres de 2018 à 2022 ; une stratégie se juge sur plusieurs périodes |
| Gary Antonacci | Double momentum (relatif et absolu, livre de 2014) | testé : moins bon que TrendGuard sur les deux périodes |
| Jesse Livermore | Spéculateur du début du XXe siècle (*Reminiscences of a Stock Operator*, 1923), ruiné plusieurs fois | même les meilleurs se ruinent sans limite de risque |

Deux preuves à garder en tête :

- **La recherche confirme le suivi de tendance.** La persistance des
  tendances est l'un des effets les mieux documentés de la finance
  (Moskowitz, Ooi et Pedersen, 2012 ; Hurst, Ooi et Pedersen, *A Century of
  Evidence on Trend-Following Investing*, 2017), y compris sur les cryptos.
- **On ne connaît que les survivants.** Pour un trader célèbre, des milliers
  ont fait pareil et ont perdu. Copier un trader parce qu'il a gagné, c'est
  oublier les autres : le bot teste les méthodes sur 8 ans de cours au lieu
  de croire les histoires.

## Les stratégies des traders, testées par le bot

Cinq stratégies publiées sont ajoutées au tournoi du bot, écrites telles que
leurs auteurs les ont décrites, avec 1 % de risque par achat comme les
autres. Résultat (Binance, 21 cryptos, choix sur 2018-2022 puis vérification
depuis 2023) : **aucune ne fait mieux que TrendGuard sur les deux périodes**.
Les Tortues gagnent plus, mais avec des baisses de 36 à 58 % ; la cassure de
Bollinger brille depuis 2023 après avoir déçu avant. Le bot les classe chaque
semaine dans son diagnostic, à titre d'information : changer de stratégie
selon le classement du moment a fait moins bien que de s'y tenir.

## Comment fonctionne une plateforme comme Binance

| Élément | Ce que c'est | Ce que le bot en fait |
| --- | --- | --- |
| Carnet d'ordres | la liste des offres d'achat et de vente en attente, classées par prix puis par ordre d'arrivée ; le moteur d'appariement exécute un ordre dès qu'une offre opposée lui correspond | avant un achat, il vérifie l'écart entre achat et vente et la profondeur du carnet (la « ruse ») |
| Ordres | au marché (immédiat, au prix disponible), à cours limité (à un prix choisi, ou pas du tout), stop (déclenché quand le cours touche un seuil) | achats au cours du moment, stop de secours posé chez Binance en réel |
| Frais | environ 0,1 % par ordre sur le Spot, moins avec des BNB ou un gros volume ; « maker » ajoute une offre au carnet, « taker » en prend une | 0,1 % compté à chaque achat et vente, plus 0,1 % de glissement |
| Liquidité et glissement | peu d'offres : le prix payé s'éloigne du prix affiché | aucune crypto échangée à moins de 5 millions de dollars par jour |
| Règles de chaque paire | taille minimale d'un ordre (5 USDT), pas de quantité et de prix | quantités et prix arrondis à ces pas, vérifiés par `verify` |
| Garde des fonds | la plateforme détient votre argent : si elle s'effondre (FTX, novembre 2022), il peut disparaître | ne laisser sur Binance que le capital confié au bot |
| Clés d'accès (API) | une clé et son secret, avec des droits choisis ; une liste d'adresses Internet autorisées | retrait interdit, trading Spot seulement, adresse autorisée ; clés jamais montrées ni publiées |
| Limites de l'API | un nombre de requêtes par minute ; une heure exacte exigée à chaque ordre signé | le bot se cale sur l'heure de Binance et ménage ses requêtes |
| Retraits de la cote | Binance annonce les cryptos qu'elle va retirer | annonces lues toutes les heures : plus aucun achat d'une crypto retirée |

## Ce que le bot ne fera pas

- Pas de levier, pas de vente à découvert, pas de contrats à terme : sur le
  Spot, on ne perd au pire que ce qu'on a acheté.
- Pas de stratégie copiée sur la foi d'une réputation : seulement ce qui
  passe ses épreuves sur 8 ans de cours ([`EVOLUTION.md`](EVOLUTION.md)).
