Fixed

* **The self-hosting run ends when its sprint is cancelled (issue #701).** The harness's `wait` step left only on a `completed` sprint, so a run whose sprint a person stopped kept waiting until its timeout (run 4 held the one-run slot for up to an hour). A `cancelled` sprint now ends the wait at once and the board is recorded as it stands.
