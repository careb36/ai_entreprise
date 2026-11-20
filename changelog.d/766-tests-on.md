Changed

* **The project's tests run after every delivery by default, in its own container, and the Developer can run them (issue #766).** `DELIVERY_TESTS_ENABLED` and `DELIVERY_SANDBOX_ENV` now default to `true`. A trial of this repository's suites in the stack image showed what stood in the way, and each is fixed:
  - each task starts from a worktree with nothing installed, and installs ran only when a delivery changed a manifest: in container or bwrap mode, a folder (or the root) never installed under that worktree is now installed before its first suite, whatever the delivery wrote, and the company's `dependency_installs` keep a `<folder>/` or `./` mark per worktree and mode;
  - a Poetry virtualenv left with the install's container: the environment volume, now on, keeps it;
  - the daemon stopped a suite after 120 s: `DELIVERY_TEST_RUN_SECONDS` (600, at most 900) bounds the root's test run, each folder's suite and `run_tests`;
  - `pytest -n auto` started a worker per host core in a 2-CPU container: the daemon sets `PYTEST_XDIST_AUTO_NUM_WORKERS` to the CPU limit;
  - in a repository whose root has no suite, the folders' suites now count as the delivery's test observation (required checks policy `v1`);
  - the Developer's `workspace__run_tests` was offered only with a root test command: it now has a `folder` parameter listing the folders' suites, keeps a wrapper such as `poetry run` on a narrower command, and installs a folder before its first run in an isolated sandbox (`services/tools/test_runs.py`).

  The backend's tests no longer need `PYTHONPATH` (`[tool.pytest.ini_options] pythonpath` in `backend/pyproject.toml`).

  **Upgrading:** remove `DELIVERY_TESTS_ENABLED=false` from `.env`, or keep it with the Owner's `tests_off` acknowledgement. The first delivery of each task installs its folders (network on, as `DELIVERY_INSTALL_NETWORK` says) before the tests.
