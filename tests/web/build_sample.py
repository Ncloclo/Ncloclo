"""Page d'exemple pour les tests navigateur (tests/web/*.spec.js) : le vrai
rejeu du bot sur le marché synthétique des tests Python, sans réseau.

  python tests/web/build_sample.py      # écrit tests/web/.out/sample.html
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.dirname(HERE)]

from trendguard import replay_animation as ra  # noqa: E402
from test_trendguard import SIM_FROM, synthetic_market  # noqa: E402

OUT = os.path.join(HERE, ".out", "sample.html")

if __name__ == "__main__":
    close, volume = synthetic_market()
    data = ra.build_replay(close, volume, str(close.index[SIM_FROM].date()))
    data.update(generated="exemple (marché synthétique des tests)", paper_live=None)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(ra.render_html(data))
    print(f"{OUT} ({len(data['dates'])} jours, {data['metrics']['trades']} trades)")
