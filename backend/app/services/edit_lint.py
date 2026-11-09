"""Moved to ``services/tools/edit_lint.py`` (ADR 0016, issue #469 step 7).

This name is the same module object, so ``from services import edit_lint`` and a
patch of ``services.edit_lint.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.tools import edit_lint as _owner

sys.modules[__name__] = _owner
