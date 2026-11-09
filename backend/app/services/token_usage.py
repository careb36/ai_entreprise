"""Moved to ``services/platform/token_usage.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import token_usage`` and a
patch of ``services.token_usage.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import token_usage as _owner

sys.modules[__name__] = _owner
