- The run-window gate now refuses to dispatch a sprint's new tasks when a
  prior task has been in a non-terminal async state (``in_progress`` or
  ``in_review``) longer than ``RUN_WINDOW_STUCK_TASK_SECONDS``, so a stuck
  run stops the backlog from growing. The refusal is exposed by
  ``run_window_gate.stuck_block_reason``.
