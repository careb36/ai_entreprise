"""Moved to ``services/tools/tool_history.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import tool_history`` and a
patch of ``services.tool_history.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import tool_history as _owner

sys.modules[__name__] = _owner
