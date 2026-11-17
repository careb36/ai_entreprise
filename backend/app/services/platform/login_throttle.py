"""A backoff per account on password sign-in (issue #541, WSTG-v42-ATHN-03).

slowapi limits ``POST /v1/auth/login`` per client address
(``LOGIN_RATE_LIMIT``); this module adds the limit per account, which an
attacker spreading guesses over many addresses still meets. After
``LOGIN_FAILURES_BEFORE_BACKOFF`` failures within
``LOGIN_FAILURE_WINDOW_SECONDS``, each further failure locks the account's
sign-in for twice as long as the previous one (2 s, 4 s, 8 s, ...), at most
``LOGIN_BACKOFF_MAX_SECONDS``; a success clears the record.

The key is the sha256 of the normalised email, whether or not an account
exists, so the lock itself says nothing about which addresses are
registered (WSTG-v42-IDNT-04), and no address is kept in memory. A locked
account is refused before its password is checked, so guessing does not go
on during the lock. The trade-off is that someone who knows an address can
keep its owner's password sign-in locked for up to the maximum, while they
keep failing; SSO and existing sessions are unaffected.

At most ``MAX_ACCOUNTS`` records are kept. Making room drops a record
without a running lock first, so guesses spread over many other addresses
cannot push the attacked account's lock out of memory (``_make_room``).

The record is per process, like slowapi's default storage
(``RATE_LIMIT_STORAGE_URI``): with several replicas, each keeps its own.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

MAX_ACCOUNTS = 10_000

_lock = threading.Lock()


@dataclass
class _Record:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


_records: OrderedDict[str, _Record] = OrderedDict()


def _settings():
    from config import settings

    return settings


def _key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def retry_after(email: str, now: float | None = None) -> int:
    """Seconds until *email* may try again; 0 when it may now."""
    now = time.monotonic() if now is None else now
    with _lock:
        record = _records.get(_key(email))
        if record is None or record.locked_until <= now:
            return 0
        return max(1, int(record.locked_until - now + 0.999))


def failure(email: str, now: float | None = None) -> int:
    """Record a failed sign-in for *email*; the lock it now has, in seconds."""
    settings = _settings()
    now = time.monotonic() if now is None else now
    window = float(settings.LOGIN_FAILURE_WINDOW_SECONDS)
    allowed = int(settings.LOGIN_FAILURES_BEFORE_BACKOFF)
    key = _key(email)
    with _lock:
        record = _records.pop(key, None) or _Record()
        record.failures = [t for t in record.failures if now - t < window]
        record.failures.append(now)
        over = len(record.failures) - allowed
        lock = 0
        if over > 0:
            lock = min(int(settings.LOGIN_BACKOFF_MAX_SECONDS), 2 ** min(over, 30))
            record.locked_until = now + lock
        _records[key] = record
        _make_room(now, keep=key)
        return lock


def _make_room(now: float, keep: str) -> None:
    """Drop records until at most ``MAX_ACCOUNTS`` remain; the caller holds
    the lock. The oldest record without a running lock goes first; only when
    every other record is locked does the one whose lock ends first go. So
    failures spread over many other addresses cannot evict, and so reset,
    the backoff of the account under attack (issue #541)."""
    while len(_records) > MAX_ACCOUNTS:
        victim = next(
            (k for k, r in _records.items() if k != keep and r.locked_until <= now),
            None,
        )
        if victim is None:
            victim = min(
                (k for k in _records if k != keep),
                key=lambda k: _records[k].locked_until,
            )
        del _records[victim]


def success(email: str) -> None:
    """A sign-in succeeded: forget *email*'s failures."""
    with _lock:
        _records.pop(_key(email), None)


def reset() -> None:
    """Forget every record (tests, and an operator's restart)."""
    with _lock:
        _records.clear()
