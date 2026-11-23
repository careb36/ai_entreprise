Changed

* **A Developer may read up to 400 lines in one call.** `workspace__read` takes `lines` (default 100, at most 400 in the daemon, still bounded by the model's result budget). With 100-line windows and five tool rounds a Developer saw at most 500 lines of a file: in sprint 6 (2026-11-23) it could not split a 642-line test file. The daemon must be restarted to serve the new window; an older daemon ignores `lines` and keeps 100.
