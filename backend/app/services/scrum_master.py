"""Moved to ``services/learning/scrum_master.py`` (ADR 0016, issue #469 step 8).

This name is the same module object, so ``from services import scrum_master`` and a
patch of ``services.scrum_master.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.learning import scrum_master as _owner

sys.modules[__name__] = _owner
