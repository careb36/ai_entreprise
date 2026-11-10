"""The release context: what leaves the factory once accepted (ADR 0012, 0016).

Publishing a task's branch with its evidence (``git_publish``) and bringing
the request's comments back (``review_sync``), merging into a local
repository (``local_merge``), a sprint's integration (``integration``) with
its jobs and retries (``integration_jobs``), its events on each task
(``integration_events``) and its publication (``integration_publish``),
undoing a delivery from its snapshot (``delivery_undo``) and the Git
workflow's set-up from what the daemon observed (``git_setup``). It starts
when the loop has closed work, through the ``sprint.closed`` event
(``platform.hooks``, registered by ``integration_jobs.install``), never from
inside the loop. It imports only ``contracts``, ``platform``, ``tasks``,
``models``, ``evidence``, ``workspace``, ``planning`` and ``loop`` among the
contexts; ``scripts/check_architecture.py`` keeps it so.
"""
