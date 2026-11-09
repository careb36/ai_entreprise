"""Moved to ``services/evidence/review_changes.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import review_changes`` and a
patch of ``services.review_changes.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import review_changes as _owner

sys.modules[__name__] = _owner
