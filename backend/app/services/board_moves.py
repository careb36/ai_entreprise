"""Moved to ``services/loop/board_moves.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import board_moves`` and a
patch of ``services.board_moves.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import board_moves as _owner

sys.modules[__name__] = _owner
