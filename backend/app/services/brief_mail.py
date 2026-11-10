"""Moved to ``services/management/brief_mail.py`` (ADR 0016, issue #469 step 14).

This name is the same module object, so ``from services import brief_mail`` and a
patch of ``services.brief_mail.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.management import brief_mail as _owner

sys.modules[__name__] = _owner
