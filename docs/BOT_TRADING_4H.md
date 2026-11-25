# L'ancien bot de trading en 4 h (bot-trading), réuni dans TrendGuard

Avant TrendGuard, un premier bot a été écrit à part, dans le dossier
`python/bot-trading` : une seule paire (BTC/USDT), des bougies de 4 heures,
trois stratégies, un tableau de bord web et un portefeuille fictif. Il faisait
en plus petit ce que TrendGuard fait déjà (stratégie, test sur le passé,
portefeuille fictif, tableau de bord) ; les deux projets ont été réunis le
25/11/2026 : TrendGuard reste le seul projet, et ce document garde ce que
l'ancien a appris.

## Ce qu'il faisait

- Prix publics de Binance, une paire (BTC/USDT par défaut), bougies de 4 h.
- Trois stratégies : `prudente` (tendance et RSI), `croisement` (moyennes
  mobiles), `tendance` (ignore les marchés sans direction).
- Risque : 1 % du capital par position, stop réglé sur la volatilité (3 ×
  ATR, 1 % au moins) qui remonte avec le prix, arrêt des achats après 10 % de
  baisse du portefeuille ou 3 % de perte dans la journée.
- Test sur le passé jusqu'à 20 000 bougies, toujours comparé au rendement
  obtenu sans rien faire ; comparaison des stratégies sur deux moitiés d'un
  long historique.
- Simulation seulement : aucun ordre réel, aucune clé d'API.

## Ce qu'il a mesuré

Évaluation du 12 novembre 2026 sur BTC/USDT, frais de 0,1 % par ordre inclus :

| Cadence | Période | Meilleure stratégie | Résultat | Sans rien faire |
| --- | --- | --- | --- | --- |
| 5 min à 1 h | 70 jours à 2 ans | aucune | −1 % à −10 % | variable |
| 4 h | 9 ans | `tendance` | +44 % (+54 % puis +9 %) | +1165 % |
| 4 h | 9 ans | `croisement` | +28 % (+40 % puis +7 %) | +1165 % |
| 4 h | 9 ans | `prudente` | +25 % (+30 % puis −3 %) | +1165 % |
| 1 jour | 9 ans | les trois | +9 % à +13 % | +779 % |

Environ 4 % par an au mieux, avec une baisse maximale d'environ 10 % : très
loin de ce qu'a rapporté le simple fait de garder du bitcoin. Une seule paire,
sur une période où le bitcoin a été multiplié par douze ; réglages du risque
modifiés une fois après une première évaluation, donc un biais de sélection
reste possible. Ces chiffres ne garantissent rien.

## Ce qu'il a appris (et que TrendGuard applique)

- Le poids des frais vaut (frais aller-retour, 0,2 %) / (distance du stop).
  Avec des stops serrés (0,4 %), les frais mangeaient la moitié du risque et
  toutes les stratégies perdaient environ 10 %, à toutes les cadences.
- Des stops larges, réglés sur la volatilité, et un stop qui ne fait que
  monter, sans objectif fixe, rendent les stratégies de tendance positives.
- En dessous de 4 h, les frais dépassent les gains : TrendGuard décide une
  fois par jour, sur la bougie journalière clôturée.
- Une stratégie ne se juge que face au rendement « sans rien faire », sur
  deux moitiés distinctes de l'historique ; on ne règle pas les paramètres
  jusqu'à obtenir un bon chiffre sur une seule période.

## Son code et son historique

Le code n'est plus dans les dossiers du projet, pour ne pas garder deux bots
qui font la même chose. Son historique Git complet (quatre commits, du 12 au
23 novembre 2026) est gardé tel quel dans
[`archives/bot-trading.bundle`](archives/bot-trading.bundle) :

```bash
git clone docs/archives/bot-trading.bundle bot-trading
```

Défauts connus de sa dernière version, jamais corrigés (relecture du
25/11/2026), à reprendre si on le ranime :

- le stop relevé par le stop suiveur n'était enregistré qu'à un achat, une
  vente ou un arrêt propre : après un plantage, l'ancien stop revenait ;
- le test sur le passé relevait le stop sur le plus haut de chaque bougie, le
  bot réel sur le cours lu chaque minute ;
- le test sur le passé n'appliquait pas l'arrêt après 3 % de perte du jour ;
- un test sur le passé lancé sans stratégie prenait `prudente` au lieu de
  celle du bot (`tendance`).
