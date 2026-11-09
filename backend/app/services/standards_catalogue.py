"""Moved to ``services/contracts/standards_catalogue.py`` (ADR 0013, 0016).

This name is the same module object, so ``from services import standards_catalogue`` and a
patch of ``services.standards_catalogue.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.contracts import standards_catalogue as _owner

sys.modules[__name__] = _owner
