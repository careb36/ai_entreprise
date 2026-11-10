"""Moved to ``services/learning/architect.py`` (ADR 0016, issue #469 step 8).

This name is the same module object, so ``from services import architect`` and a
patch of ``services.architect.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.learning import architect as _owner

sys.modules[__name__] = _owner
