# Courtiers et plateformes

Où le bot pourrait jouer sa règle, et comment il s'y branche : en
démonstration seulement. Module : `trendguard/courtiers.py` ; page
« Courtiers » du panneau (menu Mon bot) ; commande
`python trendguard_bot.py courtiers`.

## Les règles

- Démonstration seulement : compte démo MetaTrader 5, compte papier Alpaca,
  testnet d'une plateforme crypto. Un compte réel (ou de concours) est refusé
  dès qu'il est reconnu, et rien d'autre n'est lu.
- Aucun ordre n'est envoyé. Le plan miroir dit, à blanc, ce que la règle
  tiendrait sur chaque compte. Les ordres de démonstration viendront après un
  premier essai réussi sur votre propre compte démo.
- Vous ouvrez vous-même vos comptes : le bot n'en crée aucun.
- Vos identifiants restent dans le fichier `.env` de ce PC : saisie masquée,
  compte vérifié avant d'être écrit. Le panneau ne regarde que leur présence,
  jamais leur valeur.
- Chaque requête vers une plateforme crypto est vérifiée avant de partir :
  une adresse absente de la liste blanche de `trendguard/cyber.py` est
  refusée. Branché à un testnet, le bot ne peut joindre que les adresses de ce
  testnet.

## Annuaire

- 15 courtiers forex et CFD sous MetaTrader 5, dont le serveur de
  démonstration de MetaQuotes, l'éditeur du logiciel : il suffit pour un
  premier essai, sans courtier.
- Alpaca : actions et fonds cotés américains. Compte papier gratuit, ouvert à
  tous avec une adresse e-mail d'après Alpaca ; il ne reçoit que les cours
  IEX.
- Les 104 plateformes crypto de la bibliothèque CCXT, dont 48 avec un testnet
  (adresse de test déclarée par CCXT). Frais : barème de base indiqué par
  CCXT.
- Les SGI de l'UEMOA pour la BRVM : aucune API pour un programme.
- Disponibilité depuis votre pays (`TG_PAYS`, `docs/PROFIL.md`) : rien n'est
  affirmé sans source. Exness, XM, HFM et FBS acceptent en général les
  résidents de Côte d'Ivoire, d'après des sources secondaires ; aux
  États-Unis, les CFD sont interdits aux particuliers ; Binance.com n'y
  accepte pas les résidents. Pour tous, vérifiez sur le site du courtier
  avant d'ouvrir un compte.

## Cours comparés à Binance

- Les cryptos du bot sur dix grandes plateformes (OKX, Bybit, KuCoin, Bitget,
  Gate, MEXC, HTX, Kraken, Coinbase, Crypto.com), lues en même temps que
  Binance toutes les six heures, par leurs cours publics, sans compte ni clé.
- Pour chaque crypto : écart de Binance à la médiane des autres plateformes,
  écart le plus grand, part de Binance dans le volume déclaré. Un écart de
  1 % ou plus est notable.
- Écartés : un cours à plus de 25 % de Binance (un autre jeton sous le même
  symbole) ; hors de la médiane, un marché de moins de 10 000 USDT échangés en
  24 heures.
- La liste des marchés de chaque plateforme est gardée une semaine dans
  `data_courtiers/` (environ 20 Mo) : chaque lecture ne télécharge que les
  cours.
- Information seulement : aucune décision du bot n'en dépend. Première
  lecture (25/11/2026) : onze plateformes lues, Binance à 0,02 % de la médiane
  des autres, aucun écart notable.

## Brancher un compte de démonstration

### MetaTrader 5 : devises, or, pétrole, indices

1. Installez MetaTrader 5 (site de MetaTrader 5 ou d'un courtier de
   l'annuaire) et ouvrez-y un compte démo.
2. Le paquet Python est déjà dans le `.venv` de ce PC ; ailleurs :
   `.venv\Scripts\python.exe -m pip install MetaTrader5`.
3. Tapez `python trendguard_bot.py courtiers set-key mt5` : numéro du compte,
   serveur, mot de passe (saisie masquée). Le compte est vérifié (démo exigée)
   avant d'être écrit.

Le bot lit le compte chaque heure quand le terminal est ouvert. Il ne l'ouvre
jamais lui-même ; seule la commande `courtiers verifier` peut le faire. Le
terminal MetaTrader 5 est un logiciel gratuit mais pas libre ; son paquet
Python est sous licence MIT.

### Alpaca : actions et fonds américains

1. Créez un compte papier sur alpaca.markets.
2. Prenez ses clés « Paper Trading ».
3. Tapez `python trendguard_bot.py courtiers set-key alpaca`.

Le bot ne connaît que l'adresse du compte papier (paper-api.alpaca.markets) :
celle du compte réel est refusée.

### Testnet d'une plateforme crypto

Bybit, OKX (compte de démonstration), Gate, Coinbase Exchange, Gemini et
Crypto.com : créez un compte de test et sa clé, puis tapez
`python trendguard_bot.py courtiers set-key bybit` (ou `okx`, `gate`,
`coinbaseexchange`, `gemini`, `cryptocom`). Binance : son testnet est celui du
bot lui-même (`python trendguard_bot.py set-keys`).

## Plan miroir, à blanc

Les positions du portefeuille fictif hors cryptos (`multi_actifs.py`),
recopiées au même poids sur chaque compte de démonstration branché :

- MetaTrader 5 : le symbole du courtier est retrouvé (« XAUUSDm »,
  « US500.cash »…) ; les lots sont arrondis au pas du courtier, la valeur d'un
  lot étant calculée par le terminal lui-même. Une position trop petite pour
  le minimum du courtier est signalée.
- Alpaca : le fonds coté de chaque marché. Obligations : TLT et IEF ; les
  autres marchés par le fonds qui les suit (SPY, QQQ, DIA, GLD, SLV, USO, BNO,
  UNG, CPER, FXE, FXB, FXA). Un fonds n'est pas le marché lui-même : son
  rendement peut s'en écarter, surtout pour le pétrole et le gaz (coût de
  roulement des contrats).

## Commandes

```text
python trendguard_bot.py courtiers                  # comptes démo et cours comparés
python trendguard_bot.py courtiers annuaire
python trendguard_bot.py courtiers cours            # cours relus tout de suite
python trendguard_bot.py courtiers verifier         # comptes démo relus tout de suite
python trendguard_bot.py courtiers set-key mt5      # ou alpaca, bybit, okx, gate…
```
