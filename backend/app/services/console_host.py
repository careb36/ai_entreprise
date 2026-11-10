"""Moved to ``services/loop/console_host.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import console_host`` and a
patch of ``services.console_host.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import console_host as _owner

sys.modules[__name__] = _owner
