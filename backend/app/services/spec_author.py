"""Moved to ``services/planning/spec_author.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import spec_author`` and a
patch of ``services.spec_author.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import spec_author as _owner

sys.modules[__name__] = _owner
