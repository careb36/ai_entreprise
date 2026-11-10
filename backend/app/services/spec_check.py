"""Moved to ``services/planning/spec_check.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import spec_check`` and a
patch of ``services.spec_check.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import spec_check as _owner

sys.modules[__name__] = _owner
