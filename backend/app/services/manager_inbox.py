"""Moved to ``services/management/manager_inbox.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import manager_inbox`` and a
patch of ``services.manager_inbox.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import manager_inbox as _owner

sys.modules[__name__] = _owner
