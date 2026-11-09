"""Moved to ``services/models/model_routing.py`` (ADR 0016, issue #469 step 4).

This name is the same module object, so ``from services import model_routing`` and a
patch of ``services.model_routing.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.models import model_routing as _owner

sys.modules[__name__] = _owner
