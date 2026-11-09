"""Moved to ``services/contracts/edit_blocks.py`` (ADR 0013, 0016).

This name is the same module object, so ``from services import edit_blocks`` and a
patch of ``services.edit_blocks.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.contracts import edit_blocks as _owner

sys.modules[__name__] = _owner
