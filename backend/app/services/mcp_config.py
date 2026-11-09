"""Moved to ``services/tools/mcp_config.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import mcp_config`` and a
patch of ``services.mcp_config.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import mcp_config as _owner

sys.modules[__name__] = _owner
