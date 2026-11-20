"""The one SQLite connection the test engine shares, one checkout's transaction at a time.

``conftest._engine`` is an in-memory database behind a ``StaticPool``: every
checkout of every thread gets the same ``sqlite3`` connection, so the rows
one session flushed are seen by the next. The application runs part of its
database work in threads (``asyncio.to_thread``) while the event loop goes
on with its own sessions; on Postgres each of those checkouts is a
connection of its own. On the shared connection they were not: a session
closed on one thread rolled back what another thread had written and not
yet committed, and a commit landing between ``rollback()``'s check for an
open transaction and its ``ROLLBACK`` raised ``cannot rollback - no
transaction is active`` (the sprint integration job failed that way on a
loaded CI runner).

Here the connection's open transaction belongs to the checkout that opened
it (the pool's connection proxy, which the dialect is handed), as separate
connections would have it:

* every call into the connection is made under one lock, so no two threads
  are inside ``sqlite3`` at once (``check_same_thread=False`` leaves that to
  the caller);
* a statement through another checkout, from another thread, waits for that
  transaction to end: a write instead of joining it, and a read instead of
  seeing rows that thread flushed and has not committed, which a Postgres
  connection never sees (READ COMMITTED). Reads that went on were how a
  background code-graph sync was read half written
  (``test_code_graph_store``, about 1 run in 40 under ``-n 8``);
* ``commit()`` and ``rollback()`` through such a checkout end nothing: it
  has no transaction of its own (had it written, it would own it).

One checkout may move between threads (``TestClient`` runs a request's
dependencies and its endpoint on pool threads), and checkouts on one thread
keep sharing the connection, as they always have: the tests' own session and
the code under test do.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

# How long a write waits for another checkout's transaction before failing:
# a wait that long is a test holding a transaction open while it waits on
# the thread that wants to write.
WAIT_SECONDS = 30.0


class SerializedConnection(sqlite3.Connection):
    """A ``sqlite3`` connection whose calls are made one at a time."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.turn = threading.Condition(threading.RLock())
        # The (checkout, thread) whose transaction is open, set by ``install``.
        self.owner: tuple[Any, int] | None = None

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:
        return super().cursor(factory or SerializedCursor)

    def run(self, call: Any) -> Any:
        with self.turn:
            return call()

    def commit(self) -> None:
        self.run(super().commit)

    def rollback(self) -> None:
        self.run(super().rollback)


class SerializedCursor(sqlite3.Cursor):
    """A cursor that makes each call under its connection's lock."""

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        return self.connection.run(
            lambda: super(SerializedCursor, self).execute(sql, parameters)
        )

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> sqlite3.Cursor:
        return self.connection.run(
            lambda: super(SerializedCursor, self).executemany(sql, seq_of_parameters)
        )

    def fetchone(self) -> Any:
        return self.connection.run(super().fetchone)

    def fetchmany(self, size: int | None = None) -> list[Any]:
        if size is None:
            return self.connection.run(super().fetchmany)
        return self.connection.run(
            lambda: super(SerializedCursor, self).fetchmany(size)
        )

    def fetchall(self) -> list[Any]:
        return self.connection.run(super().fetchall)

    def close(self) -> None:
        self.connection.run(super().close)


def connect_args() -> dict[str, Any]:
    """``create_engine``'s ``connect_args`` for the shared in-memory database."""
    return {"check_same_thread": False, "factory": SerializedConnection}


def _raw(proxy: Any) -> Any:
    return getattr(proxy, "dbapi_connection", proxy)


def _shares(raw: SerializedConnection, checkout: Any) -> bool:
    """Whether *checkout*, on this thread, may use the open transaction."""
    if not raw.in_transaction or raw.owner is None:
        return True
    owner, thread = raw.owner
    return (
        checkout is not None and owner is checkout
    ) or thread == threading.get_ident()


def _settle(raw: SerializedConnection, checkout: Any) -> None:
    if not raw.in_transaction:
        raw.owner = None
        raw.turn.notify_all()
    elif raw.owner is None:
        raw.owner = (checkout, threading.get_ident())


def install(engine: Any) -> None:
    """Give the connection's open transaction to the checkout that opened it."""
    dialect = engine.dialect

    def executing(original: Any) -> Any:
        def execute(cursor: Any, statement: str, *args: Any) -> Any:
            raw = cursor.connection
            if not isinstance(raw, SerializedConnection):
                return original(cursor, statement, *args)
            context = args[-1] if args else None
            root = getattr(context, "root_connection", None)
            checkout = root.connection if root is not None else None
            with raw.turn:
                # Reads wait too: on the one connection they would see the
                # rows another thread flushed and has not committed, which no
                # Postgres connection ever sees (READ COMMITTED).
                if not raw.turn.wait_for(lambda: _shares(raw, checkout), WAIT_SECONDS):
                    raise RuntimeError(
                        "a statement waited on another checkout's open transaction "
                        f"for {WAIT_SECONDS:.0f} s (tests/shared_sqlite.py)"
                    )
                try:
                    return original(cursor, statement, *args)
                finally:
                    _settle(raw, checkout)

        return execute

    def ending(original: Any) -> Any:
        def end(proxy: Any) -> None:
            raw = _raw(proxy)
            if not isinstance(raw, SerializedConnection):
                return original(proxy)
            with raw.turn:
                if not _shares(raw, proxy):
                    return None  # another checkout's transaction: not this one's to end
                try:
                    return original(proxy)
                finally:
                    _settle(raw, proxy)

        return end

    dialect.do_execute = executing(dialect.do_execute)
    dialect.do_executemany = executing(dialect.do_executemany)
    dialect.do_execute_no_params = executing(dialect.do_execute_no_params)
    dialect.do_commit = ending(dialect.do_commit)
    dialect.do_rollback = ending(dialect.do_rollback)
