"""Moved to ``services/tasks/dependencies.py`` (ADR 0016, issue #469 step 3).

This name is the same module object, so ``from services import dependencies`` and a
patch of ``services.dependencies.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tasks import dependencies as _owner

sys.modules[__name__] = _owner
