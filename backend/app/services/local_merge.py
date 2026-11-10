"""Moved to ``services/release/local_merge.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import local_merge`` and a
patch of ``services.local_merge.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import local_merge as _owner

sys.modules[__name__] = _owner
