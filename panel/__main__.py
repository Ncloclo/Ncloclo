"""python -m panel [--demo] [--host H] [--port P] : panneau de contrôle."""

import sys

import trendguard_bot as tg

sys.exit(tg.main(["panel"] + sys.argv[1:]))
