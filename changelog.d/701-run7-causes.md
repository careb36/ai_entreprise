Fixed

* **A self-hosting run observes the container sandbox of the folders' suites, and stops a sprint it leaves at the timeout (issue #701).** The `sandbox` step read the daemon's reply only on `test_run.sandbox`; with folder suites (La Fabrique's root has none) the test run is their summary and each suite's reply is on its `command_runs` record, so runs 4 to 7 were labelled `not_container_mode` although every suite ran in the container. And a run whose wait reached its timeout left its sprint active (run 7, sprint 12); it now stops it and fails with `sprint_not_completed: <status>`.
