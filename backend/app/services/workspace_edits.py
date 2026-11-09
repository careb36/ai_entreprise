"""Moved to ``services/tools/workspace_edits.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import workspace_edits`` and a
patch of ``services.workspace_edits.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import workspace_edits as _owner

sys.modules[__name__] = _owner
