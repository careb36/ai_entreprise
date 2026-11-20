Fixed

* **A transient model failure no longer blocks the task (issue #766).** A provider that did not answer within the budget (`upstream_timeout`), a call the proxy dropped while it restarted (`proxy_connection_lost`), a rate limit, a 5xx or an empty answer blocked the task, and the automatic retry of blocked tasks did not take it back: its text is not one the projection reads as retryable, and an earlier rework request already marks the task for a person. The worker now queues such a task again with a `retried` event, at most three times, and blocks it only after that.
