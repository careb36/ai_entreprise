"""The loop context: driving a sprint's tasks (ADR 0012, 0016).

The task worker (dispatch, prompt, execution, delivery, recording, review and
closing, ``task_worker``, being split by stage into ``worker/``, ADR 0014) with the pieces it is made of: reading a model's
answer (``answers``), the checks after a delivery (``delivery_checks``), what
QA is shown (``review_digest``), the Architect's edit plan (``edit_plan``) and
the console producer's host (``console_host``); the moves a person makes on
the board (``board_moves``, ``carry_over``, ``run_review``), who decides when
a sprint's agents work (``sprint_control``), how it closes and is archived
(``sprint_close``, ``sprint_archive``), several replicas over one database
(``replicas``) and the company's bootstrap (``company_service``). It decides
what happens next from the evidence and never computes a verdict itself.
What leaves the factory once a sprint closes starts from the ``sprint.closed``
event (``platform.hooks``), never from a call into ``release``. It imports
only ``contracts``, ``platform``, ``tasks``, ``models``, ``evidence``,
``workspace``, ``tools``, ``learning``, ``planning`` and ``producers`` among
the contexts; ``scripts/check_architecture.py`` keeps it so.
"""
