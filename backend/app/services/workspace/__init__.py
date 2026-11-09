"""The workspace context: the client of the workspace daemon (ADR 0012, 0016).

Requests to the daemon over NATS and their errors (``workspace_daemon``), the
project's stack profile as the daemon's snapshot shows it with the company's
overrides (``stack_profiles``, ``project_profile``), the dependency install
before a delivery's tests (``dependency_install``), the sandbox modes
(``sandbox``, ``sandbox_manager``), the project snapshot
(``workspace_snapshot``) and the Git workflow's delivery spec
(``git_workflow``). It sends requests and returns observations; it never
judges them. It imports only ``contracts`` and ``platform`` among the
contexts; ``scripts/check_architecture.py`` keeps it so.
"""
