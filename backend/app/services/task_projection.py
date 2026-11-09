"""Moved to ``services/tasks/task_projection.py`` (ADR 0016, issue #469 step 3).

This name is the same module object, so ``from services import task_projection`` and a
patch of ``services.task_projection.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tasks import task_projection as _owner

sys.modules[__name__] = _owner
