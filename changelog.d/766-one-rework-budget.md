Changed

* **One rework budget per task, QA shown the tests, and reworks that ask for edits (issue #766).** Sprint 3's task 18 was sent back nine times: the quality gate, the delivery gate and QA each counted their own two requests, QA never saw whether the tests passed, and every rework asked for every file in full, so each attempt rewrote what the branch already held.
  - `MAX_REWORKS` (default 3) counts every cause's `NEEDS WORK:` since a person's send-back (`TaskRecord.reworks`); when it is spent the task is blocked and escalated to the ScrumMaster. `AUTOPILOT_MAX_REWORK_CYCLES` is read for one release, with a warning, when `MAX_REWORKS` is unset.
  - QA's prompt carries what the platform observed (`worker/review_evidence`): the verification's verdict, each check's outcome and observation, and the end of the root's test output and of each folder's suite, marked as observations a review does not settle.
  - The delivery gate and the quality gate ask the agent to change only what the problem needs on the task branch (with `workspace__edit` where offered) and to deliver only the files it changes.

  **Upgrading:** a `.env` that sets `AUTOPILOT_MAX_REWORK_CYCLES` should set `MAX_REWORKS` instead; the old name is read until the next release.
