"""
The count of deliveries the gate before QA sent back, by problem (issue #549).

``orchestration_metrics`` is in platform and may not import the evidence, so
it parses the text the loop writes. These tests pin that parsing to what the
real loop writes for every problem ``delivery_gate`` names, keep the gate's
count apart from the reviewers' (``count_qa_rework_requests``), and read the
count back through ``GET /v1/analytics/gate_refutations``.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.company import Company
from models.event import TaskEvent
from models.sprint import BacklogItem, Sprint
from services.evidence import delivery_gate
from services.loop import task_worker
from services.loop.worker import qa_review
from services.loop.worker import review as worker_review
from services.platform.orchestration_metrics import (
    GATE_UNCONFIRMED,
    count_gate_refutations,
    count_qa_rework_requests,
    gate_refutation_problem,
)
from services.tasks import task_events
from sqlmodel import Session, select

DELIVERABLE = "# Artifact: src/a.py\n\nprint('a')\n" + "word " * 120
INVENTED = "invented dependency or import"


class _FakeNATS:
    jetstream_enabled = False

    async def publish(self, subject, payload, msg_id=None):
        return None

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _company_and_sprint(session: Session) -> Sprint:
    company = Company(company_name=f"Gate count {uuid4().hex[:8]}", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item_refuted_by(session: Session, check: str, outcome: str = "refuted") -> int:
    """A task in review whose latest delivery has *check* at *outcome*."""
    sprint = _company_and_sprint(session)
    item = BacklogItem(
        sprint_id=sprint.id,
        title=f"Write a {uuid4().hex[:8]}",
        status="in_review",
        assignee="Developer",
        comments=[{"author": "Dev", "text": DELIVERABLE, "created_at": ""}],
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    run = AgentRun(
        company_id=sprint.company_id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        claims={"files": ["src/a.py"]},
        artifact_digest="d",
        artifact_size=1,
        status=outcome,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        Verification(
            company_id=sprint.company_id,
            agent_run_id=run.id,
            verdict=outcome,
            checks=[
                {"name": check, "outcome": outcome, "observation": "observed — here"}
            ],
        )
    )
    session.commit()
    return item.id


def _gate_payloads(session: Session, item_id: int) -> list[dict]:
    session.expire_all()
    events = session.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == item_id, TaskEvent.actor == task_events.GATE)
        .order_by(TaskEvent.id)
    ).all()
    return [e.payload for e in events]


def _review(session: Session, monkeypatch, item_id: int) -> list[dict]:
    monkeypatch.setattr(qa_review, "_call_llm_for_review", lambda *a, **k: None)
    asyncio.run(worker_review._review_cycle(_FakeNATS()))
    return _gate_payloads(session, item_id)


# ---------------------------------------------------------------------------
# The format, pinned to what the loop writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("check, problem", sorted(delivery_gate._GATE_PROBLEMS.items()))
def test_every_gate_problem_the_loop_writes_is_counted_by_name(
    db, monkeypatch, check, problem
):
    item_id = _item_refuted_by(db, check)

    payloads = _review(db, monkeypatch, item_id)

    assert len(payloads) == 1
    assert payloads[0]["text"].startswith("NEEDS WORK:")
    assert gate_refutation_problem(payloads[0]) == problem
    assert count_gate_refutations(payloads) == {problem: 1}


def test_an_unconfirmed_delivery_is_counted_under_its_own_name(db, monkeypatch):
    item_id = _item_refuted_by(db, "files_written", outcome="unverifiable")

    payloads = _review(db, monkeypatch, item_id)

    assert count_gate_refutations(payloads) == {GATE_UNCONFIRMED: 1}


def test_the_block_at_the_rework_limit_is_counted_with_its_problem(db, monkeypatch):
    item_id = _item_refuted_by(db, "grounded")
    item = db.get(BacklogItem, item_id)
    history = []
    for attempt in range(qa_review.AUTOPILOT_MAX_REWORK_CYCLES):
        history.append(
            {
                "author": "Verifier",
                "text": f"NEEDS WORK: {INVENTED} — x",
                "created_at": "",
            }
        )
        history.append(
            {
                "author": "Dev",
                "text": f"{DELIVERABLE} retry {attempt}",
                "created_at": "",
            }
        )
    item.comments = item.comments + history
    db.commit()

    payloads = _review(db, monkeypatch, item_id)

    # The block, then the escalation to the ScrumMaster (no text: not counted).
    assert "blocked for manual triage" in payloads[0]["text"]
    assert gate_refutation_problem(payloads[0]) == INVENTED
    assert count_gate_refutations(payloads) == {INVENTED: 1}


# ---------------------------------------------------------------------------
# What is left out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "comment",
    [
        {"author": "QA", "text": f"NEEDS WORK: {INVENTED} — the import is wrong"},
        {"author": "user", "text": f"NEEDS WORK: {INVENTED} — x"},
        {"author": "Quality gate", "text": "NEEDS WORK: deliverable too short — x"},
        {"author": "system", "text": f"NEEDS WORK: {INVENTED} — x"},
        {"author": "Verifier", "text": "NEEDS WORK: no separator here"},
        {"author": "Verifier", "text": "APPROVED: fine"},
        {"author": "Verifier", "text": ""},
        "not a comment",
    ],
)
def test_what_is_not_a_send_back_by_the_gate_is_not_counted(comment):
    assert gate_refutation_problem(comment) is None
    assert count_gate_refutations([comment]) == {}


def test_the_gate_count_and_the_reviewers_count_never_mix():
    comments = [
        {"author": "Verifier", "text": f"NEEDS WORK: {INVENTED} — x. Fix it."},
        {"author": "Verifier", "text": "NEEDS WORK: tests failed — 2 failed. Fix it."},
        {"author": "QA", "text": "NEEDS WORK: add input validation"},
    ]

    assert count_gate_refutations(comments) == {INVENTED: 1, "tests failed": 1}
    assert count_qa_rework_requests(comments) == 1


# ---------------------------------------------------------------------------
# The read route
# ---------------------------------------------------------------------------


def _gate_event(session, item, problem, created_at, author="Verifier"):
    event = task_events.record(
        session,
        item,
        "reviewed",
        task_events.GATE,
        author=author,
        status="todo",
        text=f"NEEDS WORK: {problem} — observed. Fix it.",
    )
    # Recorded now; moved to the day the test needs, as an older event is.
    event.created_at = created_at
    session.add(event)
    session.commit()


def test_the_route_counts_a_company_and_a_sprint_over_a_period(test_client, db):
    sprint = _company_and_sprint(db)
    other = Sprint(company_id=sprint.company_id, sprint_name="T", scope_size="S")
    db.add(other)
    db.commit()
    db.refresh(other)
    items = []
    for owner in (sprint, other):
        item = BacklogItem(sprint_id=owner.id, title="t", status="in_review")
        db.add(item)
        db.commit()
        db.refresh(item)
        items.append(item)
    today = datetime.now().replace(microsecond=0)
    _gate_event(db, items[0], INVENTED, today)
    _gate_event(db, items[0], GATE_UNCONFIRMED, today)
    _gate_event(db, items[1], INVENTED, today)
    _gate_event(db, items[1], "tests failed", today - timedelta(days=90))
    _gate_event(db, items[1], "too short", today, author="Quality gate")

    url = f"/v1/analytics/gate_refutations?company_id={sprint.company_id}"
    company = test_client.get(url)
    assert company.status_code == 200, company.text
    body = company.json()
    assert body["by_problem"] == {GATE_UNCONFIRMED: 1, INVENTED: 2}
    assert body["refuted"] == 2
    assert body["unconfirmed"] == 1
    assert sum(sum(w["by_problem"].values()) for w in body["weeks"]) == 3

    one_sprint = test_client.get(f"{url}&sprint_id={sprint.id}").json()
    assert one_sprint["by_problem"] == {GATE_UNCONFIRMED: 1, INVENTED: 1}

    since = (today - timedelta(days=120)).date().isoformat()
    longer = test_client.get(f"{url}&since={since}").json()
    assert longer["by_problem"]["tests failed"] == 1


def test_the_route_refuses_another_companys_sprint_and_a_reversed_period(
    test_client, db
):
    sprint = _company_and_sprint(db)
    elsewhere = _company_and_sprint(db)
    url = f"/v1/analytics/gate_refutations?company_id={sprint.company_id}"

    assert test_client.get(f"{url}&sprint_id={elsewhere.id}").status_code == 404
    reversed_period = f"{url}&since=2026-02-01&until=2026-01-01"
    assert test_client.get(reversed_period).status_code == 400
