"""Moved to ``services/management/manager_policy.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import manager_policy`` and a
patch of ``services.manager_policy.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import manager_policy as _owner

sys.modules[__name__] = _owner
