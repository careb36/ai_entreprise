"""Predicted difficulty scored against observed effort (issue #534,
``services/models/effort.py``, ``services/models/calibration.py``,
``GET /v1/companies/{id}/calibration``): the observation read only from the
task's records (events, the proxy's reported turns, the daemon's writes),
never from a run's claims; unknown tokens counted apart, never 0; one
``effort_observed`` event per task at close, a new one only when it
changed, the chain intact; the report deterministic, Spearman's correlation
on fixture data, bands two apart, the signals information only; a Viewer
reads it, an outsider does not."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from models import AuditLog, BacklogItem, Sprint, TaskEvent
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from services.models import calibration, effort
from services.tasks import task_events, task_projection
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company

T0 = datetime(2026, 11, 1, 9, 0, 0)


def _ev(kind, minutes=0, *, status=None, actor="system", payload=None):
    return {
        "event_type": kind,
        "created_at": T0 + timedelta(minutes=minutes),
        "task_status": status,
        "actor": actor,
        "payload": payload or {},
    }


# --- The observation, pure ---------------------------------------------------


def test_tokens_are_known_only_when_every_turn_was_reported():
    assert effort.tokens_of([("reported", 100), ("reported", 50)])["tokens"] == 150
    partial = effort.tokens_of([("reported", 100), ("estimated", 999)])
    # An estimated turn makes the total unknown; the reported part is kept apart.
    assert partial["tokens"] is None
    assert partial["tokens_reported"] == 100 and partial["turns_unreported"] == 1
    assert partial["tokens_unknown_reason"] == effort.UNREPORTED_TURNS
    none = effort.tokens_of([])
    assert none["tokens"] is None and none["tokens_unknown_reason"] == effort.NO_TURNS


def test_the_observation_reads_events_turns_and_daemon_writes():
    events = [
        _ev("claimed", 0, status="in_progress"),
        _ev("delivered", 10),
        _ev(
            "reviewed", 12, status="todo", actor="QA", payload={"verdict": "needs_work"}
        ),
        _ev("sent_back", 13, status="todo"),  # the autopilot's: not a person's
        _ev("delivered", 30),
        _ev("review_failed", 31),
        _ev("sent_back", 35, status="todo", actor=task_events.PERSON),
        _ev("delivered", 50),
        _ev("reviewed", 55, status="done", actor="QA", payload={"verdict": "approved"}),
    ]
    seen = effort.observe(
        status="done",
        events=events,
        turns=[("reported", 400), ("reported", 600)],
        files=["a.py", "b.py", "a.py"],
    )
    assert seen["tokens"] == 1000
    assert seen["wall_seconds"] == 55 * 60
    assert (seen["deliveries"], seen["rework_cycles"]) == (3, 2)
    assert seen["rework_requests"] == 1 and seen["send_backs"] == 1
    assert seen["review_failures"] == 1
    assert seen["files_written"] == 2
    # Never done, no daemon report: unknown, not zero.
    open_task = effort.observe(
        status="blocked", events=events[:3], turns=[], files=None
    )
    assert open_task["wall_seconds"] is None and open_task["files_written"] is None
    assert open_task["tokens"] is None


# --- The report, pure --------------------------------------------------------


def test_spearman_on_fixture_data():
    assert calibration.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert calibration.spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == -1.0
    # Ties share their average rank: x ranks 1.5, 1.5, 3, 4.
    assert calibration.spearman([1, 1, 2, 3], [5, 6, 7, 8]) == pytest.approx(
        0.9487, abs=1e-4
    )
    assert calibration.spearman([1, 2], [1, 2]) is None  # too few
    assert calibration.spearman([3, 3, 3], [1, 2, 3]) is None  # no variance


def _fact(task_id, score, tokens, *, rework=0, sprint=1, role="Developer"):
    return calibration.task_fact(
        task_id=task_id,
        sprint_id=sprint,
        score=score,
        predicted_source="estimated",
        observation={
            "observed": {
                "done": True,
                "tokens": tokens,
                "rework_cycles": rework,
                "wall_seconds": 60 * task_id,
                "files_written": None,
            },
            "attribution": {
                "role": role,
                "producer": TASK_WORKER_PRODUCER,
                "model": "m",
            },
        },
        observed_source="stored",
    )


def _fixture():
    return [
        _fact(1, 1, 100),
        _fact(2, 2, 200, rework=2),
        _fact(3, 1, 9000, rework=3),  # predicted low, observed high
        _fact(4, 3, 500),
        _fact(5, 5, 8000, sprint=2, role="QA"),
        _fact(6, 4, 150, sprint=2, role="QA"),  # predicted high, observed low
        _fact(7, 2, None, sprint=2),  # tokens unknown
    ]


def test_the_report_is_deterministic_and_counts_unknown_tokens_apart():
    facts = _fixture()
    first = calibration.report(facts)
    assert first == calibration.report(list(reversed(facts)))
    overall = first["overall"]
    assert overall["tasks"] == 7
    assert (overall["tokens_known"], overall["tokens_unknown"]) == (6, 1)
    # The unknown task is not a zero in the spread.
    assert overall["tokens_spread"]["min"] == 100
    assert overall["off_by_two_bands"] == 2 and overall["observed_band_unknown"] == 1
    assert overall["off_by_two_bands_share"] == pytest.approx(2 / 6, abs=1e-4)
    assert overall["spearman"] == calibration.spearman(
        [1, 2, 1, 3, 5, 4], [100, 200, 9000, 500, 8000, 150]
    )
    low = first["by_predicted_band"][0]
    assert low["predicted_band"] == "low" and low["tasks"] == 4
    # Two of the four easy tasks needed more than one rework.
    assert low["over_one_rework"] == 2 and low["over_one_rework_share"] == 0.5
    assert [r["predicted_band"] for r in first["by_predicted_band"]] == [
        "low",
        "medium",
        "high",
    ]
    assert [r["sprint_id"] for r in first["by_sprint_id"]] == [1, 2]
    assert {r["role"] for r in first["by_role"]} == {"Developer", "QA"}
    assert first["information_only"] is True


def test_signals_are_information_about_the_bands():
    easy = [_fact(i, 1, 100 * i, rework=2 if i % 2 else 0) for i in range(1, 7)]
    report = calibration.report(easy)
    assert {"signal": "low_band_reworked", "band": "low", "value": 0.5} in report[
        "signals"
    ]
    assert calibration.report(easy[:3])["signals"] == []  # too few to say


# --- From the records --------------------------------------------------------


def _sprint(session, company, status="completed"):
    sprint = Sprint(company_id=company.id, sprint_name="S", status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session, company, sprint, *, covers=(), status="done", writes=("a.py",)):
    item = BacklogItem(
        sprint_id=sprint.id,
        title="T",
        status=status,
        covers=list(covers),
        assignee="Developer",
    )
    session.add(item)
    session.commit()
    task_events.record(session, item, "claimed", "system", status="in_progress")
    task_events.record(session, item, "delivered", "Developer", text="done it")
    task_events.record(
        session,
        item,
        "reviewed",
        "QA",
        author="QA",
        status=status,
        text="APPROVED: fine",
    )
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        agent_role="Developer",
        model="served-model",
        # What the agent says it did: a claim, never read for the effort.
        claims={"files": ["a.py", "b.py", "c.py", "d.py"], "tokens": 1},
        artifact_digest="x",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            evidence={
                "workspace_writes": [{"path": p, "status": "written"} for p in writes]
                + [{"path": "skipped.py", "status": "refused"}]
            },
        )
    )
    session.add(
        AuditLog(
            company_id=company.id,
            interaction_id=f"task-{item.id}",
            action="execute_task",
            details={"tokens": "reported"},
            cost_tokens=700,
        )
    )
    session.commit()
    return item


def _effort_events(session, item_id):
    return session.exec(
        select(TaskEvent).where(
            TaskEvent.task_id == item_id, TaskEvent.event_type == effort.KIND
        )
    ).all()


def test_effort_is_observed_once_at_close_from_records_only(sqlite_session):
    company, other = _company(sqlite_session), _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    item = _task(sqlite_session, company, sprint)
    # Another company's turn on the same reference never counts.
    sqlite_session.add(
        AuditLog(
            company_id=other.id,
            interaction_id=f"task-{item.id}",
            action="execute_task",
            details={"tokens": "reported"},
            cost_tokens=99999,
        )
    )
    sqlite_session.commit()
    assert effort.record_sprint(sqlite_session, sprint.id) == 1
    sqlite_session.commit()
    (event,) = _effort_events(sqlite_session, item.id)
    observed = event.payload["observed"]
    assert (
        event.actor == task_events.SYSTEM and event.payload["observed_by"] == "backend"
    )
    assert observed["tokens"] == 700
    # The daemon wrote one file; the run claimed four.
    assert observed["files_written"] == 1
    assert observed["deliveries"] == 1 and observed["rework_cycles"] == 0
    assert observed["wall_seconds"] is not None
    assert event.payload["attribution"] == {
        "role": "Developer",
        "producer": TASK_WORKER_PRODUCER,
        "model": "served-model",
    }
    # Unchanged: nothing new. Changed: a new event, the first kept.
    assert effort.record_sprint(sqlite_session, sprint.id) == 0
    sqlite_session.add(
        AuditLog(
            company_id=company.id,
            interaction_id=f"task-{item.id}",
            action="execute_task",
            details={"tokens": "estimated"},
            cost_tokens=5,
        )
    )
    sqlite_session.commit()
    assert effort.record_sprint(sqlite_session, sprint.id) == 1
    sqlite_session.commit()
    first, second = _effort_events(sqlite_session, item.id)
    assert first.payload["observed"]["tokens"] == 700
    assert second.payload["observed"]["tokens"] is None
    assert task_events.verify_chain(sqlite_session, item.id)["status"] == "intact"
    # The observation says nothing about the task's state, and adds no comment.
    events = task_events.for_task(sqlite_session, item.id)
    assert task_projection.status_from_events(events) == "done"
    assert all(not e.payload.get("text") for e in (first, second))
    assert task_projection.covers(item, events)


def test_an_open_sprint_is_not_observed(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company, status="active")
    _task(sqlite_session, company, sprint)
    assert effort.record_sprint(sqlite_session, sprint.id) == 0


def test_the_report_reads_stored_and_computes_the_rest(sqlite_session):
    company = _company(sqlite_session)
    stored = _sprint(sqlite_session, company)
    _task(sqlite_session, company, stored, covers=["REQ-1.1"])
    effort.record_sprint(sqlite_session, stored.id)
    sqlite_session.commit()
    older = _sprint(sqlite_session, company)
    _task(sqlite_session, company, older, covers=["REQ-1.1", "REQ-1.2", "REQ-2.1"])
    _task(sqlite_session, company, _sprint(sqlite_session, company, "active"))
    data = calibration.record(sqlite_session, company.id)
    assert data["tasks"] == 2
    assert data["observed_computed"] == 1
    # No ``estimated`` event: recomputed from the plan, and said so.
    assert data["predicted_recomputed"] == 2
    assert data["overall"]["tokens_known"] == 2
    assert [r["predicted_band"] for r in data["by_predicted_band"]] == ["low", "high"]


@pytest.mark.usefixtures("enforcement")
def test_a_viewer_reads_the_calibration(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    _task(sqlite_session, company, _sprint(sqlite_session, company))
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/calibration"
    assert test_client.get(url).status_code == 401
    assert test_client.get(url, headers=outsider).status_code == 403
    response = test_client.get(url, headers=viewer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tasks"] == 1 and body["information_only"] is True
    assert body["overall"]["tokens_spread"]["median"] == 700


def test_observing_effort_never_stops_the_close(monkeypatch):
    from services.loop.worker import close

    def broken(session, sprint_id):
        raise RuntimeError("database gone")

    monkeypatch.setattr(close.effort, "record_sprint", broken)
    assert close._observe_effort(1) == 0
