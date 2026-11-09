"""Moved to ``services/tasks/events.py`` (ADR 0016, issue #469 step 3).

This name is the same module object, so ``from services import events`` and a
patch of ``services.events.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tasks import events as _owner

sys.modules[__name__] = _owner
