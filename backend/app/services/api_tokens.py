"""Moved to ``services/platform/api_tokens.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import api_tokens`` and a
patch of ``services.api_tokens.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import api_tokens as _owner

sys.modules[__name__] = _owner
