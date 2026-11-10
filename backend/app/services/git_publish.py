"""Moved to ``services/release/git_publish.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import git_publish`` and a
patch of ``services.git_publish.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import git_publish as _owner

sys.modules[__name__] = _owner
