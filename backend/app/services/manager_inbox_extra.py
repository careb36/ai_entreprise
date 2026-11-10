"""Moved to ``services/management/manager_inbox_extra.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import manager_inbox_extra`` and a
patch of ``services.manager_inbox_extra.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import manager_inbox_extra as _owner

sys.modules[__name__] = _owner
