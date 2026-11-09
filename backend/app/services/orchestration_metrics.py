"""Moved to ``services/platform/orchestration_metrics.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import orchestration_metrics`` and a
patch of ``services.orchestration_metrics.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import orchestration_metrics as _owner

sys.modules[__name__] = _owner
