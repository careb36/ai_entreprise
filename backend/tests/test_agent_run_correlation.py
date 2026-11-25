"""
An ``AgentRun`` keeps the outbox correlation id of the async run it records
(issue #736): the task worker's record stage passes the id the assignment
carried, and the ingest stores it. A run with no id stays ``None``.
"""

from uuid import uuid4

from models import Company
from schemas import AgentRunCreate
from services.evidence import agent_run_service
from services.loop.worker import record


def _company(session):
    company = Company(company_name=f"Corr {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def test_ingest_stores_the_correlation_id(sqlite_session):
    company = _company(sqlite_session)
    correlation_id = uuid4()

    run, created = agent_run_service.ingest_agent_run(
        sqlite_session,
        AgentRunCreate(
            company_id=company.id,
            external_ref="task-1",
            producer="task-worker",
            artifact="done",
            correlation_id=str(correlation_id),
        ),
    )

    assert created
    assert run.correlation_id == correlation_id


def test_ingest_without_an_id_leaves_it_empty(sqlite_session):
    company = _company(sqlite_session)

    run, _ = agent_run_service.ingest_agent_run(
        sqlite_session,
        AgentRunCreate(
            company_id=company.id, external_ref="task-2", producer="p", artifact="x"
        ),
    )

    assert run.correlation_id is None


def test_the_record_stage_passes_the_assignment_id(monkeypatch):
    captured = {}
    correlation_id = str(uuid4())

    class Run:
        id = 7

    class Verified:
        verdict = "verified"

    class FakeSession:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

    def ingest(session, payload):
        captured["payload"] = payload
        return Run(), True

    monkeypatch.setattr(record, "Session", FakeSession)
    monkeypatch.setattr(record.agent_run_service, "ingest_agent_run", ingest)
    monkeypatch.setattr(record, "verify_agent_run", lambda s, r, e: Verified())

    record._record_task_run(
        {"id": 3, "title": "t"},
        {"role": "Dev", "correlation_id": correlation_id},
        {"id": 2, "company_id": 1},
        "done",
        [],
        None,
    )

    assert captured["payload"].correlation_id == correlation_id


def test_an_id_that_is_not_a_uuid_is_left_out(sqlite_session):
    company = _company(sqlite_session)

    run, _ = agent_run_service.ingest_agent_run(
        sqlite_session,
        AgentRunCreate(
            company_id=company.id,
            external_ref="task-3",
            producer="p",
            artifact="x",
            correlation_id="not-a-uuid",
        ),
    )

    assert run.correlation_id is None
