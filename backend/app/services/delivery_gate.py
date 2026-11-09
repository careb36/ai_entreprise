"""Moved to ``services/evidence/delivery_gate.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import delivery_gate`` and a
patch of ``services.delivery_gate.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import delivery_gate as _owner

sys.modules[__name__] = _owner
