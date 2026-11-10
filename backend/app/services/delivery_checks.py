"""Moved to ``services/loop/delivery_checks.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import delivery_checks`` and a
patch of ``services.delivery_checks.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import delivery_checks as _owner

sys.modules[__name__] = _owner
