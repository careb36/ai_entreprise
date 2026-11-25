Added

* **A stuck task holds back the rest of its sprint.** When a task has sat in `in_progress` or `in_review` longer than `RUN_WINDOW_STUCK_TASK_SECONDS` (an hour by default; 0 turns it off), the run-window gate queues no new task for that sprint and does not release it from a run-window wait. `run_window_gate.stuck_block_reason` names the stuck tasks, and `dispatch_or_defer` returns that reason as `stuck_block_reason`.
