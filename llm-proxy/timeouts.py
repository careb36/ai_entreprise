"""How long the proxy waits, every figure derived from ``LLM_UPSTREAM_TIMEOUT_SECONDS``.

The budget of one chat request's provider calls (fallbacks and the retry
without thinking included) is ``LLM_UPSTREAM_TIMEOUT_SECONDS``, 300 s by
default. The other waits follow from it, so they stay in order whatever an
operator sets:

- the proxy stops reading a provider's answer at the budget, even while the
  provider keeps the connection alive with empty lines (DeepSeek does, for a
  non-streaming call: https://api-docs.deepseek.com/quick_start/rate_limit),
  and answers with a named ``upstream_timeout``;
- gunicorn's worker timeout (``gunicorn.conf.py``) is the budget plus
  ``WORKER_TIMEOUT_MARGIN_SECONDS``, so a worker is never killed while the
  proxy can still answer;
- the backend's client (``backend/app/llm_proxy_client.py``) waits the budget
  plus its own margin (15 s), so the proxy's answer, error or not, arrives
  before the client gives up.

300 s, not the 110 s before: a DeepSeek call that reasons first (thinking
mode) on Pro routinely takes over a minute, and 37 s of the planner's
reasoning already went unanswered under the old budget (issue #739).
"""

import math
import os

DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 300.0
# How long to wait for a provider's TCP/TLS connection, within the budget.
CONNECT_TIMEOUT_SECONDS = 10.0
# What gunicorn allows beyond the budget: the proxy's own work and its answer.
WORKER_TIMEOUT_MARGIN_SECONDS = 30


def upstream_timeout_seconds(env=None) -> float:
    """``LLM_UPSTREAM_TIMEOUT_SECONDS``, or the default when unset or not a positive number."""
    env = os.environ if env is None else env
    try:
        value = float(env.get("LLM_UPSTREAM_TIMEOUT_SECONDS", ""))
    except ValueError:
        return DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_UPSTREAM_TIMEOUT_SECONDS


def worker_timeout_seconds(env=None) -> int:
    """gunicorn's ``--timeout``: the upstream budget plus the margin, in whole seconds."""
    return math.ceil(upstream_timeout_seconds(env)) + WORKER_TIMEOUT_MARGIN_SECONDS


def positive_int(env, name: str, default: int) -> int:
    """*name* from *env* as a positive count, else *default*."""
    try:
        value = int(env.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default
