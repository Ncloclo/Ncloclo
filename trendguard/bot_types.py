"""Types partagés par le bot (bot.py) et ses parties (bot_routines.py,
bot_execution.py) : une paire tradée, la décision reportée, le jour de la
dernière bougie close."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import v29


@dataclass
class Slot:
    base: str
    symbol: str
    cfg: v29.Config
    ex: v29.ExchangeAdapter
    eng: v29.ExecutionEngine
    ctx: v29.BotContext


class DecisionDeferred(RuntimeError):
    """Données insuffisantes pour décider sans risque : la décision
    quotidienne est retentée au cycle suivant (jamais de vente sur une
    simple panne réseau)."""


def last_closed_day(now: datetime, delay_sec: int = 0) -> str:
    """Date (UTC) de la dernière bougie journalière clôturée depuis au
    moins `delay_sec` secondes."""
    t = now - timedelta(seconds=delay_sec)
    return (t - timedelta(days=1)).date().isoformat()
