"""Moved to ``services/loop/task_worker.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import task_worker`` and a
patch of ``services.task_worker.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import task_worker as _owner

sys.modules[__name__] = _owner
