"""Moved to ``services/loop/edit_plan.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import edit_plan`` and a
patch of ``services.edit_plan.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import edit_plan as _owner

sys.modules[__name__] = _owner
