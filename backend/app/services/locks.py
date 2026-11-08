"""Locks that hold across worker replicas (issue #328).

Several backend replicas may run the task worker, the review cycle and the
agents' loops against one database. Work that must happen once (a review
cycle, closing a sprint, a ScrumMaster triage, a Support or Reviewer cycle)
runs under a lock named here.

On Postgres the lock is a session-level advisory lock taken with
``pg_try_advisory_lock`` on a connection held for the duration of the work:
it never waits, a replica that does not get it skips the work, and a replica
that dies releases it with its connection. On any other database (SQLite in
the tests and in a single-process demo) the lock is a process-local table,
which is exact for one process and lets the tests race two threads.

The key is a stable 63-bit number derived from the lock's name and an id,
so every replica computes the same key without a registry.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from logging_config import setup_logger
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = setup_logger("fabrique-locks")

T = TypeVar("T")

REVIEW_CYCLE = "review-cycle"
SPRINT_CLOSE = "sprint-close"
TRIAGE = "scrum-master-triage"
TASK = "task"
SUPPORT_AGENT = "support-agent"
REVIEWER_AGENT = "reviewer-agent"
SPRINT_INTEGRATION = "sprint-integration"

_local_lock = threading.Lock()
_local_held: set[int] = set()


def lock_key(name: str, ident: int | str = 0) -> int:
    """The advisory lock key for *name* and *ident*, the same on every replica."""
    digest = hashlib.blake2b(f"{name}:{ident}".encode(), digest_size=8).digest()
    # Postgres advisory keys are signed bigints; keep the value positive.
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


@contextmanager
def _local(key: int) -> Iterator[bool]:
    with _local_lock:
        acquired = key not in _local_held
        if acquired:
            _local_held.add(key)
    try:
        yield acquired
    finally:
        if acquired:
            with _local_lock:
                _local_held.discard(key)


@contextmanager
def _advisory(engine: Engine, key: int) -> Iterator[bool]:
    conn = engine.connect()
    acquired = False
    try:
        acquired = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        )
        # Session-level: the lock outlives the transaction, and the connection
        # must not sit idle in one while the work runs.
        conn.commit()
        yield acquired
    finally:
        try:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - Closing the connection releases the lock anyway.
            logger.warning("Advisory unlock failed for key=%s: %s", key, exc)
            conn.invalidate()
        conn.close()


@contextmanager
def held(engine: Engine, name: str, ident: int | str = 0) -> Iterator[bool]:
    """Try to take the lock *name*/*ident*; yield whether this caller holds it.

    Never waits. The caller skips the work when it yields ``False``: another
    replica (or thread) is doing it.
    """
    key = lock_key(name, ident)
    if is_postgres(engine):
        with _advisory(engine, key) as acquired:
            yield acquired
    else:
        with _local(key) as acquired:
            yield acquired


def run_exclusive(
    engine: Engine,
    name: str,
    fn: Callable[..., T],
    *args: Any,
    ident: int | str = 0,
    **kwargs: Any,
) -> T | None:
    """Run ``fn(*args, **kwargs)`` under the lock, or return ``None`` when it is held elsewhere."""
    with held(engine, name, ident) as mine:
        if not mine:
            logger.debug("Lock %s:%s held elsewhere; skipping", name, ident)
            return None
        return fn(*args, **kwargs)
