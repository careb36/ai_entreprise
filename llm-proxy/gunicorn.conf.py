"""gunicorn's settings for the proxy, read from the environment at start.

gunicorn loads ``./gunicorn.conf.py`` from its working directory (``/app``).

- ``timeout`` is ``LLM_UPSTREAM_TIMEOUT_SECONDS`` plus a margin
  (``timeouts.worker_timeout_seconds``), so the proxy's own upstream deadline
  always fires first and the caller gets a named ``upstream_timeout``.
- ``worker_class = "gthread"`` with ``LLM_PROXY_THREADS`` threads per worker
  (8 by default): a chat call spends its time waiting on the provider, so one
  long thinking call holds one thread, not a whole worker; with the sync
  worker, two such calls stalled every other request, ``/health`` included.
  In a gthread worker the timeout is the worker's heartbeat, not a request's,
  so a long call is bounded by the proxy's own deadline instead of a kill
  mid-read. The proxy's shared state is already guarded by locks
  (``catalogue``, ``provider_status``) and its database pool is thread-safe.
"""

import os

import timeouts

# Fixed, as before: compose maps 5100 and ``PROXY_PORT`` is only for ``python app.py``.
bind = "0.0.0.0:5100"
workers = timeouts.positive_int(os.environ, "LLM_PROXY_WORKERS", 2)
worker_class = "gthread"
threads = timeouts.positive_int(os.environ, "LLM_PROXY_THREADS", 8)
timeout = timeouts.worker_timeout_seconds()
graceful_timeout = timeout
