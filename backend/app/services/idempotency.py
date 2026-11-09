"""Moved to ``services/platform/idempotency.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import idempotency`` and a
patch of ``services.idempotency.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import idempotency as _owner

sys.modules[__name__] = _owner
