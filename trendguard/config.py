"""Configuration de TrendGuard : variables d'environnement, fichier .env, journal.

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

import dataclasses
import logging
import os
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import Dict, Tuple

import v29

from . import autonomy
from . import trend_strategy as ts

DAY_MS = 86_400_000

# Actifs du backtest encore cotés en USDT sur Binance (XMR, FTT, EOS exclus).
LIVE_UNIVERSE_DEFAULT: Tuple[str, ...] = (
    "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "TRX", "LINK", "LTC", "BCH",
    "XLM", "ETC", "ZEC", "DASH", "NEO", "XTZ", "ALGO", "DOT", "UNI", "AAVE",
    "ICP")

TG_ENV_DOC: Dict[str, str] = {
    "RUN_MODE": "paper | live",
    "ENABLE_LIVE_TRADING": "true pour autoriser le live",
    "LIVE_TRADING_CONFIRMATION": "Doit valoir I_UNDERSTAND_RISK",
    "BINANCE_API_KEY": "Clé API Binance (trading autorisé, retraits INTERDITS)",
    "BINANCE_API_SECRET": "Secret API Binance",
    "BINANCE_TESTNET": "true : Binance Spot testnet (live uniquement ; le paper suit le vrai marché)",
    "TG_UNIVERSE": "Actifs tradés (CSV de bases, cotées en USDT)",
    "TG_RISK_PCT": "Risque par trade en fraction d'equity (0.01 = 1 %)",
    "TG_MAX_POSITIONS": "Nombre maximum de positions simultanées",
    "TG_MAX_TOTAL_RISK": "Risque initial cumulé maximum (0.06 = 6 %)",
    "TG_KILL_DRAWDOWN": "Drawdown depuis le pic qui bloque les entrées (0.40)",
    "TG_HEARTBEAT_MIN": "Intervalle du battement de cœur dans le journal (min, 0 = off)",
    "TG_MAX_CAPITAL": "Capital max géré par le bot en USDT (0 = tout le compte)",
    "TG_DD_THROTTLE": "Profil prudent : baisse:multiplicateur (ex. 0.10:0.5) ; vide = off",
    "TG_AUTO_DIAGNOSE_DAYS": "Auto-diagnostic tous les N jours (0 = désactivé)",
    "TG_ANTICIPATION": "true : alerte quand une vente ou un achat sont probables à la prochaine clôture",
    "TG_CLASSEMENT": "true : classement quotidien des cryptos par bénéfice (panneau, auto-sélection)",
    "TG_KEEP_AWAKE": "true : l'ordinateur ne se met pas en veille tout seul pendant que le bot tourne",
    "TG_MAX_SPREAD": "Ruse : achat différé si l'écart achat/vente dépasse ce seuil (0.005 = 0,5 %)",
    "TG_ENTRY_RETRY_HOURS": "Ruse : durée des nouveaux essais d'un achat différé (heures)",
    "TG_VEILLE": "true : annonces officielles Binance lues chaque jour (retrait = achats bloqués)",
    "TG_VEILLE_IA": "true : rapport quotidien des IA (conseil seulement, clés dans .env)",
    "TG_VEILLE_DB": "Base de la veille (mémoire des IA et des annonces)",
    "PANEL_HOST": "Panneau : 127.0.0.1 (ce PC) ou 0.0.0.0 (téléphone, mot de passe requis)",
    "PANEL_PORT": "Panneau : port web (8765)",
    "TG_ALLOW_RECOVERY": "true : adopter les ordres du bot inconnus de la base (base perdue)",
    "TG_PAPER_CAPITAL": "Capital initial du mode paper (USDT)",
    "TG_DB_FILE": "Base SQLite du bot",
    "TG_LOG_FILE": "Fichier de log",
    "TG_LOCK_FILE": "Fichier de verrou (une seule instance)",
    "TG_HEALTH_MAX_AGE_SEC": "Âge max du dernier cycle réussi pour `health` (s)",
    "TELEGRAM_TOKEN": "Token bot Telegram",
    "TELEGRAM_CHAT_ID": "Chat ID destination",
    "ALERT_LEVEL": "E-mail et WhatsApp : critical (alertes critiques) ou all (tout)",
    "SMTP_HOST": "E-mail : serveur SMTP (ex. smtp.gmail.com)",
    "SMTP_PORT": "E-mail : port (587 STARTTLS, 465 SSL)",
    "SMTP_USER": "E-mail : identifiant SMTP",
    "SMTP_PASSWORD": "E-mail : mot de passe (Gmail : mot de passe d'application)",
    "ALERT_EMAIL_TO": "E-mail : adresse qui reçoit les alertes",
    "WHATSAPP_PROVIDER": "WhatsApp : callmebot (gratuit) ou twilio",
    "WHATSAPP_PHONE": "WhatsApp : numéro international (+225…)",
    "CALLMEBOT_APIKEY": "WhatsApp CallMeBot : clé reçue de callmebot.com",
    "TWILIO_ACCOUNT_SID": "WhatsApp Twilio : Account SID",
    "TWILIO_AUTH_TOKEN": "WhatsApp Twilio : Auth Token",
    "TWILIO_WHATSAPP_FROM": "WhatsApp Twilio : numéro expéditeur",
    "PANEL_PASSWORD": "Panneau : mot de passe (obligatoire avec PANEL_HOST=0.0.0.0) ; set-panel-password",
    "PANEL_ASSISTANT_IA": "Assistant du panneau : true = réponses rédigées par l'IA de la veille si une clé existe",
    "PANEL_ASSISTANT_MODEL": "Assistant du panneau : modèle Claude (claude-sonnet-5 par défaut)",
}


@dataclass(frozen=True)
class GuardConfig:
    run_mode: str = "paper"
    quote: str = "USDT"
    universe: Tuple[str, ...] = LIVE_UNIVERSE_DEFAULT
    params: ts.TrendParams = ts.TrendParams()
    catastrophe_atr: float = 1.0        # stop exchange = stop clôture - k × vol
    stop_raise_min_pct: float = 0.01    # seuil de remontée du stop exchange
    decision_delay_sec: int = 120       # attente après 00:00 UTC
    loop_interval_sec: int = 60
    ohlcv_limit: int = 1000
    kill_drawdown: float = 0.40
    heartbeat_min: int = 15
    clock_resync_min: int = 60          # bot remis à l'heure de Binance
    auto_diagnose_days: int = 7         # 0 = désactivé
    # Veille (market_watch.py) : désactivée par défaut dans le code (tests,
    # rejeu hors ligne), activée par l'environnement (TG_VEILLE=true).
    watch: bool = False                 # annonces officielles → veto d'achat
    watch_ai: bool = False              # rapport quotidien des IA (conseil)
    # Classement des cryptos par bénéfice de la stratégie (affiché dans le
    # panneau, utilisé par l'auto-sélection) ; activé par l'environnement.
    rank_cryptos: bool = False
    # Alertes d'anticipation (vente ou achat probables à la prochaine
    # clôture) ; activées par l'environnement.
    anticipation_alerts: bool = False
    watch_db: str = ""
    max_capital: float = 0.0            # 0 = tout le compte
    keep_awake: bool = False            # anti-veille (activé par l'environnement)
    max_spread: float = 0.005           # ruse : carnet anormal → achat différé
    entry_retry_hours: float = 6.0      # achat différé : nouveaux essais pendant 6 h
    allow_recovery: bool = False        # adopter les ordres du bot inconnus
    paper_capital: float = 10_000.0
    enable_live_trading: bool = False
    live_confirmation: str = ""
    binance_testnet: bool = False
    db_file: str = ""
    log_file: str = ""
    lock_file: str = ""

    def __post_init__(self):
        if self.run_mode not in ("paper", "live"):
            raise ValueError("RUN_MODE doit être paper ou live.")
        if self.run_mode == "live":
            if not self.enable_live_trading:
                raise ValueError("LIVE refusé: ENABLE_LIVE_TRADING=true requis.")
            if self.live_confirmation != "I_UNDERSTAND_RISK":
                raise ValueError("LIVE refusé: LIVE_TRADING_CONFIRMATION requis.")
        if "BTC" not in self.universe:
            raise ValueError("BTC doit faire partie de l'univers (régime).")
        if self.max_capital < 0:
            raise ValueError("TG_MAX_CAPITAL doit être >= 0.")
        if not (0 < self.kill_drawdown < 1):
            raise ValueError("TG_KILL_DRAWDOWN doit être dans ]0, 1[.")
        if self.catastrophe_atr <= 0:
            raise ValueError("catastrophe_atr > 0 requis.")
        if not (0 < self.max_spread < 0.2):
            raise ValueError("TG_MAX_SPREAD doit être dans ]0, 0.2[.")
        if self.entry_retry_hours < 0:
            raise ValueError("TG_ENTRY_RETRY_HOURS doit être >= 0.")
        self.params.validate()
        for name, default in (
                ("db_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.db")),
                ("log_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.log")),
                ("lock_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.lock"))):
            if not getattr(self, name):
                object.__setattr__(self, name, default)

    @property
    def stop_file(self) -> str:
        """Demande d'arrêt déposée par le panneau de contrôle, à côté du
        verrou (fonctionne pareil sous Windows, Linux et macOS)."""
        return autonomy.sidecar(self.lock_file, ".stop")

    @property
    def alive_file(self) -> str:
        """Signe de vie du bot, surveillé par le superviseur."""
        return autonomy.sidecar(self.lock_file, ".alive")


def parse_dd_throttle(raw: str) -> Tuple[Tuple[float, float], ...]:
    """'0.10:0.5,0.20:0.25' → ((0.10, 0.5), (0.20, 0.25)) ; vide → ()."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            thr, mult = part.split(":")
            out.append((float(thr), float(mult)))
        except ValueError:
            raise ValueError(f"TG_DD_THROTTLE invalide : {part!r} "
                             f"(format baisse:multiplicateur, ex. 0.10:0.5)")
    return tuple(sorted(out))


def load_guard_config_from_env() -> GuardConfig:
    uni = tuple(a.strip().upper() for a in
                v29._env_s("TG_UNIVERSE", ",".join(LIVE_UNIVERSE_DEFAULT)).split(",")
                if a.strip())
    params = dataclasses.replace(
        ts.TrendParams(),
        risk_pct=v29._env_f("TG_RISK_PCT", 0.01),
        max_positions=v29._env_i("TG_MAX_POSITIONS", 8),
        max_total_risk=v29._env_f("TG_MAX_TOTAL_RISK", 0.06),
        dd_throttle=parse_dd_throttle(v29._env_s("TG_DD_THROTTLE", "")))
    return GuardConfig(
        run_mode=v29._env_s("RUN_MODE", "paper").lower(),
        universe=uni, params=params,
        kill_drawdown=v29._env_f("TG_KILL_DRAWDOWN", 0.40),
        heartbeat_min=v29._env_i("TG_HEARTBEAT_MIN", 15),
        auto_diagnose_days=v29._env_i("TG_AUTO_DIAGNOSE_DAYS", 7),
        watch=v29._env_b("TG_VEILLE", True),
        watch_ai=v29._env_b("TG_VEILLE_IA", True),
        rank_cryptos=v29._env_b("TG_CLASSEMENT", True),
        anticipation_alerts=v29._env_b("TG_ANTICIPATION", True),
        watch_db=v29._env_s("TG_VEILLE_DB", os.path.join(v29.APP_DIR, "trendguard_veille.db")),
        max_capital=v29._env_f("TG_MAX_CAPITAL", 0.0),
        keep_awake=v29._env_b("TG_KEEP_AWAKE", True),
        max_spread=v29._env_f("TG_MAX_SPREAD", 0.005),
        entry_retry_hours=v29._env_f("TG_ENTRY_RETRY_HOURS", 6.0),
        allow_recovery=v29._env_b("TG_ALLOW_RECOVERY", False),
        paper_capital=v29._env_f("TG_PAPER_CAPITAL", 10_000.0),
        enable_live_trading=v29._env_b("ENABLE_LIVE_TRADING", False),
        live_confirmation=v29._env_s("LIVE_TRADING_CONFIRMATION", ""),
        binance_testnet=v29._env_b("BINANCE_TESTNET", False),
        db_file=v29._env_s("TG_DB_FILE", ""),
        log_file=v29._env_s("TG_LOG_FILE", ""),
        lock_file=v29._env_s("TG_LOCK_FILE", ""))


class ExchangeTimeFormatter(logging.Formatter):
    """Horodatage des journaux à l'heure de Binance (heure du PC corrigée
    de l'écart mesuré par v29.sync_exchange_clock), millisecondes
    comprises."""

    def formatTime(self, record, datefmt=None):
        t = record.created + v29.clock_offset_ms() / 1000
        s = time.strftime(datefmt or self.default_time_format, time.localtime(t))
        if not datefmt:
            s = self.default_msec_format % (s, int((t % 1) * 1000))
        return s


def build_guard_logger(log_file: str) -> logging.Logger:
    lg = logging.getLogger("trendguard")
    lg.handlers.clear()
    lg.setLevel(logging.INFO)
    fmt = ExchangeTimeFormatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    lg.addHandler(fh)
    lg.addHandler(sh)
    lg.propagate = False
    return lg


# Le .env lu par le bot (v29 : load_dotenv depuis le dossier du projet), et
# non celui du dossier courant : lancé d'ailleurs, set-keys écrirait des
# clés que le bot ne lirait jamais.
ENV_FILE = os.path.join(v29.APP_DIR, ".env")


def set_env_var(path: str, name: str, value: str) -> None:
    """Écrit NAME=value dans le fichier .env (remplace la ligne active
    existante, sinon ajoute) ; fichier lisible par son seul propriétaire."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{name}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{name}={value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)
