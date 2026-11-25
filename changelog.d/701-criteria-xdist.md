Fixed

* **The criteria check reads pytest-xdist's verbose lines (issue #701).** The backend's suite runs under pytest-xdist by default since #592, and its `-v` lines put the outcome before the test, behind the worker (`[gw5] [ 45%] PASSED path::name`). The criteria check read only `path::name PASSED` and the short summary, so every backend acceptance test was reported `missing`, passing or not (self-hosting run 7, task 49, whose `test_req_2_1_…` passed). Those lines now read like the short summary.
