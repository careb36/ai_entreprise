"""Moved to ``services/producers/agent_worker.py`` (ADR 0016, issue #469 step 10).

This name is the same module object, so ``from services import agent_worker`` and a
patch of ``services.agent_worker.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.producers import agent_worker as _owner

sys.modules[__name__] = _owner
