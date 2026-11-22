Changed

* **A test that already failed in a shared folder is not the delivery's failure (issue #774).** Without the Git workflow, before a delivery's tests run the worker records which tests failed in the folder's latest run for another task whose delivery is still there; the Verifier's `tests_run` passes when every failure is one of those (in a file the delivery did not write) or an excused acceptance test, and names that run. An acceptance test of a criterion the task implements, a test file it wrote, or a cut list of failures is never excused.
