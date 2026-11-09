"""Moved to ``services/evidence/acceptance.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import acceptance`` and a
patch of ``services.acceptance.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import acceptance as _owner

sys.modules[__name__] = _owner
