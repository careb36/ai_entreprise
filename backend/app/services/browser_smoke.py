"""Moved to ``services/evidence/browser_smoke.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import browser_smoke`` and a
patch of ``services.browser_smoke.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import browser_smoke as _owner

sys.modules[__name__] = _owner
