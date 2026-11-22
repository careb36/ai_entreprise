Changed

* **A plan does not ask QA twice for the same tests (issue #766).** A sprint planned from a specification already gets one acceptance-test task per requirement; a QA task of the PO's plan whose criteria all belong to requirements another task implements is now left out (logged), and a task that waited for it waits for those test tasks. Phase D's bench found the PO planning "Write unit tests for count" over the criteria its acceptance tests covered: QA's test writing took up to three quarters of a run's tokens. A QA task without criteria, such as a performance check, stays.
