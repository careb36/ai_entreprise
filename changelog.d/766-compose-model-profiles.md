Fixed

* **`MODEL_PROFILES` reaches the backend container.** `docker-compose.yml` did not pass it, so a per-model override set in `.env` (such as a larger `result_max_chars`) had no effect. Unset, it is `{}`.
