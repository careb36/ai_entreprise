"""Moved to ``services/producers/github_adapter.py`` (ADR 0016, issue #469 step 10).

This name is the same module object, so ``from services import github_adapter`` and a
patch of ``services.github_adapter.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.producers import github_adapter as _owner

sys.modules[__name__] = _owner
