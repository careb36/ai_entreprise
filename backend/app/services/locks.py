"""Moved to ``services/platform/locks.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import locks`` and a
patch of ``services.locks.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import locks as _owner

sys.modules[__name__] = _owner
