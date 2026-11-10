"""Moved to ``services/planning/sprint_planner.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import sprint_planner`` and a
patch of ``services.sprint_planner.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import sprint_planner as _owner

sys.modules[__name__] = _owner
