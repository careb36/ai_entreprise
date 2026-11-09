"""Moved to ``services/models/model_record.py`` (ADR 0016, issue #469 step 4).

This name is the same module object, so ``from services import model_record`` and a
patch of ``services.model_record.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.models import model_record as _owner

sys.modules[__name__] = _owner
