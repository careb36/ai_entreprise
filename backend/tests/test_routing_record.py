"""Done, rework and cost by difficulty band and model (issue #319,
``services/routing_record.py``, ``GET /v1/companies/{id}/routing_record``):
the difficulty as planned (recomputed from the task's ``covers``), the model
of the last delivery, done and first attempts from the board, rework from
``board.rework_request_count``, reported tokens only from turns the provider
reported (estimated and unavailable ones counted apart), completed sprints
only, the policy's band beside each row; a Viewer reads it, an outsider does
not."""

from __future__ import annotations

import json

import pytest
from config import settings
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import AuditLog, BacklogItem, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from services.models import routing_record

REWORK = {"author": "QA", "text": "NEEDS WORK: cover the empty case"}
SEND_BACK = {"author": "User", "text": "Sent back to work: retry"}


def test_a_task_fact_reads_the_board():
    fact = routing_record.task_fact(
        difficulty=2, model=" m ", status="done", comments=[], runs=1
    )
    assert fact["model"] == "m" and fact["first_attempt"] and fact["done"]
    reworked = routing_record.task_fact(
        difficulty=2, model="m", status="done", comments=[REWORK], runs=2
    )
    assert reworked["rework"] == 1 and not reworked["first_attempt"]
    # Two deliveries with no request on the board are still not a first attempt.
    twice = routing_record.task_fact(
        difficulty=2, model="m", status="done", comments=[], runs=2
    )
    assert not twice["first_attempt"]
    # A person's send-back is not a first attempt either.
    sent_back = routing_record.task_fact(
        difficulty=2, model="m", status="done", comments=[SEND_BACK], runs=1
    )
    assert sent_back["rework"] == 0 and not sent_back["first_attempt"]
    blocked = routing_record.task_fact(
        difficulty=4, model=None, status="blocked", comments=[], runs=0
    )
    assert blocked["blocked"] and blocked["model"] is None


def test_estimated_and_unavailable_tokens_are_never_added():
    tokens = routing_record.turn_tokens(
        [("reported", 100), ("estimated", 0), ("unavailable", 0), ("reported", 50)]
    )
    assert tokens == {"reported": 150, "turns_reported": 2, "turns_unreported": 2}


def test_rows_by_difficulty_and_model_with_the_policy_band():
    def fact(difficulty, model, status, comments=(), runs=1, reported=0):
        return routing_record.task_fact(
            difficulty=difficulty,
            model=model,
            status=status,
            comments=list(comments),
            runs=runs,
            tokens=routing_record.turn_tokens([("reported", reported)]),
        )

    facts = [
        fact(2, "cheap", "done", reported=10),
        fact(2, "cheap", "done", [REWORK, REWORK], runs=3, reported=30),
        fact(4, "strong", "blocked", [REWORK], runs=2, reported=40),
        fact(4, "strong", "done", reported=20),
        fact(4, None, "blocked", runs=0),
    ]
    rows = routing_record.aggregate(facts, {"bands": {"1-3": "cheap", "4-5": "x"}})
    assert [(r["difficulty"], r["model"]) for r in rows] == [
        (2, "cheap"),
        (4, None),
        (4, "strong"),
    ]
    cheap, unrouted, strong = rows
    assert cheap["band"] == "1-3" and strong["band"] == "4-5"
    assert cheap["tasks"] == 2 and cheap["done"] == 2
    assert cheap["done_first_attempt"] == 1 and cheap["first_attempt_rate"] == 0.5
    assert cheap["rework_per_task"] == 1.0
    assert cheap["tokens"] == {
        "reported": 40,
        "turns_reported": 2,
        "turns_unreported": 0,
    }
    assert strong["blocked_share"] == 0.5 and strong["rework"] == 1
    assert unrouted["tasks"] == 1 and unrouted["blocked"] == 1
    # Without a policy the band is unnamed, the rows are the same.
    assert routing_record.aggregate(facts)[0]["band"] is None


def _sprint(session, company, status):
    sprint = Sprint(company_id=company.id, sprint_name="S", status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session, company, sprint, *, covers, status, models, turns=(), comments=()):
    item = BacklogItem(
        sprint_id=sprint.id,
        title="T",
        status=status,
        covers=covers,
        comments=list(comments),
    )
    session.add(item)
    session.commit()
    for version, model in enumerate(models, start=1):
        session.add(
            AgentRun(
                company_id=company.id,
                external_ref=f"task-{item.id}",
                version=version,
                producer=TASK_WORKER_PRODUCER,
                agent_role="Developer",
                model=model,
                artifact_digest="x",
                artifact_size=1,
            )
        )
    for status_, cost in turns:
        session.add(
            AuditLog(
                company_id=company.id,
                interaction_id=f"task-{item.id}",
                action="execute_task",
                details={"tokens": status_},
                cost_tokens=cost,
            )
        )
    session.commit()
    return item


def test_the_record_reads_completed_sprints(sqlite_session, monkeypatch):
    monkeypatch.setattr(
        settings, "AGENT_MODEL_POLICY", json.dumps({"bands": {"1-2": "a", "3-5": "b"}})
    )
    company = _company(sqlite_session)
    done = _sprint(sqlite_session, company, "completed")
    # One criterion: difficulty 2. Three criteria: difficulty 4.
    _task(
        sqlite_session,
        company,
        done,
        covers=["REQ-1.1"],
        status="done",
        models=["cheap"],
        turns=[("reported", 120), ("estimated", 999)],
    )
    _task(
        sqlite_session,
        company,
        done,
        covers=["REQ-1.1", "REQ-1.2", "REQ-2.1"],
        status="blocked",
        models=["cheap", "strong"],
        comments=[REWORK, SEND_BACK],
        turns=[("reported", 300), ("unavailable", 0)],
    )
    # An open sprint does not count.
    _task(
        sqlite_session,
        company,
        _sprint(sqlite_session, company, "active"),
        covers=[],
        status="done",
        models=["cheap"],
    )
    data = routing_record.record(sqlite_session, company.id)
    assert data["tasks"] == 2
    assert data["policy"] == {"1-2": "a", "3-5": "b"}
    easy, hard = data["rows"]
    assert (easy["difficulty"], easy["band"], easy["model"]) == (2, "1-2", "cheap")
    assert easy["done_first_attempt"] == 1
    assert easy["tokens"] == {
        "reported": 120,
        "turns_reported": 1,
        "turns_unreported": 1,
    }
    # Counted for the model of its last delivery.
    assert (hard["difficulty"], hard["band"], hard["model"]) == (4, "3-5", "strong")
    assert hard["blocked_share"] == 1.0 and hard["rework"] == 1
    assert hard["tokens"]["reported"] == 300


def test_another_companys_runs_and_turns_do_not_count(sqlite_session):
    company, other = _company(sqlite_session), _company(sqlite_session)
    item = _task(
        sqlite_session,
        company,
        _sprint(sqlite_session, company, "completed"),
        covers=[],
        status="done",
        models=[],
    )
    sqlite_session.add(
        AuditLog(
            company_id=other.id,
            interaction_id=f"task-{item.id}",
            action="execute_task",
            details={"tokens": "reported"},
            cost_tokens=500,
        )
    )
    sqlite_session.commit()
    (row,) = routing_record.record(sqlite_session, company.id)["rows"]
    assert row["model"] is None and row["tokens"]["reported"] == 0


@pytest.mark.usefixtures("enforcement")
def test_a_viewer_reads_the_routing_record(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    _task(
        sqlite_session,
        company,
        _sprint(sqlite_session, company, "completed"),
        covers=["REQ-1.1"],
        status="done",
        models=["cheap"],
    )
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/routing_record"
    assert test_client.get(url).status_code == 401
    assert test_client.get(url, headers=outsider).status_code == 403
    response = test_client.get(url, headers=viewer)
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["model"] == "cheap"
