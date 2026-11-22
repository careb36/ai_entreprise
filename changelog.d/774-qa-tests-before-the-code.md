Changed

* **QA's extra tests that fail only because the code is not written yet no longer refute its delivery (issue #774).** In QA's acceptance-test task, a failing test that names no criterion is excused when it is in a file the task wrote, every criterion it covers is still unimplemented, and pytest's own summary line says the import of the code failed. The runner's reason per failing test is now kept on the evidence (`test_run.failure_reasons`).
