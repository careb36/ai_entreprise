"""Moved to ``services/tools/developer_tool_schemas.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import developer_tool_schemas`` and a
patch of ``services.developer_tool_schemas.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import developer_tool_schemas as _owner

sys.modules[__name__] = _owner
