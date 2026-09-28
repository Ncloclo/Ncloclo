"""
V29.6-QUANT — Bot hybride CEX (Binance Spot) + Wallet EVM.

════════════════════════════════════════════════════════════════════════
PRINCIPES D'HARMONISATION
════════════════════════════════════════════════════════════════════════
P1. Résultats nommés. P2. Pas de réentrance implicite. P3. Fail-closed/noisy.
P4. I/O cohérentes. P5. Sections ascendantes (0 → 17).
P6. Env unifiés. P7. Logs [CAT]. P8. Exception → log + action.
P9. Transitoires exclus. P10. Unités : quote/base/ratio.
P11. Le solde exchange fait foi : on ne vend/protège jamais plus que le
     solde disponible du bot (hors EXTERNAL_BASE_RESERVE).
P12. Toute exécution est idempotente : chaque fill est comptabilisé une
     seule fois (recorded_fills), chaque ordre sensible est précédé d'une
     intention persistée (pending_order) et résolu par client-id.

════════════════════════════════════════════════════════════════════════
CHANGELOG V29.6 (refonte corrective du diagnostic V29.5)
════════════════════════════════════════════════════════════════════════
Bloquants
  - Config par défaut valide (adaptive_freshness_max=45 ≤ max_signal_age_min).
  - Suite de tests déplacée dans tests/ (pytest) avec un simulateur
    Binance Spot ; plus aucun test tautologique.
Exécution live
  - Cache de soldes invalidé après chaque ordre (fin des panic-sells
    systématiques après chaque achat).
  - Frais d'achat prélevés en base pris en compte : quantité détenue =
    net reçu, plafonnée au solde réel.
  - Types d'ordres Spot valides (STOP_LOSS / STOP_LOSS_LIMIT selon
    exchangeInfo) + OCO natif uniquement (le pseudo-OCO à 2 ordres est
    impossible en Spot : le solde est bloqué par le premier ordre).
  - Self-test via POST /api/v3/order/test (aucun ordre réel, aucun solde requis).
  - Machine de protection unique : snapshot → fills comptés une fois →
    re-protection systématique ; une position live n'est jamais laissée nue
    (re-protection à chaque cycle + stop logiciel de dernier recours).
  - Annulation « sûre » : on relit chaque jambe APRÈS l'annulation (plus de
    fill perdu entre la vérification et l'annulation).
  - Sorties partielles live : annulation de la protection → vente → nouvelle
    protection sur le reliquat.
  - Time-exit : plus de TypeError, plus de position laissée sans stop.
  - Intentions d'ordres persistées + résolution par client-id (crash ou
    timeout réseau entre l'ordre et la sauvegarde).
  - Reconciliation fail-closed (une erreur d'API n'efface plus une position).
  - GET /api/v3/orderList sans paramètre `symbol`.
Blocages définitifs supprimés
  - Pause pertes consécutives : compteur dédié remis à zéro à la pause.
  - Plus de faux « double-sell » / orphelin à chaque sortie OCO.
  - Orphelin re-vérifié périodiquement ; commande `resume` après audit.
  - Poussière (< min_notional) sortie proprement des livres.
Risque / quant
  - Adaptatif « consec » dans le bon sens (Sharpe négatif → pause plus tôt).
  - Latence adaptative mesurée sur chaque bougie (plus de biais de survie).
  - Break-even au-dessus du coût de revient (frais + slippage).
  - R-multiple rapporté au risque initial (additif sur les jambes partielles).
  - Disjoncteurs fail-closed si l'equity est illisible ; DD hebdo réarmé
    chaque semaine.
  - Volatilité réalisée annualisée selon le timeframe.
Backtest / recherche
  - Même logique que le live : biais HTF/BTC (sans look-ahead), BE,
    trailing, time-exit, cooldowns, disjoncteurs, désactivation de module.
  - Plus de double comptage des partielles ; métriques sur TOUS les trades.
  - Walk-forward avec vraie optimisation in-sample et fenêtres sans perte
    de warm-up.
Blockchain / sécurité
  - Nonce : une seule sémantique (« prochain nonce libre ») → plus de trou.
  - Confirmations, effectiveGasPrice, suivi de toutes les tx RBF.
  - Encodage ERC-20 compatible web3 v6/v7/v8 ; montants en Decimal.
  - Secrets (URL RPC, clés) retirés des messages d'erreur.

════════════════════════════════════════════════════════════════════════
ORGANISATION DU PAQUET
════════════════════════════════════════════════════════════════════════
Moteur d'exécution (utilisé par TrendGuard) :
  v29/constants.py         constantes, dossier du programme, fichier .env
  v29/utils.py             environnement, secrets masqués, heure de Binance
  v29/config.py            configuration par paire
  v29/models.py            positions, portefeuille, contexte
  v29/infra.py             journaux, alertes Telegram, verrou d'instance
  v29/store.py             base SQLite : état, trades, intentions d'ordres
  v29/exchange.py          adaptateur Binance Spot
  v29/risk.py              taille des positions, disjoncteurs, stops
  v29/execution.py         entrées, protections, sorties
  v29/reconciliation.py    réconciliation au démarrage

Ancien bot V29.6 intraday, rangé à part (v29/intraday/, chargé seulement
s'il est utilisé) : signaux, indicateurs, moteur adaptatif, wallet EVM,
backtest, boucle et ligne de commande (python -m v29 bot | backtest | …).

`import v29` donne accès à tous les noms du moteur ; l'ancien bot :
`from v29 import intraday`.
"""

from __future__ import annotations

from .config import (  # noqa: F401
    Config,
    _parse_symbol,
    dump_config_summary,
    load_config_from_env,
)
from .constants import (  # noqa: F401
    _ENV_BOOL,
    _LOG_ROOT,
    APP_DIR,
    CID_ENTRY_LIMIT,
    CID_ENTRY_MARKET,
    CID_EXIT_MARKET,
    CID_OCO_LIST,
    CID_OCO_SL,
    CID_OCO_TP,
    CID_STOP,
    ENV_DOC,
    PROTECTION_CID_PREFIXES,
    SCHEMA_VERSION,
    VERSION_MODULE,
)
from .exchange import (  # noqa: F401
    _NUMERIC_STR,
    _ORDER_STATUS_MAP,
    AmbiguousOrder,
    ExchangeAdapter,
    MarketRules,
    OrderResult,
    _fnum,
)
from .execution import (  # noqa: F401
    _LEG_REASON,
    ExecutionEngine,
    _update_extremes,
    update_extremes,
)
from .infra import (  # noqa: F401
    Console,
    Heartbeat,
    JsonFormatter,
    Notifier,
    ProcessLock,
    acquire_instance_locks,
    build_heartbeat_logger,
    build_logger,
    release_locks,
)
from .models import (  # noqa: F401
    AUTO_CLEARABLE_HALTS,
    AdaptiveState,
    BlockchainState,
    BotContext,
    BotState,
    CancelResult,
    EntryResult,
    Fill,
    HaltKind,
    Portfolio,
    Position,
    ProtectionMode,
    ProtectionSnapshot,
    ProtectionState,
    RiskState,
    Signal,
    clear_halt,
    halt_ctx,
    is_frozen,
)
from .reconciliation import (  # noqa: F401
    _recover_position,
    reconcile,
)
from .risk import (  # noqa: F401
    RR_BY_MODULE,
    RiskEngine,
    break_even_stop,
    detect_flash_move,
    in_cooldown,
    in_flash_cooldown,
    record_closed_trade,
    trailing_stop,
)
from .store import (  # noqa: F401
    Store,
)
from .utils import (  # noqa: F401
    _TF_UNITS_MS,
    BINANCE_TIMEOUT_MS,
    PUBLIC_DATA_API,
    ClockSync,
    PublicKlines,
    _annualization_factor,
    _bars_per_day,
    _cancel_client_id,
    _client_id,
    _dataclass_from_dict,
    _day_key,
    _dec_round,
    _env_b,
    _env_f,
    _env_i,
    _env_s,
    _env_tuple_csv,
    _get_env_logger,
    _isnan,
    _parse_iso,
    _row_get,
    _timeframe_ms,
    _today_utc,
    _utcnow,
    _utcnow_iso,
    _week_key,
    _week_start_utc,
    clock_offset_ms,
    describe_clock,
    ensure_utf8_stdio,
    make_binance,
    make_public_binance,
    measure_exchange_clock,
    redact_address,
    redact_url,
    resync_clock,
    scrub_secrets,
    set_clock_offset_ms,
    sync_exchange_clock,
)
