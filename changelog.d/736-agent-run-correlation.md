Changed

* **An agent run records the correlation id of the assignment that started it.** The task worker passes the `correlation_id` the outbox gave the `task.assign` on to the `AgentRun` it records, so a run can be traced back to its outbox row and the worker's log lines. `POST /v1/agent_runs` keeps an optional `correlation_id` too. Runs without one, or with a value that is not a UUID, keep it empty.
