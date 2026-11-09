"""Moved to ``services/platform/memory_graph.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import memory_graph`` and a
patch of ``services.memory_graph.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import memory_graph as _owner

sys.modules[__name__] = _owner
