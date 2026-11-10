"""Moved to ``services/loop/company_service.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import company_service`` and a
patch of ``services.company_service.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import company_service as _owner

sys.modules[__name__] = _owner
