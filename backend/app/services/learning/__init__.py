"""The learning context: how the company learns from its sprints (ADR 0012, 0016).

The procedures, kept or reverted by the board's numbers after two sprints
(``procedures``), a closed sprint's health for its retrospective
(``retrospective``), the ScrumMaster's triage of an escalation and its
procedure proposals (``scrum_master``) and the Architect's record of its
briefs (``architect``). It reads the task record and asks models through the
proxy; it never moves a task, and the numbers decide, never a model. It
imports only ``contracts``, ``platform``, ``tasks`` and ``models`` among the
contexts; ``scripts/check_architecture.py`` keeps it so.
"""
