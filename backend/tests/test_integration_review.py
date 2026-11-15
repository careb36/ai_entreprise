"""A person's review of a sprint's integration (F7 of the self-hosting retrospective).

The diff comes from the daemon (``vscode-daemon/git_diff.py``, here run
against a repository in ``tmp_path`` with no remote); a line comment is
anchored to it and keeps the file's hash; a review is recorded and audited
with its actor; with the company's setting on, the local merge needs an
approving review; and the person who started the sprint does not approve its
integration (segregation of duties, ``services/platform/sod.py``).
"""

import hashlib
import secrets
import sys
from pathlib import Path

import auth_tokens
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_diff
import git_workspace
import local_merge
from models import AuditLog, Company, CompanyMember, User
from models.integration import SprintIntegration
from models.integration_review import IntegrationReview, IntegrationReviewComment
from services.release import integration_review
from sqlmodel import select
from test_git_publish import _Daemon
from test_git_workflow import _company, enforcement, git  # noqa: F401
from test_local_merge import _integration, repo  # noqa: F401


class _ReviewingDaemon(_Daemon):
    """``request`` runs the real diff and merge against the repository on disk."""

    def __init__(self, commit=None):
        self.requests = []
        self.commit = commit

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        handler = {
            "workspace.git.diff": git_diff.integration_diff,
            "workspace.git.merge": local_merge.merge_local,
        }[subject]
        try:
            reply = handler(payload["workspace_root"], payload)
        except git_workspace.GitError as e:
            return {"error": {"status": 409, "message": str(e)}}
        if self.commit:
            reply["commit"] = self.commit
        return reply


def _person(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=f"{role} {secrets.token_hex(2)}",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(sqlite_session, test_client, repo, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session)
    sprint, row = _integration(sqlite_session, company, repo, "untested")
    daemon = _ReviewingDaemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", daemon)
    people = {
        role: _person(sqlite_session, company.id, role)
        for role in ("Viewer", "Manager", "Owner")
    }
    return {
        "company": company,
        "sprint": sprint,
        "row": row,
        "daemon": daemon,
        **people,
    }


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _reviews(session, row):
    return session.exec(
        select(IntegrationReview).where(IntegrationReview.integration_id == row.id)
    ).all()


# -- The daemon's diff ----------------------------------------------------------


def test_the_daemon_reads_the_diff_and_hashes_both_sides(repo):  # noqa: F811
    (repo / "gone.py").write_text("old = 1\n")
    git(repo, "add", "gone.py")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "fabrique/sprint-1")
    (repo / "s.py").write_text("s = 1\n")
    (repo / "README.md").write_text("# App\n\nMore.\n")
    git(repo, "rm", "-q", "gone.py")
    git(repo, "add", "s.py", "README.md")
    git(repo, "commit", "-q", "-m", "integrated")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")

    reply = git_diff.integration_diff(str(repo), {"commit": commit, "base": ""})

    assert reply["ok"] and reply["base"] == "main" and reply["commit"] == commit
    assert reply["merge_base"] == git(repo, "rev-parse", "main")
    files = {f["path"]: f for f in reply["files"]}
    assert {p: f["change"] for p, f in files.items()} == {
        "README.md": "modified",
        "gone.py": "deleted",
        "s.py": "added",
    }
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    assert (files["s.py"]["sha256_old"], files["s.py"]["sha256_new"]) == (
        None,
        sha("s = 1\n"),
    )
    assert files["gone.py"]["sha256_old"] == sha("old = 1\n")
    assert files["gone.py"]["sha256_new"] is None
    assert files["README.md"]["sha256_old"] == sha("# App\n")
    assert files["README.md"]["sha256_new"] == sha("# App\n\nMore.\n")
    assert files["s.py"]["patch"].startswith("@@ -0,0 +1 @@")
    with pytest.raises(git_workspace.GitError):
        git_diff.integration_diff(str(repo), {"commit": "not-a-commit"})
    with pytest.raises(git_workspace.GitError):
        git_diff.integration_diff(str(repo), {"commit": "1" * 40})


def test_the_lines_a_patch_shows_are_the_ones_a_comment_may_name():
    patch = "@@ -3,2 +3,3 @@\n keep\n-gone\n+new\n+more\n"
    old, new = integration_review.hunk_lines(patch)
    assert old == {3, 4} and new == {3, 4, 5}


# -- Comments anchored to the daemon's diff -------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_line_comment_is_anchored_to_the_daemons_diff(
    world, test_client, sqlite_session
):
    sprint, row = world["sprint"], world["row"]
    _, viewer = world["Viewer"]
    _, manager = world["Manager"]
    url = f"/v1/sprints/{sprint.id}/integration/comments"

    diff = test_client.get(f"/v1/sprints/{sprint.id}/integration/diff", headers=viewer)
    assert diff.status_code == 200, diff.text
    assert [f["path"] for f in diff.json()["files"]] == ["s.py"]
    assert len(diff.json()["diff_sha256"]) == 64

    assert (
        test_client.post(
            url, json={"path": "s.py", "line": 1, "body": "x"}, headers=viewer
        ).status_code
        == 403
    )
    outside = test_client.post(
        url, json={"path": "s.py", "line": 9, "body": "No"}, headers=manager
    )
    elsewhere = test_client.post(
        url, json={"path": "a.py", "line": 1, "body": "No"}, headers=manager
    )
    old_side = test_client.post(
        url,
        json={"path": "s.py", "line": 1, "side": "old", "body": "No"},
        headers=manager,
    )
    assert (outside.status_code, elsewhere.status_code, old_side.status_code) == (
        422,
        422,
        422,
    )
    assert "not in the integration's diff" in outside.json()["detail"]
    assert "not among the files" in elsewhere.json()["detail"]

    added = test_client.post(
        url,
        # A hash sent by the client is ignored: the daemon's is kept.
        json={
            "path": "s.py",
            "line": 1,
            "body": "Name it better",
            "file_sha256": "f" * 64,
        },
        headers=manager,
    )
    assert added.status_code == 201, added.text
    comment = added.json()
    assert comment["commit"] == row.commit and comment["integration_id"] == row.id
    assert comment["file_sha256"] == hashlib.sha256(b"s = 1\n").hexdigest()
    stale = test_client.post(
        url,
        json={"path": "s.py", "line": 1, "body": "x", "integration_id": row.id + 99},
        headers=manager,
    )
    assert stale.status_code == 409


@pytest.mark.usefixtures("enforcement")
def test_a_review_is_recorded_listed_and_audited_with_its_actor(
    world, test_client, sqlite_session
):
    company, sprint, row = world["company"], world["sprint"], world["row"]
    _, viewer = world["Viewer"]
    manager_user, manager = world["Manager"]
    base = f"/v1/sprints/{sprint.id}/integration"
    test_client.post(
        f"{base}/comments",
        json={"path": "s.py", "line": 1, "body": "Rename s"},
        headers=manager,
    )
    test_client.post(f"{base}/comments", json={"body": "Overall fine"}, headers=manager)

    assert (
        test_client.post(
            f"{base}/reviews", json={"event": "comment"}, headers=viewer
        ).status_code
        == 403
    )
    empty = test_client.post(
        f"{base}/reviews", json={"event": "request_changes"}, headers=manager
    )
    assert empty.status_code == 201  # carries the two open comments
    review = empty.json()
    assert (review["outcome"], review["commit"], review["files"]) == (
        "changes_requested",
        row.commit,
        1,
    )
    assert review["author_user_id"] == manager_user.id and review["audit_log_id"]
    again = test_client.post(
        f"{base}/reviews", json={"event": "request_changes"}, headers=manager
    )
    assert again.status_code == 422  # nothing left to carry, and no text

    listed = test_client.get(f"{base}/reviews", headers=viewer).json()
    assert [r["event"] for r in listed["reviews"]] == ["request_changes"]
    assert {c["review_id"] for c in listed["comments"]} == {review["id"]}
    assert (listed["approved"], listed["required"], listed["open_comments"]) == (
        False,
        False,
        0,
    )
    audit = _audits(sqlite_session, company.id, "integration_review_request_changes")
    assert len(audit) == 1 and audit[0].details["user_id"] == manager_user.id
    assert audit[0].agent_id == f"human:{manager_user.full_name}"
    assert audit[0].result["diff_sha256"] == review["diff_sha256"]
    state = test_client.get(base, headers=viewer).json()["review"]
    assert (
        state["latest"]["id"] == review["id"]
        and state["latest"]["event"] == "request_changes"
    )


@pytest.mark.usefixtures("enforcement")
def test_a_daemon_that_reads_another_commit_records_nothing(
    world, test_client, sqlite_session
):
    world["daemon"].commit = "1" * 40
    _, manager = world["Manager"]
    refused = test_client.post(
        f"/v1/sprints/{world['sprint'].id}/integration/reviews",
        json={"event": "comment"},
        headers=manager,
    )
    assert refused.status_code == 502
    assert not _reviews(sqlite_session, world["row"])


# -- The merge gate -------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_without_the_setting_the_merge_needs_no_review(world, test_client, repo):  # noqa: F811
    _, manager = world["Manager"]
    merged = test_client.post(
        f"/v1/sprints/{world['sprint'].id}/integration/merge", headers=manager
    )
    assert merged.status_code == 201, merged.text
    assert merged.json()["details"]["local_merge"]["review_id"] is None


@pytest.mark.usefixtures("enforcement")
def test_with_the_setting_on_the_merge_waits_for_an_approval(
    world,
    test_client,
    sqlite_session,
    repo,  # noqa: F811
):
    company, sprint, row = world["company"], world["sprint"], world["row"]
    _, viewer = world["Viewer"]
    _, manager = world["Manager"]
    _, owner = world["Owner"]
    setting = f"/v1/companies/{company.id}/integration_review"
    assert test_client.get(setting, headers=viewer).json()["required"] is False
    assert (
        test_client.put(setting, json={"required": True}, headers=manager).status_code
        == 403
    )
    assert (
        test_client.put(setting, json={"required": True}, headers=owner).json()[
            "required"
        ]
        is True
    )
    assert (
        len(
            _audits(
                sqlite_session, company.id, "integration_review_requirement_changed"
            )
        )
        == 1
    )
    base = f"/v1/sprints/{sprint.id}/integration"
    before = git(repo, "rev-parse", "main")

    refused = test_client.post(f"{base}/merge", headers=manager)
    assert refused.status_code == 409 and "approved" in refused.json()["detail"]
    approved = test_client.post(
        f"{base}/reviews", json={"event": "approve", "body": "Read it"}, headers=manager
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["sod"] == "unattributed"  # the platform opened this sprint
    test_client.post(
        f"{base}/reviews",
        json={"event": "request_changes", "body": "One more thing"},
        headers=owner,
    )
    assert test_client.post(f"{base}/merge", headers=manager).status_code == 409
    assert git(repo, "rev-parse", "main") == before
    again = test_client.post(
        f"{base}/reviews", json={"event": "approve"}, headers=owner
    ).json()
    assert test_client.get(base, headers=viewer).json()["review"]["approved"] is True

    merged = test_client.post(f"{base}/merge", headers=manager)
    assert merged.status_code == 201, merged.text
    assert merged.json()["details"]["local_merge"]["review_id"] == again["id"]
    assert git(repo, "rev-parse", "main") == row.commit


@pytest.mark.usefixtures("enforcement")
def test_an_integration_that_failed_its_suite_is_not_approved(
    world, test_client, sqlite_session
):
    row = world["row"]
    row.status = "tests_failed"
    sqlite_session.add(row)
    sqlite_session.commit()
    _, manager = world["Manager"]
    refused = test_client.post(
        f"/v1/sprints/{world['sprint'].id}/integration/reviews",
        json={"event": "approve"},
        headers=manager,
    )
    assert refused.status_code == 409 and "suite failed" in refused.json()["detail"]


# -- Segregation of duties ------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_who_started_the_sprint_does_not_approve_its_integration(
    world, test_client, sqlite_session
):
    company, sprint = world["company"], world["sprint"]
    starter, starter_h = world["Manager"]
    _, owner = world["Owner"]
    sprint.started_by_user_id = starter.id
    sqlite_session.add(sprint)
    sqlite_session.commit()
    url = f"/v1/sprints/{sprint.id}/integration/reviews"

    refused = test_client.post(url, json={"event": "approve"}, headers=starter_h)
    assert (
        refused.status_code == 403
        and "Segregation of duties" in refused.json()["detail"]
    )
    sod = _audits(sqlite_session, company.id, "sod_refused")
    assert [r.details["subject"]["kind"] for r in sod] == ["sprint_integration_review"]
    assert not _reviews(sqlite_session, world["row"])
    # The starter may still comment; another person approves.
    assert (
        test_client.post(
            url, json={"event": "comment", "body": "Looks done"}, headers=starter_h
        ).status_code
        == 201
    )
    approved = test_client.post(url, json={"event": "approve"}, headers=owner)
    assert approved.status_code == 201 and approved.json()["sod"] == "separated"


def test_the_gate_reads_only_the_latest_decision_on_the_integrations_commit(
    sqlite_session,
    repo,  # noqa: F811
):
    company = _company(sqlite_session)
    _, row = _integration(sqlite_session, company, repo, "untested")
    company = sqlite_session.get(Company, company.id)
    company.integration_review_required = True
    sqlite_session.add(company)
    sqlite_session.commit()

    def review(event, commit):
        sqlite_session.add(
            IntegrationReview(
                company_id=row.company_id,
                sprint_id=row.sprint_id,
                integration_id=row.id,
                event=event,
                outcome="x",
                commit=commit,
                diff_sha256="0" * 64,
                author_name="A",
            )
        )
        sqlite_session.commit()

    with pytest.raises(integration_review.IntegrationReviewRefused):
        integration_review.require_approval(sqlite_session, row)
    review("approve", "2" * 40)  # of another commit
    assert integration_review.approving_review(sqlite_session, row) is None
    review("approve", row.commit)
    review("comment", row.commit)  # a comment does not change the decision
    assert integration_review.approving_review(sqlite_session, row) is not None
    integration_review.require_approval(sqlite_session, row)
    assert isinstance(row, SprintIntegration)
    assert not sqlite_session.exec(
        select(IntegrationReviewComment).where(
            IntegrationReviewComment.integration_id == row.id
        )
    ).all()
