"""Accepting La Fabrique's work into a local repository, with no remote.

A company may keep its code only in a local repository: a task's or a
sprint's branch is merged into a base branch on the daemon's machine
(``vscode-daemon/local_merge.py``, ``services/local_merge.py``), never pushed.
These tests use repositories in ``tmp_path`` that have no remote at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import local_merge
from models.integration import SprintIntegration
from models.review import RunPublication
from models.sprint import Sprint
from test_git_publish import _Daemon, _delivered_run
from test_git_workflow import SPEC, _company, _member, daemon, enforcement, git  # noqa: F401

MERGE = {"branch": "fabrique/task-7", "base": ""}


@pytest.fixture
def repo(tmp_path):
    """A repository on disk with no remote, on main."""
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "first")
    assert git(root, "remote") == ""
    return root


def _deliver(root, content="a = 1\n", path="a.py"):
    return daemon.deliver_files(str(root), [{"path": path, "content": content}], SPEC)[
        "git"
    ]["commit"]


def _worktrees(root):
    return [
        line for line in git(root, "worktree", "list").splitlines() if "merge-" in line
    ]


def test_a_task_branch_is_fast_forwarded_into_the_checked_out_base(repo):
    before = git(repo, "rev-parse", "main")
    commit = _deliver(repo)
    assert not (repo / "a.py").exists()  # delivered in a worktree, not here

    reply = local_merge.merge_local(str(repo), {**MERGE, "commit": commit})

    assert reply == {
        "ok": True,
        "branch": "fabrique/task-7",
        "base": "main",
        "commit": commit,
        "before": before,
        "after": commit,
        "already": False,
        "fast_forward": True,
    }
    assert git(repo, "rev-parse", "main") == commit
    assert (repo / "a.py").read_text() == "a = 1\n"  # the person's files follow
    assert git(repo, "status", "--porcelain") == ""
    # Nothing to push to, and nothing pushed.
    assert git(repo, "remote") == ""


def test_a_base_that_moved_gets_a_merge_commit(repo):
    commit = _deliver(repo)
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "meanwhile")
    before = git(repo, "rev-parse", "main")

    reply = local_merge.merge_local(str(repo), {**MERGE, "commit": commit})

    assert reply["fast_forward"] is False and reply["before"] == before
    parents = git(repo, "rev-list", "--parents", "-n", "1", "main").split()[1:]
    assert parents == [before, commit]
    assert git(repo, "log", "-1", "--format=%an", "main") == "La Fabrique"
    assert (repo / "a.py").exists() and (repo / "b.txt").exists()
    assert _worktrees(repo) == []


def test_a_conflict_changes_nothing_and_names_the_file(repo):
    commit = _deliver(repo)
    (repo / "a.py").write_text("a = 2\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "theirs")
    before = git(repo, "rev-parse", "main")

    with pytest.raises(git_workspace.GitError, match=r"conflicts in a\.py"):
        local_merge.merge_local(str(repo), {**MERGE, "commit": commit})

    assert git(repo, "rev-parse", "main") == before
    assert (repo / "a.py").read_text() == "a = 2\n"
    assert git(repo, "status", "--porcelain") == ""
    assert _worktrees(repo) == []


def test_uncommitted_work_on_the_base_is_never_overwritten(repo):
    commit = _deliver(repo)
    (repo / "README.md").write_text("# Work in progress\n")
    before = git(repo, "rev-parse", "main")

    with pytest.raises(git_workspace.GitError, match="uncommitted changes"):
        local_merge.merge_local(str(repo), {**MERGE, "commit": commit})

    assert git(repo, "rev-parse", "main") == before
    assert (repo / "README.md").read_text() == "# Work in progress\n"


def test_a_base_nobody_has_checked_out_moves_without_touching_the_checkout(repo):
    git(repo, "checkout", "-q", "-b", "dev")
    (repo / "notes.txt").write_text("mine\n")  # untracked work on another branch
    commit = _deliver(repo)

    reply = local_merge.merge_local(
        str(repo), {**MERGE, "base": "main", "commit": commit}
    )

    assert reply["base"] == "main" and git(repo, "rev-parse", "main") == commit
    assert git(repo, "branch", "--show-current") == "dev"
    assert not (repo / "a.py").exists() and (repo / "notes.txt").exists()


def test_merging_again_says_it_is_already_in(repo):
    commit = _deliver(repo)
    local_merge.merge_local(str(repo), {**MERGE, "commit": commit})
    again = local_merge.merge_local(str(repo), {**MERGE, "commit": commit})
    assert again["already"] is True and again["after"] == commit


def test_what_cannot_be_merged_is_refused(repo):
    first = _deliver(repo)
    _deliver(repo, content="a = 3\n")  # the branch moved on past the run's commit
    with pytest.raises(git_workspace.GitError, match="moved on"):
        local_merge.merge_local(str(repo), {**MERGE, "commit": first})
    with pytest.raises(
        git_workspace.GitError, match="not a branch name La Fabrique writes"
    ):
        local_merge.merge_local(str(repo), {"branch": "main", "base": "dev"})
    with pytest.raises(git_workspace.GitError, match="not a branch to merge into"):
        local_merge.merge_local(str(repo), {**MERGE, "base": "fabrique/task-8"})
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(git_workspace.GitError, match="not a branch to merge into"):
        local_merge.merge_local(str(repo), MERGE)  # nothing checked out to merge into


# --- the backend -------------------------------------------------------------


class _MergingDaemon(_Daemon):
    """``request`` runs the real local merge against the repository on disk."""

    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        try:
            return local_merge.merge_local(payload["workspace_root"], payload)
        except git_workspace.GitError as e:
            return {"error": {"status": 409, "message": str(e)}}


@pytest.mark.usefixtures("enforcement")
def test_a_manager_merges_a_run_locally_and_nothing_is_pushed(
    sqlite_session, test_client, repo, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session)
    run, info = _delivered_run(sqlite_session, company, repo)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    nats = _MergingDaemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    url = f"/v1/agent_runs/{run.id}/merge?company_id={company.id}"

    assert test_client.post(url, headers=viewer).status_code == 403
    merged = test_client.post(url, headers=manager)

    assert merged.status_code == 201, merged.text
    body = merged.json()
    assert (body["how"], body["base"], body["commit"], body["url"]) == (
        "local",
        "main",
        info["commit"],
        None,
    )
    assert body["audit_log_id"] is not None
    assert git(repo, "rev-parse", "main") == info["commit"]
    subject, spec = nats.requests[0]
    assert subject == "workspace.git.merge"
    assert spec["commit"] == info["commit"]
    assert spec["message"].endswith(f"Fabrique-Run: {run.id}\n")
    listed = test_client.get(
        f"/v1/agent_runs/{run.id}/publications",
        params={"company_id": company.id},
        headers=viewer,
    ).json()["publications"]
    assert [p["how"] for p in listed] == ["local"]
    # A local merge opened no request: there are no comments to bring back.
    sync = test_client.post(
        f"/v1/agent_runs/{run.id}/publications/{body['id']}/sync",
        params={"company_id": company.id},
        headers=manager,
    )
    assert sync.status_code == 409


def test_a_conflict_or_a_run_without_a_branch_records_nothing(
    sqlite_session, test_client, repo, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session)
    plain, _ = _delivered_run(sqlite_session, company, repo, git=False)
    run, _ = _delivered_run(sqlite_session, company, repo, ref="task-8")
    (repo / "a.py").write_text("a = 2\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "theirs")
    monkeypatch.setattr(test_client.app.state, "nats_client", _MergingDaemon())

    refused = test_client.post(
        f"/v1/agent_runs/{plain.id}/merge", params={"company_id": company.id}
    )
    assert refused.status_code == 409 and "Git branch" in refused.json()["detail"]
    conflict = test_client.post(
        f"/v1/agent_runs/{run.id}/merge", params={"company_id": company.id}
    )
    assert conflict.status_code == 409 and "a.py" in conflict.json()["detail"]
    assert (
        sqlite_session.query(RunPublication).filter_by(agent_run_id=run.id).count() == 0
    )


def _integration(session, company, repo, status):
    sprint = Sprint(company_id=company.id, sprint_name="Local", status="completed")
    session.add(sprint)
    session.commit()
    git(repo, "branch", f"fabrique/sprint-{sprint.id}", "main")
    git(repo, "checkout", "-q", f"fabrique/sprint-{sprint.id}")
    (repo / "s.py").write_text("s = 1\n")
    git(repo, "add", "s.py")
    git(repo, "commit", "-q", "-m", "integrated")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="human:Ana",
        status=status,
        branch=f"fabrique/sprint-{sprint.id}",
        base="main",
        repository=str(repo),
        commit=commit,
    )
    session.add(row)
    session.commit()
    return sprint, row


@pytest.mark.usefixtures("enforcement")
def test_a_manager_merges_a_sprints_integration_locally(
    sqlite_session, test_client, repo, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    monkeypatch.setattr(test_client.app.state, "nats_client", _MergingDaemon())
    sprint, row = _integration(sqlite_session, company, repo, "untested")
    url = f"/v1/sprints/{sprint.id}/integration/merge"

    assert test_client.post(url, headers=viewer).status_code == 403
    merged = test_client.post(url, headers=manager)

    assert merged.status_code == 201, merged.text
    record = merged.json()["details"]["local_merge"]
    assert (record["base"], record["after"]) == ("main", row.commit)
    assert record["merged_by"]
    assert record["audit_log_id"] is not None
    assert git(repo, "rev-parse", "main") == row.commit and (repo / "s.py").exists()


@pytest.mark.parametrize(
    "status, reason", [("conflict", "conflict"), ("tests_failed", "suite failed")]
)
@pytest.mark.usefixtures("enforcement")
def test_an_integration_that_did_not_pass_is_not_merged(
    sqlite_session, test_client, repo, monkeypatch, tmp_path, status, reason
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    monkeypatch.setattr(test_client.app.state, "nats_client", _MergingDaemon())
    sprint, _ = _integration(sqlite_session, company, repo, status)
    before = git(repo, "rev-parse", "main")
    refused = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/merge", headers=manager
    )
    assert refused.status_code == 409 and reason in refused.json()["detail"]
    assert git(repo, "rev-parse", "main") == before


def test_the_daemon_answers_merge_requests_over_nats():
    source = (
        Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
    ).read_text()
    assert '("workspace.git.merge", local_merge.handle_merge_local)' in source
