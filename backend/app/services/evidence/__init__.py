"""The evidence context: judging a run from what was observed (ADR 0012, 0016).

The Verifier's checks and verdict (``verification``), the risk tier
(``risk``), the quality standards check (``standards``), the acceptance tests
per criterion (``acceptance``, ``criteria_tests``), the context-read check
(``context_read``), the application and browser checks as read from the
daemon's reply (``app_check``, ``browser_smoke``), the sources a run consulted
(``sources``), a console producer's run (``console_run``), SecOps scans
(``secops_checks``), the gate before QA (``delivery_gate``), the changes of a
run (``review_changes``), governance and the ingestion of AgentRun records
(``agent_run_service``). It imports only ``contracts`` and ``platform`` among
the contexts: the daemon calls whose replies it judges are the loop's
(``services/delivery_checks.py``); ``scripts/check_architecture.py`` keeps it
so.
"""
