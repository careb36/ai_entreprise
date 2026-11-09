"""The platform context: infrastructure every context may use (ADR 0012, 0016).

Audit writes, advisory locks, idempotency claims, the Glassroom publisher,
KPI turn records, token usage, API tokens, run secrets, the health check, the
knowledge graph reader and the engram processor (moved from
``backend/memory``, ADR 0015). It imports only ``contracts`` among the
contexts and knows nothing of tasks, runs or companies' decisions;
``scripts/check_architecture.py`` keeps it so.
"""
