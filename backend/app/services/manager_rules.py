"""Moved to ``services/management/manager_rules.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import manager_rules`` and a
patch of ``services.manager_rules.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import manager_rules as _owner

sys.modules[__name__] = _owner
