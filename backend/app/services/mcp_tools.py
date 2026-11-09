"""Moved to ``services/tools/mcp_tools.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import mcp_tools`` and a
patch of ``services.mcp_tools.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import mcp_tools as _owner

sys.modules[__name__] = _owner
