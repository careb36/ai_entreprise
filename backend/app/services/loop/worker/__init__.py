"""The task worker's stages (ADR 0014), part of the loop context.

``services/loop/task_worker.py`` is split by loop stage, one module per pull
request, each owning its functions and the tests' patch targets: ``messages``
(agent messages and the ScrumMaster's triage), then ``intake``, ``prompt``,
``execute``, ``deliver``, ``record``, ``review`` (with ``qa_review``, QA's
review of one task, and ``quality_gate``, the text checks before a delivery)
and ``close`` (with ``procedures_round``, the procedures round a close runs).
``board_writes`` is shared by the stages: the writers every transition of a
backlog item goes through. ``task_worker`` keeps re-exporting the names until
nothing imports them.
"""
