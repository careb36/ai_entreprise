"""Moved to ``services/tools/repo_map.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import repo_map`` and a
patch of ``services.repo_map.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import repo_map as _owner

sys.modules[__name__] = _owner
