"""Moved to ``services/loop/sprint_archive.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import sprint_archive`` and a
patch of ``services.sprint_archive.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import sprint_archive as _owner

sys.modules[__name__] = _owner
