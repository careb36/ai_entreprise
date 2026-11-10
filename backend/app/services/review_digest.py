"""Moved to ``services/loop/review_digest.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import review_digest`` and a
patch of ``services.review_digest.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import review_digest as _owner

sys.modules[__name__] = _owner
