"""Moved to ``services/workspace/stack_profiles.py`` (ADR 0016, issue #469 step 6).

This name is the same module object, so ``from services import stack_profiles`` and a
patch of ``services.stack_profiles.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.workspace import stack_profiles as _owner

sys.modules[__name__] = _owner
