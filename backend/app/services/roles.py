"""Moved to ``services/contracts/roles.py`` (ADR 0013, 0016).

This name is the same module object, so ``from services import roles`` and a
patch of ``services.roles.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.contracts import roles as _owner

sys.modules[__name__] = _owner
