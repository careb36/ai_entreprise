"""Moved to ``services/evidence/secops_checks.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import secops_checks`` and a
patch of ``services.secops_checks.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import secops_checks as _owner

sys.modules[__name__] = _owner
