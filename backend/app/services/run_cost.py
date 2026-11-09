"""Moved to ``services/models/run_cost.py`` (ADR 0016, issue #469 step 4).

This name is the same module object, so ``from services import run_cost`` and a
patch of ``services.run_cost.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.models import run_cost as _owner

sys.modules[__name__] = _owner
