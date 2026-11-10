"""The planning context: what is built and in which tasks (ADR 0012, 0016).

The specification and its deterministic check (``spec_author``,
``spec_check``, ``spec_diff``, ``spec_impact``), the architecture and its
check (``arch_author``, ``arch_check``), their approval
(``document_approval``), the PO's sprint plan from a specification
(``spec_planning``), the sprint planner and its team (``sprint_planner``,
``sprint_team``) and the project's stack discovery (``discovery_service``).
It writes plans and documents; it never runs a task. It imports only
``contracts``, ``platform``, ``tasks``, ``models``, ``evidence``,
``workspace`` and ``tools`` among the contexts;
``scripts/check_architecture.py`` keeps it so.
"""
