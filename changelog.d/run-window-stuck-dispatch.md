- The run-window gate now refuses to dispatch a sprint's new tasks when a
  prior task has been in a non-terminal async state (``in_progress`` or
  ``in_review``) longer than ``RUN_WINDOW_STUCK_TASK_SECONDS``, so a stuck
  run stops the backlog from growing. ``run_window_gate.stuck_block_reason``
  returns the reason, and ``run_window_gate.dispatch_or_defer`` exposes it
  as ``stuck_block_reason`` while refusing to queue tasks.
