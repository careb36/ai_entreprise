Fixed

* **A failed run's kept output shows which tests failed.** The test run and every folder command kept the last 2000 characters of `stdout + stderr`, which was stderr alone when stderr was long. In sprint 4 (2026-11-23) every refuted run showed pytest-asyncio's deprecation warning and never the failing test, so each rework was blind. `workspace_daemon.output_tail` now keeps the end of stdout last, with up to a quarter of the room for the end of stderr; output that fits is kept whole.
