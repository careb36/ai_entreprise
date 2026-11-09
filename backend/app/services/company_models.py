"""Moved to ``services/models/company_models.py`` (ADR 0016, issue #469 step 4).

This name is the same module object, so ``from services import company_models`` and a
patch of ``services.company_models.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.models import company_models as _owner

sys.modules[__name__] = _owner
