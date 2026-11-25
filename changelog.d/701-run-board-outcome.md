Added

* **An owner action run records how its sprint's tasks ended (issue #701).** `self_host_container_run` succeeds when the sprint ran in container mode, which said nothing of its work: run 9 read "succeeded" with three of four tasks blocked. The run's observations now carry `sprint_tasks` (tasks per status, from the harness's board), and the run panel shows tasks done, blocked and not finished, with the blocked count beside the status.
