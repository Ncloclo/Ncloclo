"""Nombres écrits à la française, partagés par le bot, le panneau, les
rapports et les études : virgule décimale, espace des milliers, vrai signe
moins."""

from __future__ import annotations


def fr(x: float, spec: str = ".2f") -> str:
    """1234.5 avec ',.1f' → « 1 234,5 » ; -0.5 → « −0,50 »."""
    return format(x, spec).replace(",", " ").replace(".", ",").replace("-", "−")


def fr_plain(x: float, digits: int = 8) -> str:
    """Nombre écrit en entier, sans décimale inutile ni puissance de dix :
    0.00001 → « 0,00001 » ; 84000.10 → « 84 000,1 » ; 5.0 → « 5 »."""
    out = fr(x, f",.{digits}f")
    if "," in out:
        out = out.rstrip("0").rstrip(",")
    return "0" if out == "−0" else out
