"""Journaux des outils : muets pour les simulations et les lectures de la
base, ou limités aux avertissements pour les vérifications (seules les
causes d'échec s'affichent). Le journal du vrai bot vient de
v29.build_logger."""

from __future__ import annotations

import logging
from typing import Any


def silent_logger(name: str, out: Any = None) -> logging.Logger:
    """Journal sans fichier ni écran ; avec `out`, seuls les avertissements y
    sont écrits, précédés de ⚠️."""
    lg = logging.getLogger(name)
    if out is None:
        lg.handlers[:] = [logging.NullHandler()]
    else:
        reasons = logging.StreamHandler(out)
        reasons.setLevel(logging.WARNING)
        reasons.setFormatter(logging.Formatter("  ⚠️  %(message)s"))
        lg.handlers[:] = [reasons]
        lg.setLevel(logging.WARNING)
    lg.propagate = False
    return lg
