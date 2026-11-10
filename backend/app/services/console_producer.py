"""Moved to ``services/producers/console_producer.py`` (ADR 0016, issue #469 step 10).

This name is the same module object, so ``from services import console_producer`` and a
patch of ``services.console_producer.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.producers import console_producer as _owner

sys.modules[__name__] = _owner
