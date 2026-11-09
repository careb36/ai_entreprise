"""Moved to ``services/platform/healthcheck.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import healthcheck`` and a
patch of ``services.healthcheck.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import healthcheck as _owner

sys.modules[__name__] = _owner
