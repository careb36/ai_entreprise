"""Moved to ``services/workspace/sandbox.py`` (ADR 0016, issue #469 step 6).

This name is the same module object, so ``from services import sandbox`` and a
patch of ``services.sandbox.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.workspace import sandbox as _owner

sys.modules[__name__] = _owner
