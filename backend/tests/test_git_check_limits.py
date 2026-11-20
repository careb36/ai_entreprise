"""
The New company form's folder check, and the stack read after the sync (issue #745).

``POST /v1/workspace/git_check`` runs before its caller belongs to the company
being created, so it is held to rules of its own (``services/release/
folder_check.py``): on a deployment with several accounts only a company
member or the first account may call it, each account is rate limited, each
"Check remote" is audited with the account and the folder (never a URL),
and the answer carries only the counts, branches and flags the form shows.
When "Bring my local copy up to date" moved the base, ``init_company`` reads
the stack again from the folder as fast-forwarded, so the stack recorded is
the one the agents will read. Temporary repositories only.
"""

# ruff: noqa: F811 - the tests take the imported ``behind`` fixture by name.

import json
import secrets
import sys
from pathlib import Path

import pytest
from config import settings
from models import AuditLog, ProjectDiscovery
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_base
import git_sync
import host_folders
import test_git_workflow
from test_git_sync import _commit, behind  # noqa: F401 - the fixture
from test_git_workflow import _company, _member, git

enforcement = test_git_workflow.enforcement
URL = "/v1/workspace/git_check"


class _Daemon:
    """The daemon's real inspection (with its snapshot), base status and sync."""

    def __init__(self):
        self.subjects = []

    async def request(self, subject, payload, timeout=5.0):
        self.subjects.append(subject)
        if subject == "workspace.inspect":
            return host_folders.inspect_project(
                payload["path"], with_snapshot=bool(payload.get("snapshot"))
            )
        if subject == "workspace.git.sync_base":
            return git_sync.sync(
                payload["workspace_root"], payload["base"], payload["forge_policy"]
            )
        if subject == "workspace.git.base_status":
            return git_base.status(
                payload["workspace_root"], payload["base"], payload["forge_policy"]
            )
        raise TimeoutError(subject)

    async def close(self):
        pass


@pytest.fixture
def daemon(test_client):
    test_client.app.state.nats_client = nats = _Daemon()
    return nats


@pytest.mark.usefixtures("enforcement")
def test_with_several_accounts_only_a_member_or_the_first_account_may_check(
    test_client, sqlite_session, behind, daemon
):
    clone, _, _ = behind
    body = {"project_path": str(clone)}
    first = _member(sqlite_session, None, "Owner")
    alone = test_client.post(URL, json=body, headers=first)
    assert alone.status_code == 200, alone.text  # one account: its own

    stranger = _member(sqlite_session, None, "Viewer")
    company = _company(sqlite_session)
    member = _member(sqlite_session, company.id, "Viewer")
    refused = test_client.post(URL, json=body, headers=stranger)
    assert refused.status_code == 403 and "first account" in refused.text
    assert test_client.post(URL, json=body, headers=member).status_code == 200
    assert test_client.post(URL, json=body, headers=first).status_code == 200
    assert test_client.post(URL, json=body).status_code == 401


@pytest.mark.usefixtures("enforcement")
def test_each_account_is_rate_limited(
    test_client, sqlite_session, behind, daemon, monkeypatch
):
    clone, _, _ = behind
    monkeypatch.setattr(settings, "GIT_CHECK_RATE_LIMIT", "2/minute")
    company = _company(sqlite_session)
    one = _member(sqlite_session, company.id, "Owner")
    other = _member(sqlite_session, company.id, "Viewer")
    body = {"project_path": str(clone)}

    codes = [
        test_client.post(URL, json=body, headers=one).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    # Another account behind the same address keeps its own allowance.
    assert test_client.post(URL, json=body, headers=other).status_code == 200


def test_the_answer_holds_only_what_the_form_shows_and_each_check_is_audited(
    test_client, sqlite_session, behind, daemon
):
    clone, _, _ = behind
    password = secrets.token_hex(6)
    remote = git(clone, "remote", "get-url", "origin")

    local = test_client.post(URL, json={"project_path": str(clone)}).json()
    checked = test_client.post(
        URL, json={"project_path": str(clone), "contact_remote": True}
    ).json()

    assert checked["base_behind_remote"] == 5 and checked["base_ahead_of_remote"] == 0
    assert checked["clean"] is True and checked["current_branch"] == "main"
    for answer in (local, checked):
        assert not {"base_commit", "base_remote_commit", "repository", "path"} & set(
            answer
        )
    rows = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "folder_remote_checked")
    ).all()
    assert len(rows) == 1  # the local read is not a remote check
    assert rows[0].details["folder"] == str(clone)
    assert "remote_url" not in rows[0].result
    assert rows[0].result["base_behind_remote"] == 5

    # An unreachable remote is answered with its code, never git's words.
    git(
        clone,
        "remote",
        "set-url",
        "origin",
        f"https://dev:{password}@{clone.parent}/gone",
    )
    gone = test_client.post(
        URL, json={"project_path": str(clone), "contact_remote": True}
    ).json()
    assert gone["remote_unreachable"] == "remote_unreachable"
    assert password not in json.dumps(gone)
    last = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "folder_remote_checked")
    ).all()[-1]
    assert password not in json.dumps(last.details) + json.dumps(last.result)
    git(clone, "remote", "set-url", "origin", remote)


def test_the_stack_is_read_again_from_the_folder_the_sync_brought_up_to_date(
    test_client, sqlite_session, behind, daemon
):
    clone, upstream, _ = behind
    for name in ("app.js", "routes.js", "views.js"):
        _commit(upstream, name, "export default 1\n")
    git(upstream, "push", "-q", "origin", "main")
    # The wizard's Scan ran before the sync: one Python file.
    scanned = {
        "name": "app",
        "language": "Python",
        "file_count": 1,
        "team_source": "rules",
    }

    created = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Rescan Corp",
            "project_path": str(clone),
            "start_now": False,
            "detected_stack": scanned,
            "forge_policy": "fetch",
            "sync_base": True,
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["base_sync"]["outcome"] == "updated"
    discovery = sqlite_session.exec(
        select(ProjectDiscovery).where(
            ProjectDiscovery.company_id == body["company_id"]
        )
    ).first()
    stack = json.loads(discovery.detected_stack)
    assert stack["language"] == "JavaScript" and stack["read_after_sync"] is True
    assert stack["team_source"] == "rules"  # the team the Owner chose is kept
    assert body["detected_stack"] == stack


def test_without_a_move_the_scan_stands(test_client, sqlite_session, behind, daemon):
    clone, _, _ = behind
    scanned = {"name": "app", "language": "Python", "file_count": 1}
    created = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Local Corp",
            "project_path": str(clone),
            "start_now": False,
            "detected_stack": scanned,
            "forge_policy": "none",
            "sync_base": True,
        },
    ).json()
    assert created["base_sync"]["outcome"] == "forge_disabled"
    assert "detected_stack" not in created
    discovery = sqlite_session.exec(
        select(ProjectDiscovery).where(
            ProjectDiscovery.company_id == created["company_id"]
        )
    ).first()
    assert json.loads(discovery.detected_stack) == scanned
