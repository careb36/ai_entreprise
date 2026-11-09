"""Moved to ``services/evidence/app_check.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import app_check`` and a
patch of ``services.app_check.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import app_check as _owner

sys.modules[__name__] = _owner
