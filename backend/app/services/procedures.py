"""Moved to ``services/learning/procedures.py`` (ADR 0016, issue #469 step 8).

This name is the same module object, so ``from services import procedures`` and a
patch of ``services.procedures.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.learning import procedures as _owner

sys.modules[__name__] = _owner
