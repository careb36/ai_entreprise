"""Moved to ``services/workspace/git_workflow.py`` (ADR 0016, issue #469 step 6).

This name is the same module object, so ``from services import git_workflow`` and a
patch of ``services.git_workflow.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.workspace import git_workflow as _owner

sys.modules[__name__] = _owner
