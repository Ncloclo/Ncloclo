"""
Panneau de contrôle TrendGuard : application web locale.

  python trendguard_bot.py panel          # http://127.0.0.1:8765 (ce PC)
  python trendguard_bot.py panel --demo   # données fictives, sans réseau

Modules :
  server.py   serveur HTTP (bibliothèque standard), API JSON, sécurité
  data.py     lecture seule de la base du bot (état, capital, positions)
  market.py   cours Binance publics, mis en cache
  control.py  démarrage et arrêt propre du bot
  demo.py     données de démonstration (aperçu, tests)
  static/     interface (HTML, CSS, JavaScript, application installable)
"""
