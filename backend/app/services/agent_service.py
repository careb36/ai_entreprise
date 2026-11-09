"""Moved to ``services/platform/agent_service.py`` (ADR 0016, issue #469 step 2).

This name is the same module object, so ``from services import agent_service`` and a
patch of ``services.agent_service.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.platform import agent_service as _owner

sys.modules[__name__] = _owner
