"""Moved to ``services/release/git_setup.py`` (ADR 0016, issue #469 step 12).

This name is the same module object, so ``from services import git_setup`` and a
patch of ``services.git_setup.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.release import git_setup as _owner

sys.modules[__name__] = _owner
