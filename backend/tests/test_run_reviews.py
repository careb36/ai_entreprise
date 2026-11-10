"""
Reviewing a run like a pull request: comments on the changed lines, then a
review that comments, approves or requests changes.

A task the company's agents delivered is decided by the review loop, so a
person's review acts only where the loop waits for a person: requesting
changes sends a task blocked for a person back to work with the comments as
its ``NEEDS WORK:`` feedback, and approving accepts a delivery QA approved
that waits for a person. Another producer's run gets an approval record.
"""

import hashlib
import importlib.util
import secrets
from pathlib import Path

import auth_tokens
import pytest
from config import settings
from models import Company, CompanyMember, User
from models.agent_run import (
    TASK_WORKER_PRODUCER,
    AgentRun,
    AgentRunApproval,
    Verification,
)
from models.review import ReviewComment, RunReview
from models.sprint import BacklogItem, Sprint
from services.tasks.board import (
    RISK_HOLD_PREFIX,
    is_send_back,
    pending_rework_feedback,
)
from sqlmodel import select

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_reviews", _DAEMON_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()


@pytest.fixture(autouse=True)
def enforcement(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session, company_id, role, name=None):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=name or role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def company(sqlite_session, tmp_path):
    company = Company(company_name=f"Review {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    return {
        "id": company.id,
        "manager": _member(sqlite_session, company.id, "Manager", "Ana Manager"),
        "viewer": _member(sqlite_session, company.id, "Viewer"),
        "outsider": _member(sqlite_session, None, "Owner"),
        "workspace": tmp_path,
    }


def _task_run(
    session, company, *, status="blocked", comments=None, sprint_status="active"
):
    sprint = Sprint(
        company_id=company["id"], sprint_name="Sprint 1", status=sprint_status
    )
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Encrypt stored credentials",
        status=status,
        assignee="Developer",
        comments=comments
        if comments is not None
        else [
            {
                "author": "Developer",
                "text": "Here is the change.",
                "created_at": "2026-11-03 10:00:00",
            },
            {
                "author": "QA",
                "text": "NEEDS WORK: tests missing",
                "created_at": "2026-11-03 10:05:00",
            },
            {
                "author": "QA",
                "text": "NEEDS WORK: still no tests",
                "created_at": "2026-11-03 10:10:00",
            },
            {
                "author": "System",
                "text": "QA review failed — manual triage needed",
                "created_at": "2026-11-03 10:11:00",
            },
        ],
    )
    session.add(item)
    session.commit()
    run = _run(
        session,
        company,
        producer=TASK_WORKER_PRODUCER,
        subject={
            "sprint_id": sprint.id,
            "backlog_item_id": item.id,
            "title": item.title,
        },
    )
    return run, item


def _run(session, company, **fields):
    artifact = fields.pop("artifact_text", "the answer")
    run = AgentRun(
        company_id=company["id"],
        external_ref=f"task-{secrets.token_hex(3)}",
        producer=fields.pop("producer", "copilot"),
        artifact_text=artifact,
        artifact_digest=hashlib.sha256(artifact.encode()).hexdigest(),
        artifact_size=len(artifact),
        **fields,
    )
    session.add(run)
    session.commit()
    writes = daemon.write_files(
        str(company["workspace"]),
        [{"path": "app/crypto.py", "content": "def encrypt(v):\n    return v\n"}],
    )
    session.add(
        Verification(
            company_id=company["id"],
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={"workspace_writes": writes},
        )
    )
    session.commit()
    return run


def _comment(client, company, run, headers=None, **body):
    payload = {
        "company_id": company["id"],
        "path": "app/crypto.py",
        "line": 2,
        "body": "Encrypt, don't echo.",
    }
    payload.update(body)
    return client.post(
        f"/v1/agent_runs/{run.id}/comments",
        json=payload,
        headers=headers or company["manager"],
    )


def _review(client, company, run, event, body=None, headers=None):
    return client.post(
        f"/v1/agent_runs/{run.id}/reviews",
        json={"company_id": company["id"], "event": event, "body": body},
        headers=headers or company["manager"],
    )


def test_a_manager_comments_on_a_changed_line_and_a_viewer_reads_it(
    sqlite_session, test_client, company
):
    run = _run(sqlite_session, company)

    created = _comment(test_client, company, run)

    assert created.status_code == 201
    assert created.json()["author_name"] == "Ana Manager"
    listed = test_client.get(
        f"/v1/agent_runs/{run.id}/comments",
        params={"company_id": company["id"]},
        headers=company["viewer"],
    )
    assert [
        (c["path"], c["line"], c["side"], c["body"]) for c in listed.json()["comments"]
    ] == [("app/crypto.py", 2, "new", "Encrypt, don't echo.")]


def test_only_a_manager_of_the_company_writes_comments_and_reviews(
    sqlite_session, test_client, company
):
    run = _run(sqlite_session, company)

    assert (
        _comment(test_client, company, run, headers=company["viewer"]).status_code
        == 403
    )
    assert (
        _comment(test_client, company, run, headers=company["outsider"]).status_code
        == 403
    )
    assert (
        _review(
            test_client, company, run, "comment", "fine", headers=company["viewer"]
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            f"/v1/agent_runs/{run.id}/reviews",
            params={"company_id": company["id"]},
            headers=company["outsider"],
        ).status_code
        == 403
    )


def test_a_comment_names_a_file_the_run_changed_and_a_line_needs_its_file(
    sqlite_session, test_client, company
):
    run = _run(sqlite_session, company)

    assert _comment(test_client, company, run, path="README.md").status_code == 422
    assert _comment(test_client, company, run, path=None, line=3).status_code == 422
    assert _comment(test_client, company, run, line=0).status_code == 422
    assert _comment(test_client, company, run, side="left").status_code == 422
    assert _comment(test_client, company, run, body="   ").status_code == 422
    # A comment on the run as a whole needs no file.
    assert _comment(test_client, company, run, path=None, line=None).status_code == 201


def test_requesting_changes_on_another_producer_s_run_records_the_rejection(
    sqlite_session, test_client, company
):
    run = _run(sqlite_session, company)
    _comment(test_client, company, run)
    _comment(test_client, company, run, line=1, side="old", body="Keep the old name.")

    response = _review(test_client, company, run, "request_changes", "Not yet.")

    assert response.status_code == 201
    review = response.json()
    assert (review["event"], review["outcome"]) == (
        "request_changes",
        "approval_recorded",
    )
    approval = sqlite_session.exec(
        select(AgentRunApproval).where(AgentRunApproval.agent_run_id == run.id)
    ).one()
    assert (approval.decision, approval.approver_name) == ("rejected", "Ana Manager")
    assert approval.feedback == (
        "NEEDS WORK: review by Ana Manager: Not yet.\n"
        "- app/crypto.py:2: Encrypt, don't echo.\n"
        "- app/crypto.py:1 (old): Keep the old name."
    )
    carried = sqlite_session.exec(
        select(ReviewComment).where(ReviewComment.agent_run_id == run.id)
    ).all()
    assert {c.review_id for c in carried} == {review["id"]}
    # The next review starts with no comments: the first one carried them.
    _review(test_client, company, run, "approve", "Now it is fine.")
    approvals = sqlite_session.exec(
        select(AgentRunApproval)
        .where(AgentRunApproval.agent_run_id == run.id)
        .order_by(AgentRunApproval.id)
    ).all()
    assert [(a.decision, a.feedback) for a in approvals][1] == (
        "approved",
        "Now it is fine.",
    )


def test_requesting_changes_sends_a_task_blocked_for_a_person_back_with_its_comments(
    sqlite_session, test_client, company
):
    run, item = _task_run(sqlite_session, company)
    _comment(test_client, company, run)

    response = _review(
        test_client, company, run, "request_changes", "Add the tests QA asked for."
    )

    assert response.status_code == 201
    assert response.json()["outcome"] == "sent_back"
    sqlite_session.refresh(item)
    assert item.status == "todo"
    send_back, feedback = item.comments[-2:]
    assert is_send_back(send_back)
    assert feedback["author"] == "user"
    # What the Developer's next prompt carries: QA's requests on the same
    # deliverable, then the person's, file by line.
    assert pending_rework_feedback(item.comments) == [
        "QA: NEEDS WORK: tests missing",
        "QA: NEEDS WORK: still no tests",
        (
            "user: NEEDS WORK: review by Ana Manager: Add the tests QA asked for.\n"
            "- app/crypto.py:2: Encrypt, don't echo."
        ),
    ]
    # Another producer's approval record is not written for an in-house run.
    assert (
        sqlite_session.exec(
            select(AgentRunApproval).where(AgentRunApproval.agent_run_id == run.id)
        ).all()
        == []
    )


def test_a_task_still_in_the_agents_hands_is_not_sent_back(
    sqlite_session, test_client, company
):
    run, item = _task_run(sqlite_session, company, status="in_progress", comments=[])

    response = _review(test_client, company, run, "request_changes", "Stop.")

    assert response.status_code == 409
    assert "in the agents' hands" in response.json()["detail"]
    sqlite_session.refresh(item)
    assert item.status == "in_progress"
    assert (
        sqlite_session.exec(
            select(RunReview).where(RunReview.agent_run_id == run.id)
        ).all()
        == []
    )


def test_a_closed_sprint_s_task_is_carried_over_instead(
    sqlite_session, test_client, company
):
    run, _ = _task_run(sqlite_session, company, sprint_status="completed")

    response = _review(test_client, company, run, "request_changes", "Again.")

    assert response.status_code == 409
    assert "carry the task" in response.json()["detail"]


def test_approving_accepts_a_delivery_that_waits_for_a_person(
    sqlite_session, test_client, company
):
    held = [
        {
            "author": "Developer",
            "text": "Here is the change.",
            "created_at": "2026-11-03 10:00:00",
        },
        {"author": "QA", "text": "APPROVED: good", "created_at": "2026-11-03 10:05:00"},
        {
            "author": "System",
            "text": f"{RISK_HOLD_PREFIX} (path app/auth.py). Mark it done to accept it.",
            "created_at": "2026-11-03 10:05:01",
        },
    ]
    run, item = _task_run(sqlite_session, company, comments=held)

    response = _review(test_client, company, run, "approve")

    assert response.status_code == 201
    assert response.json()["outcome"] == "accepted"
    sqlite_session.refresh(item)
    assert item.status == "done"
    assert item.comments[-1]["author"] == "user"
    assert item.comments[-1]["text"].startswith(
        "High-risk delivery accepted by a person"
    )


def test_approving_a_task_qa_has_not_approved_is_refused(
    sqlite_session, test_client, company
):
    run, item = _task_run(sqlite_session, company)

    response = _review(test_client, company, run, "approve")

    assert response.status_code == 409
    assert "QA decides this task" in response.json()["detail"]
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_changes_need_something_to_change_and_a_comment_review_changes_nothing(
    sqlite_session, test_client, company
):
    run, item = _task_run(sqlite_session, company)

    assert _review(test_client, company, run, "request_changes").status_code == 422
    assert _review(test_client, company, run, "merge", "x").status_code == 422
    commented = _review(test_client, company, run, "comment", "Looking.")
    assert (commented.status_code, commented.json()["outcome"]) == (201, "recorded")
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    listed = test_client.get(
        f"/v1/agent_runs/{run.id}/reviews",
        params={"company_id": company["id"]},
        headers=company["viewer"],
    ).json()["reviews"]
    assert [(r["event"], r["body"], r["outcome"]) for r in listed] == [
        ("comment", "Looking.", "recorded")
    ]


def test_a_resolved_comment_is_left_out_of_the_next_review(
    sqlite_session, test_client, company
):
    run = _run(sqlite_session, company)
    resolved = _comment(test_client, company, run, body="Typo.").json()
    _comment(test_client, company, run, body="Rename it.")

    answer = test_client.post(
        f"/v1/agent_runs/{run.id}/comments/{resolved['id']}/resolve",
        params={"company_id": company["id"]},
        headers=company["manager"],
    )
    assert answer.status_code == 200
    assert answer.json()["resolved_at"] is not None
    _review(test_client, company, run, "request_changes")

    approval = sqlite_session.exec(
        select(AgentRunApproval).where(AgentRunApproval.agent_run_id == run.id)
    ).one()
    assert "Rename it." in approval.feedback
    assert "Typo." not in approval.feedback


def test_another_company_s_run_is_not_found(sqlite_session, test_client, company):
    run = _run(sqlite_session, company)
    other = Company(company_name="Other")
    sqlite_session.add(other)
    sqlite_session.commit()
    other_manager = _member(sqlite_session, other.id, "Manager")

    response = test_client.post(
        f"/v1/agent_runs/{run.id}/reviews",
        json={"company_id": other.id, "event": "comment", "body": "hi"},
        headers=other_manager,
    )
    assert response.status_code == 404
