"""Moved to ``services/contracts/sandbox_schemas.py`` (ADR 0013, 0016).

This name is the same module object, so ``from services import sandbox_schemas`` and a
patch of ``services.sandbox_schemas.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.contracts import sandbox_schemas as _owner

sys.modules[__name__] = _owner
