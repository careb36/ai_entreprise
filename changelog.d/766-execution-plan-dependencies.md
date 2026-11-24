Fixed

* **A plan from a prompt keeps the order between its tasks.** The PO's execution and self-improvement plans now ask for each task's `depends_on` (the titles of the tasks that must be done first) and read it as the specification planner does: by title, as written or as cut, or by position; unknown ones and self dropped, a cycle broken. `start_sprint` then holds the task until those are done. In sprint 6 (2026-11-23) the QA task that validated a split started before the split existed, and spent reworks refuting work that had not been done yet.
