"""The tasks context: a task's record (ADR 0012, 0016).

A task's events and their hash chain (``task_events``), the projections that
answer the board's questions (``task_projection``, ``TaskRecord``), the
comment-thread readers (``board``), the event listings (``events``) and the
dependencies between a sprint's tasks (``dependencies``). It records and
answers; it never dispatches, reviews or calls a model. It imports only
``contracts`` and ``platform`` among the contexts;
``scripts/check_architecture.py`` keeps it so.
"""
