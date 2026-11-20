"""The test engine's one SQLite connection, shared by threads (``shared_sqlite``).

The application writes from ``asyncio.to_thread`` while the event loop goes
on with sessions of its own; on the test engine every session of every
thread gets the same ``sqlite3`` connection. A session closed on one thread
must not roll back another thread's open transaction, a write on one thread
waits for another's transaction instead of joining it, a read on another
thread waits for it too (never seeing uncommitted rows), and sessions on one
thread still see each other's flushed rows. Before, the sprint integration
job failed on a loaded runner with ``cannot rollback - no transaction is
active``, and a thread's committed rows could be lost.
"""

import threading

import conftest
import pytest
from sqlalchemy import text
from sqlmodel import Session

TABLE = "shared_sqlite_probe"


@pytest.fixture()
def engine():
    with conftest._engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE TABLE {TABLE} (v TEXT)")
    yield conftest._engine
    with conftest._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TABLE {TABLE}")


def _values(engine):
    with Session(engine) as session:
        return sorted(session.exec(text(f"SELECT v FROM {TABLE}")).scalars())


def _in_thread(target):
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    return worker


def test_a_session_closed_on_one_thread_leaves_another_threads_transaction_open(
    engine,
):
    written, closed = threading.Event(), threading.Event()
    failures = []

    def other_thread():
        try:
            with Session(engine) as session:
                session.exec(text(f"INSERT INTO {TABLE} VALUES ('other')"))
                written.set()
                assert closed.wait(10)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - reported by the test thread
            failures.append(exc)

    worker = _in_thread(other_thread)
    assert written.wait(10)
    # A session on this thread that checks the connection out, ended by a
    # rollback when it closes (a read would wait for the other transaction).
    with Session(engine) as session:
        session.connection()
    closed.set()
    worker.join(10)

    assert failures == []
    assert _values(engine) == ["other"]


def test_a_read_on_another_thread_never_sees_rows_not_yet_committed(engine):
    """As on Postgres (READ COMMITTED): a background thread half way through a
    write is never read half written. The read waits for the commit
    (``test_code_graph_store``'s section read a graph whose files were
    flushed and whose definitions were not yet, about 1 run in 40 under -n 8)."""
    written, reading = threading.Event(), threading.Event()
    failures = []

    def other_thread():
        try:
            with Session(engine) as session:
                session.exec(text(f"INSERT INTO {TABLE} VALUES ('first')"))
                written.set()
                assert reading.wait(10)
                threading.Event().wait(0.2)  # the read below is waiting now
                session.exec(text(f"INSERT INTO {TABLE} VALUES ('second')"))
                session.commit()
        except Exception as exc:  # noqa: BLE001 - reported by the test thread
            failures.append(exc)

    worker = _in_thread(other_thread)
    assert written.wait(10)
    reading.set()
    seen = _values(engine)
    worker.join(10)

    assert failures == []
    assert seen == ["first", "second"]


def test_a_write_waits_for_another_threads_transaction_instead_of_joining_it(
    engine,
):
    written, writing = threading.Event(), threading.Event()
    failures = []

    def other_thread():
        try:
            with Session(engine) as session:
                session.exec(text(f"INSERT INTO {TABLE} VALUES ('other')"))
                written.set()
                assert writing.wait(10)
                # This thread's write is now waiting: undoing this one keeps it.
                threading.Event().wait(0.2)
                session.rollback()
        except Exception as exc:  # noqa: BLE001 - reported by the test thread
            failures.append(exc)

    worker = _in_thread(other_thread)
    assert written.wait(10)
    with Session(engine) as session:
        writing.set()
        session.exec(text(f"INSERT INTO {TABLE} VALUES ('mine')"))
        session.commit()
    worker.join(10)

    assert failures == []
    assert _values(engine) == ["mine"]


def test_a_session_that_moves_between_threads_still_ends_its_own_transaction(
    engine,
):
    """``TestClient`` runs a request's dependencies and its endpoint on pool
    threads: a session writes on one and is rolled back from another."""
    with Session(engine) as session:
        _in_thread(
            lambda: session.exec(text(f"INSERT INTO {TABLE} VALUES ('undone')"))
        ).join(10)
        session.rollback()

    assert _values(engine) == []


def test_sessions_on_one_thread_still_see_each_others_flushed_rows(engine):
    with Session(engine) as writer:
        writer.exec(text(f"INSERT INTO {TABLE} VALUES ('flushed')"))
        with Session(engine) as reader:
            seen = reader.exec(text(f"SELECT v FROM {TABLE}")).scalars().all()

    assert seen == ["flushed"]
