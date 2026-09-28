"""TrendGuard : bot de suivi de tendance multi-cryptos sur Binance Spot.

Point d'entrée : python trendguard_bot.py <commande> (voir docs/ARCHITECTURE.md).

Stratégie et décision
  trend_strategy.py    règles d'achat et de vente, backtest, sélection
  config.py            configuration (variables d'environnement, fichier .env)
  bot.py               le bot : décision quotidienne, exécution, surveillance
  selection.py         cryptos achetables (choix du panneau)
  explain.py           raisonnement du jour, en clair
  anticipation.py      ventes, achats et risques probables à la prochaine clôture

Fonctionnement autonome
  autonomy.py          superviseur, démarrage avec l'ordinateur, anti-veille
  alerts.py            alertes Telegram, e-mail et WhatsApp
  market_watch.py      veille : annonces officielles de Binance, avis des IA
  watch_claude.py      avis de Claude pour la veille (SDK Anthropic)

Contrôle et recherche
  cli.py               ligne de commande
  diagnostics.py       auto-diagnostic (lecture seule)
  strategy_lab.py      laboratoire des stratégies
  replay.py            rejeu paper sur historique réel
  replay_animation.py  page d'animation du rejeu

Le moteur d'exécution Binance est dans le paquet v29, le panneau de contrôle
dans panel, les études reproductibles dans research.
"""
