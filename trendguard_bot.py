#!/usr/bin/env python3
"""
TrendGuard Bot — portefeuille de suivi de tendance sur Binance Spot.

Stratégie : trendguard/trend_strategy.py (validée hors échantillon, voir
docs/TRENDGUARD_REPORT.md). Les décisions quotidiennes appellent EXACTEMENT
les mêmes fonctions que le backtest (update_positions / plan_entries).

Exécution : moteur V29.6 (paquet v29) par paire — intention persistée avant
chaque ordre, frais en base, annulation sûre, reprise après crash.

Protection à deux niveaux :
  - stop de CLÔTURE (celui de la stratégie, évalué chaque jour après 00:00 UTC)
    → définit le risque de 1 % ;
  - stop CATASTROPHE sur l'exchange (STOP_LOSS), placé 1 × volatilité sous
    le stop de clôture et remonté avec lui → protège contre un krach entre
    deux clôtures, sans provoquer de sorties sur de simples mèches.

Commandes :
  python trendguard_bot.py run       # boucle continue (paper par défaut)
  python trendguard_bot.py once      # un seul cycle (cron)
  python trendguard_bot.py status    # état du portefeuille
  python trendguard_bot.py resume    # lève l'arrêt d'urgence après audit
  python trendguard_bot.py supervise # bot relancé seul en cas de plantage
  python trendguard_bot.py stop      # arrêt propre de l'automatisation
  python trendguard_bot.py autostart on|off   # démarrage avec l'ordinateur
  python trendguard_bot.py panel     # panneau de contrôle (navigateur)
  python trendguard_bot.py set-panel-password   # accès depuis un téléphone

Outils (mêmes options qu'avant, après le nom de l'outil) :
  python trendguard_bot.py alerts configurer|tester     # alertes
  python trendguard_bot.py watch [check|set-key <ia>]   # veille
  python trendguard_bot.py strategy download|research   # stratégie
  python trendguard_bot.py lab --cache data_binance     # laboratoire
  python trendguard_bot.py animation                    # page du rejeu
  python trendguard_bot.py evolution [examen|regles]    # évolution encadrée
  python trendguard_bot.py rapport [maintenant]         # rapport quotidien

Ce fichier est le seul point d'entrée : le code est rangé dans le paquet
trendguard/ (voir docs/ARCHITECTURE.md) et tous ses noms restent
accessibles ici (import trendguard_bot).

Bibliothèques : si le dossier .venv existe à côté de ce fichier, toute
commande y est relancée (versions testées, séparées des autres logiciels du
PC) ; sinon elle tourne avec les bibliothèques du PC.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # Avant tout autre import : les bibliothèques lues ensuite sont celles de
    # l'environnement propre du bot (trendguard/environnement.py).
    from trendguard.environnement import relaunch

    _code = relaunch(os.path.dirname(os.path.abspath(__file__)), sys.orig_argv[1:])
    if _code is not None:
        sys.exit(_code)

from trendguard.bot import DecisionDeferred, Slot, TrendGuardBot, _sleep, _stop, last_closed_day
from trendguard.cli import (
    _ORDER_METHODS,
    MIN_LIVE_CAPITAL,
    TOOLS,
    _auth_hint,
    _build,
    _forbid_orders,
    check_api_keys,
    clean_api_secret,
    cmd_diagnose,
    cmd_set_keys,
    cmd_set_panel_password,
    cmd_set_secret,
    cmd_verify,
    cmd_verify_public,
    health_check,
    main,
    panel_password_problem,
)
from trendguard.config import (
    DAY_MS,
    ENV_FILE,
    LIVE_UNIVERSE_DEFAULT,
    TG_ENV_DOC,
    ExchangeTimeFormatter,
    GuardConfig,
    build_guard_logger,
    load_guard_config_from_env,
    parse_dd_throttle,
    set_env_var,
)
from trendguard.explain import EXIT_WHY, WATCH_BAND_PCT, _explain_asset, _pc, explain_decision
from trendguard.replay import HistoricalExchange, replay
from trendguard.selection import read_selection, write_selection

if __name__ == "__main__":
    sys.exit(main())
