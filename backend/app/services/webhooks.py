"""Moved to ``services/integrations/webhooks.py`` (ADR 0016, issue #469 step 13).

This name is the same module object, so ``from services import webhooks`` and a
patch of ``services.webhooks.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.integrations import webhooks as _owner

sys.modules[__name__] = _owner
