"""Moved to ``services/platform/glassroom.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import glassroom`` and a
patch of ``services.glassroom.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import glassroom as _owner

sys.modules[__name__] = _owner
