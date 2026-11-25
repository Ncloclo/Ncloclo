"""
Table des villes du panneau (trendguard/villes.tsv.gz) : les villes de plus
de 15 000 habitants de GeoNames (licence CC BY 4.0, https://www.geonames.org),
pour trouver la ville la plus proche d'une position sans aucun service
extérieur (trendguard/profil.py, nearest_city).

À refaire rarement, à la main :

    python -m research.villes                  # télécharge cities15000.zip puis écrit la table
    python -m research.villes cities15000.zip  # depuis un fichier déjà téléchargé
"""

from __future__ import annotations

import gzip
import io
import sys
import urllib.request
import zipfile
from datetime import date
from typing import List, Optional, Sequence, Tuple

from trendguard import profil

SOURCE = "https://download.geonames.org/export/dump/cities15000.zip"
# Quartiers d'une ville (PPLX), lieux disparus ou historiques : pas des villes.
SKIP_CODES = {"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"}


def rows_of(text: str) -> List[Tuple[str, str, float, float, int]]:
    """(pays, ville, latitude, longitude, habitants) de chaque ligne GeoNames."""
    out = []
    for line in io.StringIO(text):
        f = line.rstrip("\n").split("\t")
        if len(f) < 15 or len(f[8]) != 2 or f[7] in SKIP_CODES:
            continue
        out.append((f[8], f[1].replace("\t", " "), round(float(f[4]), 2), round(float(f[5]), 2),
                    int(f[14] or 0)))
    out.sort(key=lambda r: (r[0], -r[4], r[1]))
    return out


def write(rows: Sequence[Tuple[str, str, float, float, int]], path: str = str(profil.CITIES)) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# Villes de plus de 15 000 habitants, GeoNames (CC BY 4.0), lues le {date.today()}\n")
        fh.write("# pays\tville\tlatitude\tlongitude\n")
        for iso, name, lat, lon, _pop in rows:
            fh.write(f"{iso}\t{name}\t{lat:g}\t{lon:g}\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        with open(args[0], "rb") as fh:
            data = fh.read()
    else:
        req = urllib.request.Request(SOURCE, headers={"User-Agent": "TrendGuard/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:     # noqa: S310 (adresse fixe)
            data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        text = z.read("cities15000.txt").decode("utf-8")
    rows = rows_of(text)
    write(rows)
    print(f"{len(rows)} villes écrites dans {profil.CITIES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
