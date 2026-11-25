Fixed

* **A cut test output keeps the lines that name a criterion or a failure (issue #701).** Fitting a `workspace.execute` reply into one NATS message kept the start and the end of a verbose suite, so `test_req_1_1_… PASSED`, printed in the middle of 7500 tests, was left out and the criteria check reported "no test ran for REQ-1.1" (self-hosting run 7, task 48). The daemon now keeps, from the part it leaves out, every line naming a criterion (`req_N`) and every `FAILED` or `ERROR` line, bounded to 256 KiB.
