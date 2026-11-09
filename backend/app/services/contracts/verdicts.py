"""The verdict names a verification records, read by every context that judges a run.

``orchestration_metrics`` re-exports them for the importers not moved yet
(ADR 0013).
"""

VERDICT_VERIFIED = "verified"
VERDICT_REFUTED = "refuted"
VERDICT_UNVERIFIABLE = "unverifiable"
