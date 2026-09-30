"""Nombres écrits à la française, partagés par les rapports et les études :
virgule décimale, espace des milliers, vrai signe moins."""

from __future__ import annotations


def fr(x: float, spec: str = ".2f") -> str:
    """1234.5 avec ',.1f' → « 1 234,5 » ; -0.5 → « −0,50 »."""
    return format(x, spec).replace(",", " ").replace(".", ",").replace("-", "−")
