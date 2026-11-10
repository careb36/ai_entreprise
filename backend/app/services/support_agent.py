"""Moved to ``services/management/support_agent.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import support_agent`` and a
patch of ``services.support_agent.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import support_agent as _owner

sys.modules[__name__] = _owner
