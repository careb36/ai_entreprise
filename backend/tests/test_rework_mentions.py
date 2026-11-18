"""
Rework requests whose text names a missing module, package or import, before
and after the grounded check shipped (issue #549, the second measure).

A rework request's text is what a reviewer wrote: a claim. These tests pin the
matching rule to real-shaped texts (the gate's own wording as the review cycle
writes it, QA's tracebacks, the package managers' errors), say which task
events are rework requests, and read the before/after split back through
``GET /v1/analytics/missing_module_mentions``.
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
from services.loop import task_worker
from services.loop.worker import qa_review
from services.loop.worker import review as worker_review
from services.platform.rework_mentions import (
    GROUNDED_CHECK_SHIPPED_AT,
    count_rework_mentions,
    names_missing_module,
    rework_request_source,
)
from services.tasks import task_events
from sqlmodel import Session, select

DELIVERABLE = "# Artifact: src/a.py\n\nprint('a')\n" + "word " * 120
# The observation ``services/evidence/grounding.py`` writes for a refuted
# delivery, in its own words (``no registry has …``, ``import(s) name no file``).
GROUNDED_OBSERVATION = (
    "no registry has requests-magic; 1 import(s) name no file: src/app.py: helpers"
)


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
    company = Company(company_name=f"Mentions {uuid4().hex[:8]}", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session: Session, sprint: Sprint, **fields) -> BacklogItem:
    fields.setdefault("title", "t")
    item = BacklogItem(sprint_id=sprint.id, status="in_review", **fields)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# The matching rule, on real-shaped texts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The gate's own send-back after the grounded check (#503).
        (
            f"NEEDS WORK: invented dependency or import — {GROUNDED_OBSERVATION}. "
            "Fix it and deliver again."
        ),
        (
            "NEEDS WORK: the tests fail with ModuleNotFoundError: No module named "
            "'requests_magic'"
        ),
        "NEEDS WORK: running pytest gives `No module named app.helpers`.",
        'NEEDS WORK: go build: cannot find package "github.com/acme/fastjson"',
        (
            "NEEDS WORK: tsc reports Cannot find module 'lodash-es' or its "
            "corresponding type declarations."
        ),
        "NEEDS WORK: Module not found: Error: Can't resolve './utils/format'",
        "NEEDS WORK: npm ERR! 404 'left-padder@^2.0.0' is not in this registry.",
        (
            "NEEDS WORK: pip install fails: ERROR: No matching distribution found "
            "for fastjsonx==9.1"
        ),
        (
            "NEEDS WORK: Could not find a version that satisfies the requirement "
            "pydantic-magic"
        ),
        "NEEDS WORK: error[E0432]: unresolved import `crate::helpers`",
        "NEEDS WORK: javac: package com.acme.util does not exist",
        "NEEDS WORK: ImportError: cannot import name 'parse_rows' from 'app.csv'",
        "NEEDS WORK: go: no required module provides package example.com/x/y",
        "NEEDS WORK: the delivery is missing a dependency in requirements.txt",
        "NEEDS WORK: package 'fastjsonx' not found on PyPI",
        "NEEDS WORK: this imports a hallucinated package",
        "Sent back to work: the build stops on a missing module.",
    ],
)
def test_texts_that_name_a_missing_module_or_package_match(text):
    assert names_missing_module(text)


@pytest.mark.parametrize(
    "text",
    [
        "NEEDS WORK: add input validation to the form",
        "NEEDS WORK: the import page is missing a cancel button",
        "NEEDS WORK: the module docstring is missing",
        "NEEDS WORK: imports are not sorted",
        "NEEDS WORK: missing tests for the parser",
        "NEEDS WORK: tests failed — 2 failed, 10 passed. Fix it.",
        "NEEDS WORK: the package.json script name is wrong",
        "APPROVED: the dependency list is right",
        "",
    ],
)
def test_texts_that_do_not_name_a_missing_module_do_not_match(text):
    assert not names_missing_module(text)


def test_a_non_text_never_matches():
    assert not names_missing_module(None)
    assert not names_missing_module(42)


def test_the_gate_wording_the_review_cycle_writes_matches(db, monkeypatch):
    """The real loop's send-back for a refuted grounded check, not a copy of it."""
    sprint = _company_and_sprint(db)
    item = _item(
        db,
        sprint,
        title=f"Write a {uuid4().hex[:8]}",
        assignee="Developer",
        comments=[{"author": "Dev", "text": DELIVERABLE, "created_at": ""}],
    )
    run = AgentRun(
        company_id=sprint.company_id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        claims={"files": ["src/a.py"]},
        artifact_digest="d",
        artifact_size=1,
        status="refuted",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    db.add(
        Verification(
            company_id=sprint.company_id,
            agent_run_id=run.id,
            verdict="refuted",
            checks=[
                {
                    "name": "grounded",
                    "outcome": "refuted",
                    "observation": GROUNDED_OBSERVATION,
                }
            ],
        )
    )
    db.commit()
    monkeypatch.setattr(qa_review, "_call_llm_for_review", lambda *a, **k: None)

    asyncio.run(worker_review._review_cycle(_FakeNATS()))

    db.expire_all()
    events = db.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == item.id, TaskEvent.event_type == "reviewed")
        .order_by(TaskEvent.id)
    ).all()
    assert len(events) == 1
    event = events[0]
    assert rework_request_source(event.event_type, event.actor, event.payload) == (
        "gate"
    )
    assert names_missing_module(event.payload["text"])


# ---------------------------------------------------------------------------
# Which task events are rework requests, and whose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, actor, payload, source",
    [
        (
            "reviewed",
            "QA",
            {"verdict": "needs_work", "text": "NEEDS WORK: x"},
            "agent_reviewer",
        ),
        (
            "reviewed",
            "gate",
            {"verdict": "needs_work", "text": "NEEDS WORK: x — y"},
            "gate",
        ),
        (
            "reviewed",
            "person",
            {"verdict": "needs_work", "text": "NEEDS WORK: x"},
            "person",
        ),
        # A person's review feedback (``run_review``) carries no verdict flag.
        (
            "reviewed",
            "person",
            {"text": "The import is wrong", "review_id": 3},
            "person",
        ),
        (
            "sent_back",
            "person",
            {"text": "Sent back to work: the build fails"},
            "person",
        ),
        (
            "sent_back",
            "Manager",
            {"author": "Manager agent", "text": "Sent back to work: x"},
            "agent_reviewer",
        ),
        # Not rework requests:
        ("reviewed", "QA", {"verdict": "approved", "text": "APPROVED: fine"}, None),
        ("reviewed", "QA", {"verdict": None, "text": "Looks fine"}, None),
        (
            "sent_back",
            "system",
            {"text": "Autopilot rework scheduled after QA feedback (1/3)."},
            None,
        ),
        (
            "reviewed",
            "system",
            {"verdict": "needs_work", "text": "NEEDS WORK: x"},
            None,
        ),
        # The reasons of a person's send-back in a review are in its ``reviewed`` event.
        (
            "sent_back",
            "person",
            {"text": "Sent back to work by a person, in review #3.", "review_id": 3},
            None,
        ),
        ("delivered", "Developer", {"text": "NEEDS WORK: ModuleNotFoundError"}, None),
        ("reviewed", "QA", "not a payload", None),
    ],
)
def test_which_events_are_rework_requests_and_from_whom(kind, actor, payload, source):
    assert rework_request_source(kind, actor, payload) == source


def test_the_count_splits_requests_and_mentions_by_source():
    missing = {"verdict": "needs_work", "text": "NEEDS WORK: No module named 'x'"}
    other = {"verdict": "needs_work", "text": "NEEDS WORK: add validation"}
    gate = {
        "verdict": "needs_work",
        "text": f"NEEDS WORK: invented dependency or import — {GROUNDED_OBSERVATION}.",
    }
    events = [
        ("reviewed", "QA", missing),
        ("reviewed", "QA", other),
        ("reviewed", "gate", gate),
        ("reviewed", "person", {"text": "cannot find package foo", "review_id": 1}),
        ("sent_back", "system", {"text": "Autopilot rework scheduled"}),
        ("delivered", "Developer", missing),
    ]

    counts = count_rework_mentions(events)

    assert counts == {
        "rework_requests": {"agent_reviewer": 2, "gate": 1, "person": 1},
        "requests_whose_text_names_missing_module": {
            "agent_reviewer": 1,
            "gate": 1,
            "person": 1,
        },
    }


def test_the_default_cutoff_is_when_the_grounded_check_merged():
    # Pull request #503, merged 2026-11-12T12:33:51Z; naive UTC like TaskEvent.created_at.
    assert GROUNDED_CHECK_SHIPPED_AT == datetime(2026, 11, 12, 12, 33, 51)


# ---------------------------------------------------------------------------
# The read route
# ---------------------------------------------------------------------------


def _event(session, item, kind, actor, text, created_at, **extra):
    event = task_events.record(session, item, kind, actor, text=text, **extra)
    # Recorded now; moved to the moment the test needs, as an older event is.
    event.created_at = created_at
    session.add(event)
    session.commit()


def _seed(db):
    sprint = _company_and_sprint(db)
    other = Sprint(company_id=sprint.company_id, sprint_name="T", scope_size="S")
    db.add(other)
    db.commit()
    db.refresh(other)
    first, second = _item(db, sprint), _item(db, other)
    before = GROUNDED_CHECK_SHIPPED_AT - timedelta(days=3)
    after = GROUNDED_CHECK_SHIPPED_AT + timedelta(hours=2)
    qa = {"author": "QA", "payload": {"verdict": "needs_work"}}
    _event(
        db,
        first,
        "reviewed",
        "QA",
        "NEEDS WORK: ModuleNotFoundError: No module named 'x'",
        before,
        **qa,
    )
    _event(
        db, first, "reviewed", "QA", "NEEDS WORK: cannot find package foo", before, **qa
    )
    _event(db, second, "reviewed", "QA", "NEEDS WORK: add validation", before, **qa)
    _event(db, first, "reviewed", "QA", "NEEDS WORK: add validation", after, **qa)
    _event(
        db,
        second,
        "reviewed",
        task_events.GATE,
        f"NEEDS WORK: invented dependency or import — {GROUNDED_OBSERVATION}. Fix it.",
        after,
        author="Verifier",
    )
    _event(db, second, "reviewed", "QA", "NEEDS WORK: No module named 'y'", after, **qa)
    return sprint, first, second


def test_the_route_splits_at_the_grounded_checks_merge_by_default(test_client, db):
    sprint, _, _ = _seed(db)
    url = f"/v1/analytics/missing_module_mentions?company_id={sprint.company_id}"
    until = (GROUNDED_CHECK_SHIPPED_AT + timedelta(days=1)).date().isoformat()

    response = test_client.get(f"{url}&until={until}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cutoff"] == "2026-11-12T12:33:51"
    assert body["cutoff_is_default"] is True
    assert "not whether" in body["counts_what"]
    assert body["before"]["rework_requests"] == {"agent_reviewer": 3}
    assert body["before"]["requests_whose_text_names_missing_module"] == {
        "agent_reviewer": 2
    }
    assert body["after"]["rework_requests"] == {"agent_reviewer": 2, "gate": 1}
    assert body["after"]["requests_whose_text_names_missing_module"] == {
        "agent_reviewer": 1,
        "gate": 1,
    }
    assert body["before"]["until"] == body["after"]["since"] == body["cutoff"]
    periods = {(w["week"], w["period"]) for w in body["weeks"]}
    # 2026-11-09 (before) and 2026-11-12 (after) are both in ISO week 46.
    assert periods == {("2026-W46", "before"), ("2026-W46", "after")}
    total = sum(
        sum(w["requests_whose_text_names_missing_module"].values())
        for w in body["weeks"]
    )
    assert total == 4


def test_the_route_takes_a_cutoff_and_a_sprint(test_client, db):
    sprint, _, _ = _seed(db)
    url = f"/v1/analytics/missing_module_mentions?company_id={sprint.company_id}"
    until = (GROUNDED_CHECK_SHIPPED_AT + timedelta(days=1)).date().isoformat()
    cutoff = (GROUNDED_CHECK_SHIPPED_AT - timedelta(days=10)).isoformat()

    moved = test_client.get(f"{url}&until={until}&cutoff={cutoff}").json()
    assert moved["cutoff_is_default"] is False
    assert moved["before"]["rework_requests"] == {}
    assert sum(moved["after"]["requests_whose_text_names_missing_module"].values()) == 4

    one_sprint = test_client.get(f"{url}&until={until}&sprint_id={sprint.id}").json()
    assert one_sprint["before"]["requests_whose_text_names_missing_module"] == {
        "agent_reviewer": 2
    }
    assert one_sprint["after"]["requests_whose_text_names_missing_module"] == {}


def test_the_route_refuses_another_companys_sprint_and_a_reversed_period(
    test_client, db
):
    sprint = _company_and_sprint(db)
    elsewhere = _company_and_sprint(db)
    url = f"/v1/analytics/missing_module_mentions?company_id={sprint.company_id}"

    assert test_client.get(f"{url}&sprint_id={elsewhere.id}").status_code == 404
    reversed_period = f"{url}&since=2026-02-01&until=2026-01-01"
    assert test_client.get(reversed_period).status_code == 400
