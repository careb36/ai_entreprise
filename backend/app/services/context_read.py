"""Moved to ``services/evidence/context_read.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import context_read`` and a
patch of ``services.context_read.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import context_read as _owner

sys.modules[__name__] = _owner
