"""Moved to ``services/tools/research_registries.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import research_registries`` and a
patch of ``services.research_registries.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import research_registries as _owner

sys.modules[__name__] = _owner
