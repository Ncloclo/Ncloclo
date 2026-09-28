"""Constantes, chemins, dépendances optionnelles et documentation des variables d'environnement.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import os
from typing import Dict

VERSION_MODULE = "V29.6"
# Les fichiers d'état par défaut sont placés à côté du programme (et non
# dans le dossier courant) : un lancement depuis un autre dossier retrouve
# la même base et le même verrou.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 11

# Fichier .env (python-dotenv), à côté du programme.

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(APP_DIR, ".env"))
except ImportError:
    pass


_ENV_BOOL = ("1", "true", "yes", "on")
_LOG_ROOT = "v29"

# Préfixes de client-id : permettent d'identifier nos ordres sur l'exchange.
CID_ENTRY_MARKET = "BM"
CID_ENTRY_LIMIT = "QE"
CID_EXIT_MARKET = "SM"
CID_OCO_LIST = "QO"
CID_OCO_TP = "QT"
CID_OCO_SL = "QS"
CID_STOP = "QB"
PROTECTION_CID_PREFIXES = (CID_OCO_LIST, CID_OCO_TP, CID_OCO_SL, CID_STOP)


ENV_DOC: Dict[str, str] = {
    "SYMBOL": "Paire CEX (ex: TRX/USDT)",
    "BTC_SYMBOL": "Paire de référence BTC (biais et volatilité)",
    "TIMEFRAME": "Timeframe principal (15m, 1h, 4h)",
    "HTF_TIMEFRAME": "Timeframe supérieur pour le biais (> TIMEFRAME)",
    "RUN_MODE": "paper | live",
    "ENABLE_LIVE_TRADING": "true pour autoriser le live",
    "LIVE_TRADING_CONFIRMATION": "Doit valoir I_UNDERSTAND_RISK",
    "BINANCE_API_KEY": "Clé API Binance (trading autorisé, retraits INTERDITS)",
    "BINANCE_API_SECRET": "Secret API Binance",
    "BINANCE_TESTNET": "true : Binance Spot testnet (à utiliser avant tout live)",
    "RISK_BASE_PCT": "Risque de base par trade en fraction d'equity (0.010 = 1%)",
    "RISK_MIN_PCT": "Risque minimum par trade",
    "RISK_MAX_PCT": "Risque maximum par trade",
    "SIZING_MODE": "fixed | kelly | vol_target",
    "MAX_DAILY_DD": "Drawdown journalier déclenchant le halt (0.03 = 3%)",
    "MAX_WEEKLY_DD": "Drawdown hebdomadaire déclenchant le halt",
    "EXTERNAL_BASE_RESERVE": "Quantité de base détenue hors bot (jamais vendue)",
    "USE_OCO": "true : protection OCO native (TP + SL)",
    "USE_L2_FILTER": "true : filtre carnet L2",
    "USE_SMART_BUY": "true : chaser limit au lieu de market",
    "SELF_TEST_CONDITIONAL_ORDERS": "true : valide les ordres au boot via order/test",
    "RECOVERY_REQUIRE_VERIFIED_ENTRY": "true : refuse une recovery sans achat vérifié",
    "RECOVERY_ADOPT_ORDERS": "true : adopter les ordres du bot inconnus de la base (base perdue)",
    "INTRABAR_PARTIAL_MODE": "Backtest : ohlc | optimistic | conservative",
    "ADAPTIVE_ENABLED": "true : active l'AdaptiveEngine",
    "HEARTBEAT_EVERY_CYCLES": "Intervalle heartbeat (cycles)",
    "HEARTBEAT_SPINNER": "true : spinner Unicode",
    "HEARTBEAT_USE_COLORS": "true : couleurs ANSI",
    "HEARTBEAT_LOG_FILE": "Chemin log heartbeat",
    "BLOCKCHAIN_ENABLED": "true : active le wallet EVM",
    "BLOCKCHAIN_CHAIN": "ethereum | bsc | polygon | arbitrum",
    "BLOCKCHAIN_RPC_URL": "URL du RPC (secret : jamais loggée en clair)",
    "BLOCKCHAIN_CHAIN_ID": "1 | 56 | 137 | 42161",
    "BLOCKCHAIN_PRIVATE_KEY": "Clé privée hex (0x…) — JAMAIS commit, wallet dédié",
    "BLOCKCHAIN_DRY_RUN": "true : simule sans broadcast",
    "BLOCKCHAIN_MAX_TX_VALUE_ETH": "Plafond par tx en natif",
    "BLOCKCHAIN_MIN_GAS_RESERVE_ETH": "Réserve native à ne pas entamer",
    "BLOCKCHAIN_MAX_PRIORITY_FEE_GWEI": "Tip EIP-1559 max",
    "BLOCKCHAIN_MAX_FEE_GWEI": "Fee total EIP-1559 max",
    "BLOCKCHAIN_CONFIRMATIONS": "Confirmations requises avant de finaliser une tx",
    "BLOCKCHAIN_TX_TIMEOUT_SEC": "Délai avant remplacement (RBF) d'une tx en attente",
    "BLOCKCHAIN_WHITELIST_ENABLED": "true : restreint les destinations",
    "BLOCKCHAIN_WHITELIST": "Adresses autorisées, CSV",
    "BLOCKCHAIN_TRACK_TOKEN": "Adresse ERC-20 à suivre",
    "BLOCKCHAIN_TRACK_TOKEN_SYMBOL": "Symbole du token suivi",
    "BLOCKCHAIN_SWEEP_ENABLED": "true : sweep auto de l'excédent natif",
    "BLOCKCHAIN_SWEEP_TARGET": "Adresse cible du sweep (doit être whitelistée)",
    "BLOCKCHAIN_SWEEP_TRIGGER_ETH": "Solde au-dessus duquel on sweep",
    "BLOCKCHAIN_SWEEP_KEEP_ETH": "Réserve native conservée pour le gaz",
    "TELEGRAM_TOKEN": "Token bot Telegram",
    "TELEGRAM_CHAT_ID": "Chat ID destination",
    "DB_FILE": "Chemin DB SQLite",
    "LOG_FILE": "Chemin log",
    "LOCK_FILE": "Chemin lock",
}
