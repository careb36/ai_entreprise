"""Moved to ``services/loop/sprint_control.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import sprint_control`` and a
patch of ``services.sprint_control.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import sprint_control as _owner

sys.modules[__name__] = _owner
