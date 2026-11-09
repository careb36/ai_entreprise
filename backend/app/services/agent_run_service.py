"""Moved to ``services/evidence/agent_run_service.py`` (ADR 0016, issue #469 step 5).

This name is the same module object, so ``from services import agent_run_service`` and a
patch of ``services.agent_run_service.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.evidence import agent_run_service as _owner

sys.modules[__name__] = _owner
