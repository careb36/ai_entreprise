"""The task worker's stages (ADR 0014), part of the loop context.

``services/loop/task_worker.py`` is split by loop stage, each module owning its
functions and the tests' patch targets: ``intake`` (the consumer, the claim,
queue markers and dispatch, with ``task_run``, one claimed task up to QA's
handoff), ``prompt``, ``execute``, ``deliver``, ``record``, ``review`` (with
``qa_review``, QA's review of one task, and ``quality_gate``, the text checks
before a delivery), ``close`` (with ``procedures_round``, the procedures round
a close runs) and ``messages`` (agent messages and the ScrumMaster's triage).
``board_writes`` is shared by the stages: the writers every transition of a
backlog item goes through. The split is complete; ``task_worker`` is the
facade that keeps re-exporting the names until nothing imports them.
"""
