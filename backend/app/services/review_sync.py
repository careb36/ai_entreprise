"""Moved to ``services/release/review_sync.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import review_sync`` and a
patch of ``services.review_sync.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import review_sync as _owner

sys.modules[__name__] = _owner
