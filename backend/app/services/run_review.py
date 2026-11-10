"""Moved to ``services/loop/run_review.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import run_review`` and a
patch of ``services.run_review.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import run_review as _owner

sys.modules[__name__] = _owner
