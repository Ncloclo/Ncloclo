# La BRVM dans le bot

La BRVM (Bourse régionale des valeurs mobilières, à Abidjan) est la bourse
commune des huit pays de l'UEMOA : Bénin, Burkina Faso, Côte d'Ivoire,
Guinée-Bissau, Mali, Niger, Sénégal et Togo. MSCI la classe parmi les marchés
frontières (« WAEMU »). Module : `trendguard/brvm.py`.

## Ce que le bot lit

- Les deux pages publiques de brvm.org : les indices
  (`/fr/indices` : BRVM Composite, BRVM 30, Prestige, Principal, indices
  sectoriels, indice dividendes réinvestis, et l'activité du marché) et le
  cours de chaque action cotée (`/fr/cours-actions/0` : volume, cours de la
  veille, d'ouverture et de clôture, variation).
- Une fois par séance, après la clôture (16:30 UTC, heure d'Abidjan), au sein
  de la lecture d'Internet du noyau de savoir.
- Le robots.txt de la BRVM permet ces pages et demande 10 secondes entre deux
  lectures : le bot en lit deux, à 11 secondes d'écart, sous son nom
  (« TrendGuard veille ; lecture seule »). Il ne se connecte à aucun compte et
  ne lit pas le flux en temps réel, qui est un service à part.

## Ce que le bot en garde

- La BRVM ne publie pas d'historique téléchargeable. Le bot construit le
  sien : une ligne par séance et par titre dans `data_brvm/` (date, clôture,
  volume), et la dernière séance lue dans le noyau de savoir (clé « brvm »).
- Un jour férié, la page ne change pas : aucune séance n'est ajoutée.

## Ce qu'il en fait

- Page « BRVM » du panneau (menu Marchés) : indices, activité du marché, et
  toutes les actions cotées, avec filtres et recherche.
- Page « Pays du monde » : les huit pays de l'UEMOA montrent le BRVM
  Composite.
- Sa règle de tendance s'appliquera quand l'historique sera assez long : 150
  séances pour la moyenne, 250 pour un signal d'achat. Une étude, déclarée
  d'avance comme les autres, dira alors si elle tient sur la BRVM.
- Information seulement : aucun ordre réel. Il faudrait passer par une SGI
  (société de gestion et d'intermédiation) agréée.
