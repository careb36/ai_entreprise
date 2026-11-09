"""Moved to ``services/workspace/workspace_daemon.py`` (ADR 0016, issue #469 step 6).

This name is the same module object, so ``from services import workspace_daemon`` and a
patch of ``services.workspace_daemon.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.workspace import workspace_daemon as _owner

sys.modules[__name__] = _owner
