"""Moved to ``services/management/reviewer_agent.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import reviewer_agent`` and a
patch of ``services.reviewer_agent.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import reviewer_agent as _owner

sys.modules[__name__] = _owner
