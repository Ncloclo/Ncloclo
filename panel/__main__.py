"""python -m panel [--demo] [--host H] [--port P] : panneau de contrôle."""

import sys

from trendguard.cli import main

sys.exit(main(["panel"] + sys.argv[1:]))
