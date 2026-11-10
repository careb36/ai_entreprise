"""Moved to ``services/planning/discovery_service.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import discovery_service`` and a
patch of ``services.discovery_service.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import discovery_service as _owner

sys.modules[__name__] = _owner
