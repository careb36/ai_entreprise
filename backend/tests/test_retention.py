"""
The janitor's retention (issue #328): what it deletes, and what it keeps.

The statements run over a plain ``sqlite3`` connection to a database the
models created, the way the janitor runs them over psycopg2.

- Delivered outbox events older than the setting go; pending and failed stay.
- A superseded run version older than the setting goes with its
  verifications; the latest version and a version a person decided on stay.
  A batch is one transaction: a decision recorded after the runs were picked
  keeps its run (the guards are checked again), and a failure rolls the
  batch back with the verifications in place.
- KPI turn rows outside the audit chain go; a chained row is never deleted
  and is counted as kept.
- Task events of closed sprints go; an open sprint's stay.
- ``0`` keeps everything, and a malformed setting keeps the default.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from models import AuditLog, Company, OutboxEvent, Sprint, TaskEvent
from models.agent_run import AgentRun, AgentRunApproval, Verification
from sqlmodel import Session, SQLModel, create_engine, select

from janitor import retention

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
OLD = (NOW - timedelta(days=40)).replace(tzinfo=None)
RECENT = (NOW - timedelta(days=2)).replace(tzinfo=None)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "retention.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        company = Company(company_name="Retention")
        session.add(company)
        session.commit()
        session.refresh(company)
        yield session, company.id, path


def _apply(path, **days):
    conn = sqlite3.connect(path)
    try:
        return retention.apply(conn, days, now=NOW)
    finally:
        conn.close()


def _count(session, model, *where):
    session.expire_all()
    return len(session.exec(select(model).where(*where)).all())


def test_delivered_outbox_events_go_after_the_setting(db):
    session, _, path = db
    rows = [
        ("completed", OLD),
        ("completed", RECENT),
        ("pending", OLD),
        ("failed", OLD),
    ]
    for status, processed_at in rows:
        session.add(
            OutboxEvent(
                aggregate_type="x",
                aggregate_id="1",
                event_type="nats_publish",
                payload={},
                status=status,
                processed_at=processed_at,
            )
        )
    session.commit()

    report = _apply(path, RETENTION_OUTBOX_DAYS=7)

    assert report["outbox_events"] == 1
    left = sorted(
        (e.status, e.processed_at == RECENT)
        for e in session.exec(select(OutboxEvent)).all()
    )
    assert left == [("completed", True), ("failed", False), ("pending", False)]


def _run(session, company_id, ref, version, ingested_at):
    run = AgentRun(
        company_id=company_id,
        external_ref=ref,
        version=version,
        producer="le-fabrique-task-worker",
        artifact_digest="d",
        artifact_size=1,
        ingested_at=ingested_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        Verification(
            company_id=company_id, agent_run_id=run.id, verdict="verified", checks=[]
        )
    )
    session.commit()
    return run


def test_superseded_run_versions_go_and_the_evidence_of_record_stays(db):
    session, company_id, path = db
    old_v1 = _run(session, company_id, "task-1", 1, OLD).id
    _run(session, company_id, "task-1", 2, OLD)  # latest: kept however old
    recent_v1 = _run(session, company_id, "task-2", 1, RECENT)
    _run(session, company_id, "task-2", 2, RECENT)
    decided_v1 = _run(session, company_id, "task-3", 1, OLD)
    _run(session, company_id, "task-3", 2, OLD)
    session.add(
        AgentRunApproval(
            company_id=company_id,
            agent_run_id=decided_v1.id,
            decision="rejected",
            approver_name="a manager",
        )
    )
    session.commit()

    report = _apply(path, RETENTION_AGENT_RUNS_DAYS=30)

    assert report["agent_runs"] == 1
    remaining = {r.id for r in session.exec(select(AgentRun)).all()}
    assert old_v1 not in remaining
    assert {recent_v1.id, decided_v1.id} <= remaining and len(remaining) == 5
    assert _count(session, Verification, Verification.agent_run_id == old_v1) == 0
    assert _count(session, Verification) == 5


def _superseded_pair(session, company_id, ref):
    old = _run(session, company_id, ref, 1, OLD).id
    _run(session, company_id, ref, 2, OLD)
    return old


def test_a_decision_recorded_after_the_runs_were_picked_keeps_its_run(db, monkeypatch):
    session, company_id, path = db
    commented = _superseded_pair(session, company_id, "task-1")
    plain = _superseded_pair(session, company_id, "task-2")
    original = retention._execute
    picked = []

    def comment_right_after_the_pick(conn, statement, params=()):
        cursor = original(conn, statement, params)
        if not picked and "ORDER BY r.id LIMIT" in statement:
            picked.append(statement)
            # A Manager's comment that committed before the batch locked the runs.
            original(
                conn,
                "INSERT INTO review_comments (company_id, agent_run_id, side, body, "
                "author_name, created_at) VALUES (%s, %s, 'new', 'keep', 'a manager', %s)",
                (company_id, commented, OLD.isoformat(sep=" ")),
            )
        return cursor

    monkeypatch.setattr(retention, "_execute", comment_right_after_the_pick)
    report = _apply(path, RETENTION_AGENT_RUNS_DAYS=30)

    assert picked and report["agent_runs"] == 1
    remaining = {r.id for r in session.exec(select(AgentRun)).all()}
    assert commented in remaining and plain not in remaining
    assert _count(session, Verification, Verification.agent_run_id == commented) == 1


def test_a_batch_that_fails_keeps_the_verifications_with_their_runs(db, monkeypatch):
    session, company_id, path = db
    old = _superseded_pair(session, company_id, "task-1")
    original = retention._execute

    def parent_delete_fails(conn, statement, params=()):
        if statement.startswith("DELETE FROM agent_runs"):
            raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        return original(conn, statement, params)

    monkeypatch.setattr(retention, "_execute", parent_delete_fails)
    with pytest.raises(sqlite3.IntegrityError):
        _apply(path, RETENTION_AGENT_RUNS_DAYS=30)

    session.expire_all()
    assert old in {r.id for r in session.exec(select(AgentRun)).all()}
    assert _count(session, Verification, Verification.agent_run_id == old) == 1
    assert _count(session, Verification) == 2


def test_retention_refuses_a_connection_in_autocommit():
    class Autocommit:
        autocommit = True

        def rollback(self):
            raise AssertionError("nothing to roll back")

    with pytest.raises(ValueError, match="autocommit"):
        retention.apply(Autocommit(), {"RETENTION_AGENT_RUNS_DAYS": 30}, now=NOW)


def _turn(session, company_id, action, created_at, chain_seq=None):
    row = AuditLog(
        company_id=company_id,
        action=action,
        details={"role": "Developer"},
        created_at=created_at,
        chain_seq=chain_seq,
    )
    session.add(row)
    session.commit()
    return row


def test_kpi_turns_outside_the_chain_go_and_the_chain_is_never_touched(db):
    session, company_id, path = db
    unchained = _turn(session, None, "execute_task", OLD).id
    chained = _turn(session, company_id, "qa_review", OLD, chain_seq=1).id
    _turn(session, None, "execute_task", RECENT)
    other_action = _turn(session, None, "burn_engram", OLD).id

    report = _apply(path, RETENTION_KPI_TURNS_DAYS=30)

    assert report["kpi_turns"] == 1 and report["kpi_turns_kept_for_chain"] == 1
    ids = {r.id for r in session.exec(select(AuditLog)).all()}
    assert unchained not in ids
    assert {chained, other_action} <= ids and len(ids) == 3


def test_a_turn_a_run_points_at_is_kept(db):
    session, company_id, path = db
    turn = _turn(session, None, "execute_task", OLD)
    run = _run(session, company_id, "task-9", 1, RECENT)
    run.audit_log_id = turn.id
    session.add(run)
    session.commit()

    assert _apply(path, RETENTION_KPI_TURNS_DAYS=30)["kpi_turns"] == 0


def test_task_events_of_closed_sprints_go(db):
    session, company_id, path = db
    sprints = {}
    for status in ("completed", "cancelled", "active"):
        sprint = Sprint(company_id=company_id, sprint_name=status, status=status)
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
        sprints[status] = sprint.id
        for created_at in (OLD, RECENT):
            session.add(
                TaskEvent(
                    company_id=company_id,
                    sprint_id=sprint.id,
                    event_type="moved",
                    created_at=created_at,
                )
            )
    session.commit()

    report = _apply(path, RETENTION_GLASSROOM_DAYS=30)

    assert report["task_events"] == 2
    assert _count(session, TaskEvent, TaskEvent.sprint_id == sprints["active"]) == 2
    assert _count(session, TaskEvent) == 4


def test_zero_keeps_everything(db):
    session, company_id, path = db
    _turn(session, None, "execute_task", OLD)
    _run(session, company_id, "task-1", 1, OLD)
    _run(session, company_id, "task-1", 2, OLD)

    report = _apply(
        path,
        RETENTION_OUTBOX_DAYS=0,
        RETENTION_AGENT_RUNS_DAYS=0,
        RETENTION_KPI_TURNS_DAYS=0,
        RETENTION_GLASSROOM_DAYS=0,
    )

    assert set(report.values()) == {0}
    assert _count(session, AgentRun) == 2 and _count(session, AuditLog) == 1


def test_settings_come_from_the_environment_with_safe_defaults():
    days = retention.settings_from_env(
        {"RETENTION_AGENT_RUNS_DAYS": "90", "RETENTION_KPI_TURNS_DAYS": "soon"}
    )
    assert days == {
        "RETENTION_OUTBOX_DAYS": 7,
        "RETENTION_AGENT_RUNS_DAYS": 90,
        "RETENTION_KPI_TURNS_DAYS": 0,
        "RETENTION_GLASSROOM_DAYS": 0,
    }
    assert (
        retention.settings_from_env({"RETENTION_OUTBOX_DAYS": "-3"})[
            "RETENTION_OUTBOX_DAYS"
        ]
        == 0
    )
