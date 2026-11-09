"""Moved to ``services/models/model_factory.py`` (ADR 0015, 0016, issue #469 step 4).

This name is the same module object, so ``from llm.model_factory import X``
and a patch of ``llm.model_factory.X`` reach the owner. Removed once nothing
imports it.
"""

import sys

from services.models import model_factory as _owner

sys.modules[__name__] = _owner
