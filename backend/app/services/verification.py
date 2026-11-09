"""Moved to ``services/evidence/verification.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import verification`` and a
patch of ``services.verification.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import verification as _owner

sys.modules[__name__] = _owner
