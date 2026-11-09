"""Moved to ``services/tasks/board.py`` (ADR 0016, issue #469 step 3).

This name is the same module object, so ``from services import board`` and a
patch of ``services.board.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tasks import board as _owner

sys.modules[__name__] = _owner
