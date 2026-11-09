"""Moved to ``services/platform/engram_processor.py`` (ADR 0015, 0016, issue #469 step 2).

This name is the same module object, so ``from memory import engram_processor``
and a patch of ``memory.engram_processor.X`` reach the owner. Removed once
nothing imports it.
"""

import sys

from services.platform import engram_processor as _owner

sys.modules[__name__] = _owner
