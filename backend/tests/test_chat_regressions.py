"""Regressions posted to Slack and Teams (issue #682, split from #512's chat phase).

A regression is an observation, never an agent's claim:

- a sprint integration whose test run failed (``tests_failed``, the daemon's
  exit code) on the same repository and base, with the same test command, as
  the company's previous integration whose run passed (``tests_passed``);
- a refuted verification of a run whose previous verification was
  ``verified``, while the run's task is done (accepted).

Each is queued once per enabled connection of the company in the transaction
that recorded it, delivered through the outbox with a link to the evidence,
and carries no button.
"""

import base64
import secrets
import uuid

import pytest
from chat_fakes import AGENT_CLAIM, member, setup
from models import (
    AgentRun,
    BacklogItem,
    ChatConnection,
    Company,
    OutboxEvent,
    Sprint,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from models.integration import SprintIntegration
from services.integrations import chat_connections, chat_regressions
from sqlalchemy import delete
from sqlmodel import select

COMMAND = "pytest -q"


@pytest.fixture(autouse=True)
def drain_regression_outbox(sqlite_session):
    """Remove the rows these tests leave in the shared engine's outbox."""
    yield
    sqlite_session.rollback()
    sqlite_session.exec(
        delete(OutboxEvent).where(OutboxEvent.event_type == chat_regressions.POST)
    )
    sqlite_session.commit()


@pytest.fixture
def w(sqlite_session, monkeypatch):
    team_id = "T" + secrets.token_hex(4).upper()
    fake = setup(monkeypatch, team_id)
    chat_regressions.install()
    session = sqlite_session
    company = Company(company_name=f"Regressions {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    owner, _ = member(session, company.id, "Owner")
    slack, _ = chat_connections.save(
        session,
        company.id,
        "slack",
        workspace_id=team_id,
        channel="C" + secrets.token_hex(4).upper(),
        post_credential="xoxb-" + secrets.token_hex(12),
        signing_secret=secrets.token_hex(16),
        enabled=True,
        actor=owner.email,
    )
    teams, _ = chat_connections.save(
        session,
        company.id,
        "teams",
        workspace_id=str(uuid.uuid4()),
        channel=None,
        post_credential=f"https://fabrique-test.webhook.office.com/webhookb2/{secrets.token_hex(8)}",
        signing_secret=base64.b64encode(secrets.token_bytes(32)).decode(),
        enabled=True,
        actor=owner.email,
    )
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="Sprint 7", status="completed")
    session.add(sprint)
    session.commit()
    return {
        "session": session,
        "fake": fake,
        "company": company,
        "owner": owner,
        "slack": slack,
        "teams": teams,
        "sprint": sprint,
    }


def _rows(session, connection_ids):
    return [
        row
        for row in session.exec(
            select(OutboxEvent).where(OutboxEvent.event_type == chat_regressions.POST)
        ).all()
        if row.payload.get("connection_id") in connection_ids
    ]


def _deliver(session, connection_ids):
    import asyncio

    errors = []
    for row in _rows(session, connection_ids):
        if row.status != "pending":
            continue
        try:
            asyncio.run(chat_regressions.deliver(session, row.payload, row.id))
            row.status = "completed"
        except Exception as exc:  # noqa: BLE001 - Returned for the test to inspect.
            errors.append(exc)
            row.retry_count = (row.retry_count or 0) + 1
        session.add(row)
    session.commit()
    return errors


def _integration(w, status, *, command=COMMAND, base="main", repository="/w/app"):
    row = SprintIntegration(
        company_id=w["company"].id,
        sprint_id=w["sprint"].id,
        trigger="sprint_closed",
        requested_by="task_worker",
        status=status,
        branch=f"fabrique/sprint-{w['sprint'].id}",
        base=base,
        repository=repository,
        commit=secrets.token_hex(20),
        tested_commit=secrets.token_hex(20),
        test_run={
            "command": command,
            "exit_code": 0 if status == "tests_passed" else 1,
            "output_tail": "1 failed" if status == "tests_failed" else "ok",
        },
    )
    w["session"].add(row)
    w["session"].commit()
    return row


def _ids(w):
    return {w["slack"].id, w["teams"].id}


def _all_text(value) -> str:
    return repr(value)


# ---------------------------------------------------------------------------
# An integration suite that passed and now fails
# ---------------------------------------------------------------------------


def test_integration_regression_posts_once_per_connection_with_the_evidence(w):
    session = w["session"]
    passed = _integration(w, "tests_passed")
    failed = _integration(w, "tests_failed")

    rows = _rows(session, _ids(w))
    assert sorted(r.payload["connection_id"] for r in rows) == sorted(_ids(w))
    assert {r.payload["kind"] for r in rows} == {"integration"}
    assert {r.payload["subject_id"] for r in rows} == {failed.id}

    assert _deliver(session, _ids(w)) == []
    slack_posts = [p for m, p in w["fake"].slack if m == "chat.postMessage"]
    assert len(slack_posts) == 1 and len(w["fake"].teams) == 1
    slack_text = _all_text(slack_posts[0])
    assert f"/sprints/{w['sprint'].id}" in slack_text
    assert f"integration {failed.id}" in slack_text
    assert f"integration {passed.id}" in slack_text
    assert COMMAND in slack_text
    # Information only: no button, no action block.
    assert all(b["type"] != "actions" for b in slack_posts[0]["blocks"])
    assert "button" not in slack_text
    card = w["fake"].teams[0]["attachments"][0]["content"]
    assert {a["type"] for a in card.get("actions", [])} <= {"Action.OpenUrl"}
    assert f"/sprints/{w['sprint'].id}" in _all_text(card)
    assert w["slack"].last_status == "ok"


def test_an_existing_job_that_turns_failed_is_a_regression_too(w):
    session = w["session"]
    _integration(w, "tests_passed")
    job = _integration(w, "running")
    assert _rows(session, _ids(w)) == []
    job.status = "tests_failed"
    job.test_run = {"command": COMMAND, "exit_code": 2}
    session.add(job)
    session.commit()
    rows = _rows(session, _ids(w))
    assert len(rows) == 2 and {r.payload["subject_id"] for r in rows} == {job.id}


@pytest.mark.parametrize(
    "previous, changes",
    [
        (None, {}),  # nothing passed before
        ("tests_failed", {}),  # it was failing already
        ("untested", {}),  # no test outcome to compare with
        ("tests_passed", {"command": "npm test"}),  # another suite
        ("tests_passed", {"base": "release"}),  # another base
        ("tests_passed", {"repository": "/w/other"}),  # another project
    ],
)
def test_a_failure_that_is_not_a_regression_posts_nothing(w, previous, changes):
    if previous:
        _integration(w, previous)
    _integration(w, "tests_failed", **changes)
    assert _rows(w["session"], _ids(w)) == []


def test_the_latest_decided_integration_is_the_one_compared(w):
    _integration(w, "tests_passed")
    _integration(w, "tests_failed")  # the regression, posted
    _integration(w, "untested")  # no outcome: skipped when comparing
    _integration(w, "tests_failed")  # still failing: not a new regression
    assert len(_rows(w["session"], _ids(w))) == 2


def test_saving_the_failed_row_again_queues_nothing_more(w):
    session = w["session"]
    _integration(w, "tests_passed")
    failed = _integration(w, "tests_failed")
    failed.details = {"note": "read again"}
    session.add(failed)
    session.commit()
    assert len(_rows(session, _ids(w))) == 2


def test_a_duplicate_row_posts_once(w):
    session = w["session"]
    _integration(w, "tests_passed")
    failed = _integration(w, "tests_failed")
    # A second row for the same regression (a race between replicas).
    session.add(
        OutboxEvent(
            aggregate_type=chat_regressions.AGGREGATE,
            aggregate_id=chat_regressions.key(w["slack"].id, "integration", failed.id),
            event_type=chat_regressions.POST,
            payload={
                "connection_id": w["slack"].id,
                "kind": "integration",
                "subject_id": failed.id,
            },
        )
    )
    session.commit()
    assert _deliver(session, {w["slack"].id}) == []
    assert len([m for m, _ in w["fake"].slack if m == "chat.postMessage"]) == 1


def test_a_rolled_back_regression_queues_nothing(w):
    session = w["session"]
    _integration(w, "tests_passed")
    row = SprintIntegration(
        company_id=w["company"].id,
        sprint_id=w["sprint"].id,
        trigger="manual",
        requested_by="human:1",
        status="tests_failed",
        branch="b",
        base="main",
        repository="/w/app",
        test_run={"command": COMMAND, "exit_code": 1},
    )
    session.add(row)
    session.flush()
    session.rollback()
    assert _rows(session, _ids(w)) == []


def test_only_enabled_connections_of_the_company_are_posted_to(w):
    session = w["session"]
    teams = session.get(ChatConnection, w["teams"].id)
    teams.enabled = False
    session.add(teams)
    other = Company(company_name=f"Other {secrets.token_hex(3)}")
    session.add(other)
    session.commit()
    _integration(w, "tests_passed")
    _integration(w, "tests_failed")
    rows = _rows(session, _ids(w))
    assert [r.payload["connection_id"] for r in rows] == [w["slack"].id]


def test_a_connection_turned_off_before_delivery_posts_nothing(w):
    session = w["session"]
    _integration(w, "tests_passed")
    _integration(w, "tests_failed")
    for row in (w["slack"], w["teams"]):
        connection = session.get(ChatConnection, row.id)
        connection.enabled = False
        session.add(connection)
    session.commit()
    assert _deliver(session, _ids(w)) == []
    assert w["fake"].slack == [] and w["fake"].teams == []


def test_a_failed_post_is_recorded_raised_and_retried_once(w):
    session = w["session"]
    _integration(w, "tests_passed")
    _integration(w, "tests_failed")
    w["fake"].fail_next = 1
    errors = _deliver(session, {w["slack"].id})
    assert len(errors) == 1
    session.refresh(w["slack"])
    assert w["slack"].last_status == "error"
    assert _deliver(session, {w["slack"].id}) == []
    assert len([m for m, _ in w["fake"].slack if m == "chat.postMessage"]) == 1


# ---------------------------------------------------------------------------
# An accepted delivery that later failed verification
# ---------------------------------------------------------------------------


def _done_task_run(w, status="done"):
    session = w["session"]
    item = BacklogItem(
        sprint_id=w["sprint"].id,
        title="Rotate the signing key",
        status=status,
        assignee="Developer",
        comments=[],
    )
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=w["company"].id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        subject={"sprint_id": w["sprint"].id, "backlog_item_id": item.id},
        claims={"summary": AGENT_CLAIM},
        artifact_text="x",
        artifact_digest="0" * 64,
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    return item, run


def _verify(w, run, verdict, checks=None):
    row = Verification(
        company_id=w["company"].id,
        agent_run_id=run.id,
        verdict=verdict,
        checks=checks or [{"name": "tests", "outcome": "pass"}],
    )
    w["session"].add(row)
    w["session"].commit()
    return row


def test_an_accepted_delivery_refuted_later_posts_with_the_failed_checks(w):
    session = w["session"]
    _item, run = _done_task_run(w)
    _verify(w, run, "verified")
    refuted = _verify(
        w, run, "refuted", [{"name": "tests", "outcome": "fail"}, {"name": "ok"}]
    )
    rows = _rows(session, _ids(w))
    assert len(rows) == 2
    assert {(r.payload["kind"], r.payload["subject_id"]) for r in rows} == {
        ("verification", refuted.id)
    }
    assert _deliver(session, _ids(w)) == []
    slack_post = next(p for m, p in w["fake"].slack if m == "chat.postMessage")
    text = _all_text(slack_post)
    assert f"/evidence/{run.id}" in text
    assert "tests: fail" in text
    assert AGENT_CLAIM not in text
    assert all(b["type"] != "actions" for b in slack_post["blocks"])
    card = _all_text(w["fake"].teams[0])
    assert f"/evidence/{run.id}" in card and AGENT_CLAIM not in card


@pytest.mark.parametrize(
    "status, previous",
    [
        ("in_progress", "verified"),  # not accepted: the delivery's own checks
        ("done", "refuted"),  # refuted already
        ("done", None),  # first verification
    ],
)
def test_a_refutation_that_is_not_a_regression_posts_nothing(w, status, previous):
    _item, run = _done_task_run(w, status)
    if previous:
        _verify(w, run, previous)
    _verify(w, run, "refuted")
    assert _rows(w["session"], _ids(w)) == []


# ---------------------------------------------------------------------------
# The message (pure)
# ---------------------------------------------------------------------------


def test_record_text_is_escaped_in_slack():
    from services.integrations import chat_regression_messages as messages

    facts = {
        "kind": "verification",
        "headline": "An accepted delivery failed verification",
        "title": "<!channel> <https://evil.example|click>",
        "lines": ["tests: fail"],
        "evidence_url": "https://app.example/evidence/1",
        "evidence_label": "Run 1 evidence",
    }
    blocks = messages.slack_blocks(facts)
    text = _all_text(blocks)
    assert "<!channel>" not in text and "<https://evil" not in text
    assert all(b["type"] != "actions" for b in blocks)
