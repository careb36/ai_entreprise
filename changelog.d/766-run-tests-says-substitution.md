Fixed

* **`workspace__run_tests` says when it ran another command than the one asked for.** A command off the folder's test runner was silently replaced by the folder's own test command, and the reply showed that run's output under "exit status N". In sprint 6 (2026-11-23) a QA asked for `python scripts/check_file_sizes.py`, got the full suite's output, and reported a traceback nothing had produced. The reply now starts by naming the command not run and the one run instead, and says the every-delivery checks are run by the platform after delivery.
