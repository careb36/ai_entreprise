"""Moved to ``services/tasks/task_events.py`` (ADR 0016, issue #469 step 3).

This name is the same module object, so ``from services import task_events`` and a
patch of ``services.task_events.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tasks import task_events as _owner

sys.modules[__name__] = _owner
