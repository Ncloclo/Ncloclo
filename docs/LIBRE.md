# Bot libre

Votre demande (1er octobre, confirmée ensuite) : que le bot agisse de façon
autonome et libre sur ce qu'il apprend d'Internet et des IA, applique ses
choix, améliore ses propres règles et les applique, même ce que je jugeais
dangereux, sous votre responsabilité.

C'est fait, dans un **second portefeuille fictif**, à côté du bot principal,
avec le même capital que le paper. Code : `trendguard/libre.py`.

## Ce qu'il fait, chaque jour à la décision de 00:02

1. **Il apprend, vite.** Chaque source du noyau de savoir (presse, Google et
   Bing Actualités, Reddit, Hacker News, StockTwits, tendances CoinGecko,
   Fear & Greed, IA quand des clés sont enregistrées) gagne du poids quand son
   avis de la veille a vu juste sur le cours du jour, en perd sinon. Aucune
   période de preuve : il apprend dès le premier jour.
2. **Il se fait son propre avis** sur chaque crypto : les avis du jour de
   toutes les sources, pondérés par ce qu'il a appris d'elles.
3. **Il agit seul.** Il vend une crypto vue en baisse ou dont la perte
   atteint son stop, puis achète celles qu'il voit le plus en hausse : 20 %
   du capital par position, 5 positions au plus, frais et glissement comptés.
4. **Il améliore ses propres règles et les applique.** Chaque semaine, il
   rejoue ses avis passés avec d'autres seuils d'achat et de vente et
   d'autres stops, et adopte sur-le-champ ceux qui auraient le plus rapporté.

## Ce qui ne change pas

- Le bot principal garde ses règles éprouvées sur 8 ans : le bot libre ne
  touche ni à ses achats, ni à ses ventes, ni à son risque.
- Le bot libre ne joue jamais d'argent réel.

Les deux capitaux s'affichent côte à côte (panneau ▸ Veille ▸ Noyau de
savoir, « Ce que pense le bot », rapport quotidien) : vous jugerez sur pièces
laquelle des deux façons de faire gagne le plus. Pour l'arrêter :
`TG_BOT_LIBRE=false`.

## Ce qui n'est pas fait, et pourquoi

| Demande | Pourquoi non |
| --- | --- |
| Lire Internet chaque seconde | les sites bloquent ceux qui lisent trop vite (Reddit l'a fait pendant les essais) et leurs règles l'interdisent ; le bot lit désormais toutes les 15 minutes, le plus souvent qu'ils tolèrent |
| Web profond, dark web, proxy Tor | rien d'utile au trading et un vrai danger pour le PC qui garde vos clés : logiciels malveillants, contenus illégaux, arnaques |
| Fusion avec Kali Linux | Kali sert à tester la sécurité des systèmes informatiques, pas à trader ; rien à y gagner pour le bot |
| Entraîner son propre modèle d'IA | il faudrait des cartes graphiques et des téraoctets de données ; ce PC a 16 Go de mémoire et peu de place sur son disque. Le bot interroge plutôt les IA existantes (Claude, GPT, Gemini, DeepSeek, Grok…), dès que leurs clés sont enregistrées |
| Une conscience, une réflexion « parfaite » | un programme ne pense pas et ne ressent rien ; le promettre serait mentir. Il a une mémoire qui grandit et une mesure honnête de qui a raison |
