"""Moved to ``services/learning/retrospective.py`` (ADR 0016, issue #469 step 8).

This name is the same module object, so ``from services import retrospective`` and a
patch of ``services.retrospective.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.learning import retrospective as _owner

sys.modules[__name__] = _owner
