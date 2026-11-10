"""Moved to ``services/release/delivery_undo.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import delivery_undo`` and a
patch of ``services.delivery_undo.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import delivery_undo as _owner

sys.modules[__name__] = _owner
