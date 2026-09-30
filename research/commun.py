"""Ce qui est commun aux études reproductibles (research/) : données Binance
des paires du bot, options de la ligne de commande et écriture du rapport."""

from __future__ import annotations

import argparse
import os
from typing import Callable, List, Optional, Tuple

import pandas as pd

import v29
from trendguard import evolution
from trendguard.config import LIVE_UNIVERSE_DEFAULT


def load_binance(cache: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Clôtures et volumes journaliers Binance des paires du bot, en cache au
    format Coin Metrics : téléchargés une fois, jamais rafraîchis (études
    reproductibles)."""
    return evolution.load_history(cache, list(LIVE_UNIVERSE_DEFAULT), max_age_days=None)


def study_args(description: str, default_out: Optional[str], argv: Optional[List[str]] = None,
               extra: Optional[Callable[[argparse.ArgumentParser], None]] = None
               ) -> argparse.Namespace:
    """Options communes (--cache, --out) et options propres à l'étude."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--cache", default="data_binance")
    if default_out is not None:
        ap.add_argument("--out", default=default_out)
    if extra is not None:
        extra(ap)
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    return args


def write_report(text: str, out: str) -> None:
    """Rapport écrit dans docs/ (dossier créé au besoin), puis affiché."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    print(f"\nRapport écrit : {out}")
