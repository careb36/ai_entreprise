"""Moved to ``services/tools/developer_tools.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import developer_tools`` and a
patch of ``services.developer_tools.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import developer_tools as _owner

sys.modules[__name__] = _owner
