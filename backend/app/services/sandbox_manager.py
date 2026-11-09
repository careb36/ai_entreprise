"""Moved to ``services/workspace/sandbox_manager.py`` (ADR 0016, issue #469 step 6).

This name is the same module object, so ``from services import sandbox_manager`` and a
patch of ``services.sandbox_manager.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.workspace import sandbox_manager as _owner

sys.modules[__name__] = _owner
