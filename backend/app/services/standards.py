"""Moved to ``services/evidence/standards.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import standards`` and a
patch of ``services.standards.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import standards as _owner

sys.modules[__name__] = _owner
