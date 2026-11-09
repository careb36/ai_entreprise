"""Moved to ``services/platform/run_secrets.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import run_secrets`` and a
patch of ``services.run_secrets.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import run_secrets as _owner

sys.modules[__name__] = _owner
