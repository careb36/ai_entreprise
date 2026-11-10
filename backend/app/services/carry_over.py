"""Moved to ``services/loop/carry_over.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import carry_over`` and a
patch of ``services.carry_over.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import carry_over as _owner

sys.modules[__name__] = _owner
