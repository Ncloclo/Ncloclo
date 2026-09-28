"""python -m v29 : ligne de commande du bot V29 (bot, backtest, walkforward,
status, resume, docs…)."""

import sys

from .cli import main

try:
    main()
except KeyboardInterrupt:
    print("\nInterrompu.")
    sys.exit(0)
