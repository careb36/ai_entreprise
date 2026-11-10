"""Moved to ``services/planning/arch_check.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import arch_check`` and a
patch of ``services.arch_check.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import arch_check as _owner

sys.modules[__name__] = _owner
