"""Moved to ``services/integrations/outbox_service.py`` (ADR 0016, issue #469 step 13).

This name is the same module object, so ``from services import outbox_service`` and a
patch of ``services.outbox_service.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.integrations import outbox_service as _owner

sys.modules[__name__] = _owner
