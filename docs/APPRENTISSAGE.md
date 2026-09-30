# TrendGuard — apprentissage libre, règles encadrées

Depuis le 29 septembre 2026, le bot suit une charte en deux parties :

- **Libre, rapide et précis** : apprendre, s'adapter, ruser, s'informer. Chaque
  relevé est appris et appliqué aussitôt, sans attendre ni demander. Code :
  `trendguard/learning.py`.
- **Encadré avec sagesse** : changer une règle (cassure, stops, lecture du
  marché). Épreuves, essai de 30 jours, niveaux : voir
  [`EVOLUTION.md`](EVOLUTION.md). Le risque, lui, ne change jamais seul.

## Ce qui est libre

| Domaine | Ce que le bot apprend | Ce qu'il en fait aussitôt |
| --- | --- | --- |
| Carnets d'ordres | l'écart achat/vente et la profondeur normaux de chaque crypto, relevés toutes les 10 minutes le temps de les apprendre (quelques heures), puis toutes les heures, et à chaque achat | la ruse diffère un achat dès qu'un carnet s'écarte de SA normale : écart de plus de 3 fois la normale (au moins 0,2 %), ou carnet vidé à moins du quart de sa profondeur habituelle |
| Prévisions | chaque probabilité annoncée (vente, achat, marché baissier), relevée 12, 6, 3 et 1 heure avant la clôture, comparée à ce qui s'est passé | les probabilités suivantes sont corrigées par cette expérience : panneau, alertes d'anticipation, Rachelle |
| Ruse | le bilan des achats différés : achetés plus tard et à quel prix, ou abandonnés | affiché dans Réglages ▸ Autonomie |
| Veille | les annonces officielles de Binance (retraits de cryptos), relues toutes les heures au lieu d'une fois par jour | achats bloqués dans l'heure ; alerte si la crypto est détenue |

**Précis** : la normale d'un carnet part de la médiane des 24 premiers relevés,
puis suit une moyenne glissante où un relevé aberrant (krach éclair) pèse au
plus comme 5 fois la normale. La correction des prévisions attend au moins 8
comparaisons par tranche de probabilité, et reste tempérée par le modèle tant
que l'expérience est mince. **Rapide** : l'expérience récente pèse plus (1 % de
poids en moins par soir pour l'ancienne). L'erreur des prévisions (score de
Brier) est mesurée avant et après correction, pour vérifier que l'apprentissage
aide vraiment.

## Ce que l'apprentissage ne peut pas faire

- **Rendre le bot moins prudent** : un seuil appris n'est jamais plus large que
  le seuil fixe (écart de 0,5 %, carnet de 3 fois le montant de l'achat). La
  ruse apprise ne peut que différer un achat, jamais en forcer un.
- **Avancer une décision** : les probabilités corrigées préparent la clôture,
  elles ne décident rien. C'est la clôture de 00:00 UTC qui décide, avec les
  règles.
- **Changer une règle ou le risque** : cassure, stops, lecture du marché et
  palier de risque (1 à 2 % par achat) ne changent que par l'évolution
  encadrée ; nombre de positions, arrêt d'urgence et passage en réel restent
  hors de portée.
- **Bloquer le trading** : un relevé impossible (réseau, carnet illisible) est
  simplement sauté ; le relevé horaire des carnets dure au plus 20 secondes.

## Suivre ce qu'il apprend

- Panneau : Réglages ▸ Autonomie, ligne « Apprentissage libre » ; la carte
  Anticipation indique quand ses probabilités sont corrigées par l'expérience.
- Rachelle répond à « Le bot apprend-il ? » et à « Le bot peut-il évoluer ? ».
- Journal du bot : lignes `[APPRENTISSAGE]` (prévisions comparées à chaque
  clôture) et `[RUSE]`.
