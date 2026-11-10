"""Moved to ``services/release/integration_jobs.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import integration_jobs`` and a
patch of ``services.integration_jobs.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import integration_jobs as _owner

sys.modules[__name__] = _owner
