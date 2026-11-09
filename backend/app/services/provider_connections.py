"""Moved to ``services/models/provider_connections.py`` (ADR 0016, issue #469 step 4).

This name is the same module object, so ``from services import provider_connections`` and a
patch of ``services.provider_connections.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.models import provider_connections as _owner

sys.modules[__name__] = _owner
