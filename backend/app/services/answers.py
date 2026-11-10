"""Moved to ``services/loop/answers.py`` (ADR 0016, issue #469 step 11).

This name is the same module object, so ``from services import answers`` and a
patch of ``services.answers.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.loop import answers as _owner

sys.modules[__name__] = _owner
