"""Moved to ``services/evidence/governance.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import governance`` and a
patch of ``services.governance.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import governance as _owner

sys.modules[__name__] = _owner
