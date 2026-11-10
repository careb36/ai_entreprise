"""
The Git workflow: each task's deliveries as commits on its own branch.

The daemon writes a task's files on ``fabrique/task-<id>`` in a worktree
under the repository's git directory and commits them, so the person's
checkout, index and branch are never touched (``vscode-daemon/git_workspace.py``).
These tests run real git against a repository in ``tmp_path``.
"""

import asyncio
import importlib.util
import json
import secrets
import subprocess
from pathlib import Path

import auth_tokens
import pytest
import worker_stages
from config import settings
from models import Company, CompanyMember, User
from models.agent_run import AgentRun, Verification
from models.company import ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.loop.worker import deliver as worker_deliver
from services.workspace import git_workflow
from sqlmodel import Session, select

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_git", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Ana", "-c", "user.email=ana@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "first")
    return root


SPEC = {
    "branch": "fabrique/task-7",
    "base": "",
    "message": "Add login\n\nFabrique-Task: 7\nFabrique-Sprint: 3\n",
}


def _deliver(root, files, spec=SPEC):
    return daemon.deliver_files(str(root), files, spec)


# --- the daemon --------------------------------------------------------------


def test_a_delivery_is_a_commit_on_the_task_branch_and_the_checkout_is_untouched(repo):
    main_head = git(repo, "rev-parse", "HEAD")

    reply = _deliver(
        repo, [{"path": "src/login.py", "content": "def login():\n    return True\n"}]
    )

    assert reply["ok"] is True
    info = reply["git"]
    assert (info["branch"], info["parent"]) == ("fabrique/task-7", main_head)
    worktree = Path(info["worktree"])
    assert worktree.parent == (repo / ".git" / "fabrique-worktrees").resolve()
    assert reply["workspace_root"] == str(worktree)
    assert (worktree / "src" / "login.py").read_text().startswith("def login")
    # The person's checkout: no new file, nothing staged, still on main.
    assert not (repo / "src").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    # The commit is the delivery, with the task's trailers.
    assert git(repo, "rev-parse", "fabrique/task-7") == info["commit"]
    assert "Fabrique-Task: 7" in git(
        repo, "log", "-1", "--format=%B", "fabrique/task-7"
    )
    assert (
        git(repo, "log", "-1", "--format=%an <%ae>", "fabrique/task-7")
        == "La Fabrique <fabrique@localhost>"
    )
    [written] = reply["files"]
    assert (written["status"], written["change"]) == ("written", "added")


def test_another_attempt_is_another_commit_and_the_same_bytes_are_none(repo):
    first = _deliver(repo, [{"path": "app.py", "content": "a = 1\n"}])["git"]["commit"]

    second = _deliver(repo, [{"path": "app.py", "content": "a = 2\n"}])

    assert second["git"]["parent"] == first
    assert second["files"][0]["change"] == "modified"
    assert "-a = 1\n+a = 2" in second["files"][0]["diff"]
    again = _deliver(repo, [{"path": "app.py", "content": "a = 2\n"}])
    assert again["git"]["commit"] is None
    assert again["files"][0]["change"] == "unchanged"


def test_a_named_base_starts_the_branch_there(repo):
    git(repo, "checkout", "-q", "-b", "develop")
    (repo / "dev.txt").write_text("dev\n", encoding="utf-8")
    git(repo, "add", "dev.txt")
    git(repo, "commit", "-q", "-m", "develop work")
    develop = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")

    reply = _deliver(
        repo, [{"path": "x.py", "content": "x = 1\n"}], {**SPEC, "base": "develop"}
    )

    assert reply["git"]["parent"] == develop
    assert (Path(reply["git"]["worktree"]) / "dev.txt").exists()


def test_what_git_refuses_is_an_error_the_backend_can_read(repo, tmp_path):
    bad = _deliver(
        repo, [{"path": "x.py", "content": "x\n"}], {**SPEC, "branch": "../../etc"}
    )
    assert bad["ok"] is False
    assert "branch name" in bad["error"]

    missing = _deliver(tmp_path / "gone", [{"path": "x.py", "content": "x\n"}])
    assert missing["ok"] is False
    assert missing["files"][0]["status"] == "workspace_missing"


# --- the backend ---------------------------------------------------------------


def _company(session, *, on=True, base=None):
    company = Company(
        company_name=f"Git {secrets.token_hex(3)}",
        git_workflow=on,
        git_base_branch=base,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def test_the_spec_is_asked_only_with_the_workflow_on(sqlite_session):
    on = _company(sqlite_session, base="develop")
    off = _company(sqlite_session, on=False)
    item = {"id": 41, "title": "  Encrypt   stored credentials "}

    assert (
        git_workflow.delivery_spec(
            sqlite_session.get_bind(), {"id": 3, "company_id": off.id}, item
        )
        is None
    )
    spec = git_workflow.delivery_spec(
        sqlite_session.get_bind(), {"id": 3, "company_id": on.id}, item
    )
    assert spec == {
        "branch": "fabrique/task-41",
        "base": "develop",
        "message": "Encrypt stored credentials\n\nFabrique-Task: 41\nFabrique-Sprint: 3\n",
    }


def test_the_spec_names_the_branches_of_the_done_tasks_it_depends_on(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    done = BacklogItem(sprint_id=sprint.id, title="Store", status="done")
    pending = BacklogItem(sprint_id=sprint.id, title="Cache", status="blocked")
    sqlite_session.add_all([done, pending])
    sqlite_session.commit()
    task = BacklogItem(
        sprint_id=sprint.id, title="Endpoint", depends_on=[done.id, pending.id]
    )
    sqlite_session.add(task)
    sqlite_session.commit()
    spec = git_workflow.delivery_spec(
        sqlite_session.get_bind(),
        {"id": sprint.id, "company_id": company.id},
        {"id": task.id, "title": "Endpoint"},
    )
    assert spec["merge"] == [f"fabrique/task-{done.id}"]
    alone = git_workflow.delivery_spec(
        sqlite_session.get_bind(),
        {"id": sprint.id, "company_id": company.id},
        {"id": done.id, "title": "Store"},
    )
    assert "merge" not in alone


def test_the_daemon_merges_the_prerequisites_branches_into_the_worktree(repo):
    first = _deliver(
        repo,
        [{"path": "store.py", "content": "STORE = 1\n"}],
        {**SPEC, "branch": "fabrique/task-1"},
    )
    assert first["ok"]
    second = _deliver(
        repo,
        [{"path": "endpoint.py", "content": "from store import STORE\n"}],
        {
            **SPEC,
            "branch": "fabrique/task-2",
            "merge": ["fabrique/task-1", "fabrique/task-9", "main"],
        },
    )
    assert second["ok"]
    # The done task's branch is in; a missing one and a branch that is not
    # La Fabrique's are skipped.
    assert second["git"]["merged"] == ["fabrique/task-1"]
    worktree = Path(second["git"]["worktree"])
    assert (worktree / "store.py").read_text() == "STORE = 1\n"
    assert git(worktree, "log", "--oneline").count("\n") == 3  # first, merge, endpoint
    # Already in: nothing merged again.
    again = _deliver(
        repo,
        [{"path": "endpoint.py", "content": "from store import STORE, STORE\n"}],
        {**SPEC, "branch": "fabrique/task-2", "merge": ["fabrique/task-1"]},
    )
    assert again["git"]["merged"] == []
    # A conflict is an error the backend can read, and the worktree is left clean.
    _deliver(
        repo,
        [{"path": "store.py", "content": "STORE = 2\n"}],
        {**SPEC, "branch": "fabrique/task-3"},
    )
    conflict = _deliver(
        repo,
        [{"path": "x.py", "content": "x\n"}],
        {**SPEC, "branch": "fabrique/task-3", "merge": ["fabrique/task-1"]},
    )
    assert (
        conflict["ok"] is False
        and "could not merge fabrique/task-1" in conflict["error"]
    )
    third_worktree = Path(repo) / ".git" / "fabrique-worktrees" / "fabrique__task-3"
    assert git(third_worktree, "status", "--porcelain") == ""


def test_a_daemon_that_could_not_write_leaves_the_delivery_unverifiable_with_its_reason():
    evidence = {"workspace_writes": []}
    git_workflow.record_reply(
        evidence,
        {
            "ok": False,
            "files": [],
            "error": "fatal: 'fabrique/task-7' is already checked out",
        },
    )
    assert evidence["workspace_writes"] is None
    assert "already checked out" in evidence["reason"]


class _DaemonOverNATS:
    """``request`` runs the real daemon, git spec included."""

    jetstream_enabled = False

    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if subject != "workspace.generate":
            raise TimeoutError("not served here")
        return daemon.deliver_files(
            payload["workspace_root"], payload["files"], payload.get("git")
        )

    async def publish(self, subject, payload, msg_id=None):
        return None


ANSWER = """Login handler.

```python path=src/auth/login.py
def login(user, password):
    return user == "admin"
```
"""


def test_a_task_delivered_with_the_workflow_lands_on_its_branch_and_is_verified(
    sqlite_session, repo, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    engine = sqlite_session.get_bind()
    worker_stages.use_engine(monkeypatch, engine)
    company = _company(sqlite_session)
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(repo)))
    sprint = Sprint(company_id=company.id, sprint_name="Auth", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Implement login",
        status="in_progress",
        assignee="Developer",
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    nats = _DaemonOverNATS()

    asyncio.run(
        worker_deliver._deliver_artifacts(
            nats,
            {"id": item.id, "title": item.title, "description": ""},
            {"role": "Developer", "name": "Dev", "model": "m", "served_model": "m"},
            {"id": sprint.id, "company_id": company.id, "name": sprint.sprint_name},
            ANSWER,
        )
    )

    _, payload = nats.requests[0]
    assert payload["git"]["branch"] == f"fabrique/task-{item.id}"
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
        ).one()
        verification = session.exec(
            select(Verification).where(Verification.agent_run_id == run.id)
        ).one()
    assert verification.verdict == "verified"
    info = verification.evidence["git"]
    assert git(repo, "rev-parse", f"fabrique/task-{item.id}") == info["commit"]
    assert verification.evidence["workspace_root"] == info["worktree"]
    assert not (repo / "src").exists()


@pytest.fixture
def enforcement(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
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


def test_a_manager_turns_the_workflow_on_and_a_viewer_reads_it(
    sqlite_session, test_client, enforcement
):
    company = _company(sqlite_session, on=False)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/git"

    assert test_client.get(url, headers=viewer).json() == {
        "company_id": company.id,
        "git_workflow": False,
        "git_base_branch": None,
    }
    assert (
        test_client.put(url, json={"git_workflow": True}, headers=viewer).status_code
        == 403
    )
    saved = test_client.put(
        url,
        json={"git_workflow": True, "git_base_branch": " develop "},
        headers=manager,
    )
    assert saved.json() == {
        "company_id": company.id,
        "git_workflow": True,
        "git_base_branch": "develop",
    }
    assert (
        test_client.put(
            url,
            json={"git_workflow": True, "git_base_branch": "main..evil"},
            headers=manager,
        ).status_code
        == 422
    )
    assert (
        test_client.put(
            url,
            json={"git_workflow": True, "git_base_branch": "--upload-pack=x"},
            headers=manager,
        ).status_code
        == 422
    )
    assert (
        test_client.get(url, headers=_member(sqlite_session, None, "Owner")).status_code
        == 403
    )


def test_the_changes_name_the_branch_and_commit(sqlite_session, test_client, repo):
    company = _company(sqlite_session)
    reply = _deliver(repo, [{"path": "a.py", "content": "a\n"}])
    run = AgentRun(
        company_id=company.id,
        external_ref="task-9",
        producer="le-fabrique-task-worker",
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    sqlite_session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={"workspace_writes": reply["files"], "git": reply["git"]},
        )
    )
    sqlite_session.commit()

    body = test_client.get(
        f"/v1/agent_runs/{run.id}/changes", params={"company_id": company.id}
    ).json()

    assert body["git"]["branch"] == "fabrique/task-7"
    assert body["git"]["commit"] == reply["git"]["commit"]
    assert json.dumps(body)  # serializable as returned
