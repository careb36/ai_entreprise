Security

* **A check the delivery rewrote no longer judges it.** An every-delivery command (such as `python3 scripts/check_file_sizes.py`) whose own file the delivery changed now refutes the `required_commands` check, whatever it exited. The changed paths are the daemon's report of what it wrote, never the agent's list. In sprint 4 (2026-11-23) a QA task rewrote `scripts/check_file_sizes.py` and `scripts/check_architecture.py`, the two checks that judged it.
