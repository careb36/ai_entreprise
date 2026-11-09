"""Moved to ``services/evidence/criteria_tests.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import criteria_tests`` and a
patch of ``services.criteria_tests.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import criteria_tests as _owner

sys.modules[__name__] = _owner
