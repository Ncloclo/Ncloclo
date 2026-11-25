# Votre profil : un bot pour tous les pays

Le bot parle de votre situation, où que vous soyez. Trois réglages, dans le
fichier `.env` de ce PC (rien n'est envoyé à l'extérieur). Module :
`trendguard/profil.py`.

| Réglage | Rôle | Par défaut |
| --- | --- | --- |
| `TG_PAYS` | votre pays, code ISO à deux lettres (CI, FR, NG, US…) | CI (Côte d'Ivoire) |
| `TG_FUSEAU` | votre fuseau horaire (Africa/Lagos, Europe/Paris…) | celui de votre pays |
| `TG_DEVISE` | la monnaie d'affichage du capital | celle de votre pays |

## Ce qui change selon votre pays

- Les bourses : la page « Bourses du monde » dit comment y accéder depuis
  votre pays. Pour une bourse de votre pays ou de votre zone (la BRVM pour
  l'UEMOA, la BVMAC pour la CEMAC) : un intermédiaire agréé localement. Pour
  une bourse étrangère : un courtier international régulé. On peut y choisir
  un autre pays, sans toucher au bot.
- Les heures : chaque ouverture et chaque clôture s'écrit dans l'heure de la
  place et dans la vôtre (dans le panneau, l'heure de l'appareil qui le
  regarde).
- Le guide pour investir : des points communs à tous les pays, puis ceux de
  votre zone (UEMOA, CEMAC, États-Unis…) et de votre pays.
- Les courtiers : leur accueil depuis votre pays (« à vérifier » sauf source,
  par exemple les CFD interdits aux particuliers aux États-Unis).
- Le capital : dans votre monnaie, parmi une soixantaine : l'euro (taux
  EUR/USDT de Binance, relu chaque heure), le franc CFA (parité fixe avec
  l'euro), les autres d'après leur cours face au dollar, lu chaque jour par
  le suivi des marchés.

## Ce qui ne change pas

La règle du bot, ses risques et ses verrous sont les mêmes partout : il
trade des cryptos sur Binance, en paper tant que vous ne décidez pas
autrement. Le profil ne change que ce qu'il vous dit, jamais ce qu'il fait.

Les 249 pays connus du bot viennent de la base des fuseaux horaires
(tzdata, sur le PC) : leur nom en français pour les pays suivis et pour
l'Afrique, en anglais pour les autres.
