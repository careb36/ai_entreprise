"""Moved to ``services/planning/document_approval.py`` (ADR 0016, issue #469 step 9).

This name is the same module object, so ``from services import document_approval`` and a
patch of ``services.document_approval.X`` reach the owner. Removed once nothing imports it.
"""

import sys

from services.planning import document_approval as _owner

sys.modules[__name__] = _owner
