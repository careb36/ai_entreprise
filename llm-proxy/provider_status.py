"""
What the proxy last observed of each deployment provider.

Each entry is an observation, never a claim: the class of the provider's own
answer (``classify``), when it was seen, the HTTP status, and where it was
seen (``startup``, ``refresh`` or a ``request``). ``checked`` says what the
observation can prove: ``key`` for an endpoint that only authenticates (a
model list), ``key_and_credit`` for a billable call or a served request.

No key ever enters an entry: ``detail`` is the provider's message with the key
and anything key-shaped replaced (``redact``), cut to 200 characters.

The credit markers here are wider than ``upstream.CREDIT_PHRASES`` on purpose:
those decide whether a request may fall back to another provider, and that
behaviour stays as it was; these only name what the provider said.
"""

import re
import threading
import time
from datetime import UTC, datetime

PROVIDERS = ("gemini", "deepseek", "qwen", "nebius")

# ok: the provider answered 2xx. invalid_key: 401/403 (a key or an access).
# no_credit: 402, or the provider's own quota/billing error. rate_limited: a
# 429 that is a per-minute limit. unreachable: no answer, or 5xx.
# unexpected: any other answer (the detail says what). not_configured: no key,
# or a placeholder from .env.example.
STATUSES = ("ok", "invalid_key", "no_credit", "rate_limited", "unreachable", "unexpected",
            "not_configured")

# Lower-cased markers of an account without credit, by provider:
# DeepSeek answers 402 "Insufficient Balance"; Qwen (DashScope) "Arrearage",
# "AllocationQuota.FreeTierOnly" and "Prepaid/PostpaidBillOverdue"; Gemini a
# RESOURCE_EXHAUSTED "exceeded your current quota ... billing details" or
# "enable billing"; Nebius and the OpenAI-compatible APIs 402 or
# "insufficient_quota".
CREDIT_MARKERS = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient credit",
    "insufficient funds",
    "not enough funds",
    "insufficient_quota",
    "insufficient quota",
    "credit balance",
    "out of credit",
    "credits exhausted",
    "account balance",
    "balance is not enough",
    "arrearage",
    "freetieronly",
    "free tier of the model has been exhausted",
    "billoverdue",
    "exceeded your current quota",
    "payment required",
    "billing",
)

# A 429 that names a per-minute (or per-second) limit is a rate limit, even
# when Gemini words it as "exceeded your current quota".
RATE_MARKERS = ("perminute", "per minute", "persecond", "per second", "rate limit")

_PLACEHOLDER_WORDS = {"changeme", "change_me", "replace_me", "replaceme", "xxx", "none", "null",
                      "todo", "placeholder"}


def is_placeholder(value: str) -> bool:
    """Whether *value* is a placeholder such as ``.env.example``'s ``your_…_api_key``."""
    v = (value or "").strip()
    if not v:
        return False
    low = v.lower()
    angle = v.startswith("<") and v.endswith(">")
    return low.startswith(("your_", "your-")) or angle or low in _PLACEHOLDER_WORDS


def classify(http_status: int | None, text: str) -> str:
    """The class of a provider's answer; ``None`` means it never answered."""
    if http_status is None:
        return "unreachable"
    low = (text or "").lower()
    if 200 <= http_status < 300:
        return "ok"
    if http_status == 429 and any(m in low for m in RATE_MARKERS):
        return "rate_limited"
    if http_status == 402 or any(m in low for m in CREDIT_MARKERS):
        return "no_credit"
    if http_status in (401, 403) or "invalid_api_key" in low:
        return "invalid_key"
    if http_status == 429:
        return "rate_limited"
    if http_status >= 500:
        return "unreachable"
    return "unexpected"


_KEY_SHAPED = re.compile(r"(?:sk-|AIza)[A-Za-z0-9_\-*.]+")


def redact(text: str, key: str = "") -> str:
    """*text* without *key* or anything key-shaped, cut to 200 characters."""
    out = str(text or "")
    if key:
        out = out.replace(key, "[redacted]")
    return _KEY_SHAPED.sub("[redacted]", out)[:200]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Board:
    """The latest observation per provider; safe across gunicorn threads."""

    def __init__(self, providers=PROVIDERS):
        self._lock = threading.Lock()
        self._seen: dict[str, dict] = {p: {"status": "not_observed"} for p in providers}

    def record(self, provider: str, status: str, *, http_status: int | None, source: str,
               checked: str | None = None, detail: str | None = None) -> None:
        entry = {"status": status, "observed_at": _now(), "http_status": http_status,
                 "source": source, "checked": checked}
        if detail:
            entry["detail"] = detail
        with self._lock:
            self._seen[provider] = entry

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {p: dict(e) for p, e in self._seen.items()}


class RefreshLimiter:
    """At most one on-demand check every *interval* seconds (each costs a call)."""

    def __init__(self, interval: float, clock=time.monotonic):
        self.interval = interval
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def try_acquire(self) -> tuple[bool, float]:
        """``(allowed, retry_after_seconds)``."""
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self.interval:
                return False, round(self.interval - (now - self._last), 1)
            self._last = now
            return True, 0.0
