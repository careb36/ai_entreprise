Fixed

* **The e2e counts what the loop does now (issue #766).** It runs with `MAX_REWORKS=2`, the budget its foundation sprint's checks count (the deployment's default is 3 since the one rework budget), and its Architect checks count QA's retried document beside the new task, since QA consults the Architect too (issue #750). Both e2e jobs had failed on these three checks since; the run passes in full on the host sandbox.
