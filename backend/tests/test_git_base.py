"""
Where La Fabrique's branches start, and a folder that is not a repository.

A task's branch and a sprint's integration branch start from the company's
base as its remote has it (``vscode-daemon/git_base.py``): a local base
behind its upstream by a fast-forward starts from the remote tip without
moving the person's branch, a diverged one is refused, a repository with no
remote and an offline remote keep the local base, and the publication of an
integration behind ``origin``'s base is refused. A folder that is not a
repository is delivered without the Git workflow, and the company's
workflow follows what the daemon observed in its folder
(``services/release/git_setup.py``). Real git in ``tmp_path``: a bare remote, a
clone whose local base is behind it, a plain folder and an unreachable URL.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from models import BacklogItem, Company, ProjectDiscovery, Sprint
from models.agent_run import AgentRun, Verification
from models.integration import SprintIntegration
from services.loop.worker import deliver as worker_deliver
from services.release import git_setup, integration_publish
from services.workspace import git_workflow
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_base
import git_workspace
import sprint_integration
import test_git_workflow
import worker_stages
from test_git_workflow import (
    ANSWER,
    SPEC,
    _company,
    _DaemonOverNATS,
    _member,
    daemon,
    git,
)

# The fixture that requires authentication, as the Git workflow's tests use it.
enforcement = test_git_workflow.enforcement


def _commit(root, name, text):
    (root / name).write_text(text, encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", f"{name}: {text.strip()}")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def behind(tmp_path):
    """``(clone, upstream)``: a bare remote, a clone of it, and three commits pushed since."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", str(remote), str(upstream))
    git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _commit(upstream, "core.py", "VERSION = 1\n")
    git(upstream, "push", "-q", "origin", "main")
    clone = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    for version in (2, 3, 4):
        _commit(upstream, "core.py", f"VERSION = {version}\n")
    git(upstream, "push", "-q", "origin", "main")
    return clone, upstream


def _files(name="feature.py"):
    return [{"path": name, "content": "FEATURE = True\n"}]


# --- the daemon: where a task's branch starts ----------------------------------


def test_a_base_behind_its_remote_starts_the_task_branch_at_the_remote_tip(behind):
    clone, upstream = behind
    local_main = git(clone, "rev-parse", "main")
    remote_tip = git(upstream, "rev-parse", "main")

    reply = daemon.deliver_files(str(clone), _files(), SPEC)

    assert reply["ok"], reply
    info = reply["git"]
    assert info["parent"] == remote_tip
    assert info["base_commit"] == remote_tip
    assert info["base_behind_remote"] == 3
    assert info["base_local_commit"] == local_main
    assert info["started_from"] == "origin/main"
    worktree = Path(info["worktree"])
    assert (worktree / "core.py").read_text() == "VERSION = 4\n"
    # The person's branch and files have not moved.
    assert git(clone, "rev-parse", "main") == local_main
    assert (clone / "core.py").read_text() == "VERSION = 1\n"
    assert git(clone, "status", "--porcelain") == ""

    again = daemon.deliver_files(str(clone), _files("other.py"), SPEC)
    assert "base_commit" not in again["git"]  # the branch started at the first delivery


def test_a_base_up_to_date_or_only_ahead_starts_where_it_is(behind):
    clone, _ = behind
    git(clone, "pull", "-q", "--ff-only")
    mine = _commit(clone, "mine.py", "MINE = 1\n")  # unpushed work of the person's

    info = daemon.deliver_files(str(clone), _files(), SPEC)["git"]

    assert info["parent"] == mine
    assert info["base_commit"] == mine
    assert info["base_ahead_of_remote"] == 1
    assert "base_behind_remote" not in info


def test_a_diverged_base_is_refused_naming_both_sides(behind):
    clone, _ = behind
    _commit(clone, "mine.py", "MINE = 1\n")

    reply = daemon.deliver_files(str(clone), _files(), SPEC)

    assert reply["ok"] is False
    assert "diverged" in reply["error"]
    assert "1 commit(s) only here, 3 only there" in reply["error"]
    assert not git_workspace.has_branch(clone, SPEC["branch"])


def test_a_repository_without_a_remote_keeps_working_as_before(tmp_path):
    root = tmp_path / "local-only"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    head = _commit(root, "README.md", "# App\n")

    info = daemon.deliver_files(str(root), _files(), SPEC)["git"]

    assert info["parent"] == head
    assert info["base_commit"] == head
    assert info["base_remote"] is None
    assert "remote_unreachable" not in info


def test_a_base_the_remote_does_not_have_is_local_not_unreachable(behind):
    clone, _ = behind
    git(clone, "checkout", "-q", "-b", "spike")
    spike = _commit(clone, "spike.py", "SPIKE = 1\n")

    info = daemon.deliver_files(str(clone), _files(), {**SPEC, "base": "spike"})["git"]

    assert info["parent"] == spike
    assert info["base_not_on_remote"] == "origin/spike"
    assert info["base_remote"] is None
    assert "remote_unreachable" not in info


def test_an_unreachable_remote_proceeds_from_the_local_base(behind, tmp_path):
    clone, _ = behind
    local_main = git(clone, "rev-parse", "main")
    git(clone, "remote", "set-url", "origin", (tmp_path / "nowhere.git").as_uri())

    reply = daemon.deliver_files(str(clone), _files(), SPEC)

    assert reply["ok"], reply
    assert reply["git"]["parent"] == local_main
    assert reply["git"]["remote_unreachable"]
    assert "base_behind_remote" not in reply["git"]


def test_a_stalled_fetch_falls_back_to_the_local_base_within_the_budget(
    behind, monkeypatch
):
    clone, _ = behind
    local_main = git(clone, "rev-parse", "main")
    # A remote that never answers: git's ext:: transport running `sleep`.
    git(clone, "config", "protocol.ext.allow", "always")
    git(clone, "remote", "set-url", "origin", "ext::sleep 60")
    monkeypatch.setattr(git_base, "FETCH_TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    reply = daemon.deliver_files(str(clone), _files(), SPEC)
    elapsed = time.monotonic() - started

    assert reply["ok"], reply
    assert reply["git"]["parent"] == local_main
    assert "took more than 1 s" in reply["git"]["remote_unreachable"]
    assert elapsed < 10, elapsed


def test_the_fetch_bound_stays_well_inside_the_backend_delivery_wait():
    budget = git_workflow.delivery_timeout(SPEC)
    assert git_base.FETCH_TIMEOUT_SECONDS < git_workflow.FETCH_ALLOWANCE_SECONDS
    # The fetch, then the worktree, commit and reply, all before the backend gives up.
    assert git_base.FETCH_TIMEOUT_SECONDS * 3 <= budget
    assert git_workflow.delivery_timeout(None) == (
        worker_deliver.settings.WORKSPACE_WRITE_TIMEOUT_SECONDS
    )


def test_the_base_status_observes_without_starting_anything(behind, tmp_path):
    clone, upstream = behind
    status = git_base.status(str(clone), "")
    assert status["is_repo"] is True
    assert (status["base_ref"], status["base_remote"]) == ("main", "origin/main")
    assert status["base_behind_remote"] == 3
    assert status["base_remote_commit"] == git(upstream, "rev-parse", "main")
    assert git(clone, "branch", "--list", "fabrique/*") == ""

    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_base.status(str(plain), "") == {"is_repo": False}
    with pytest.raises(git_workspace.GitError):
        git_base.status(str(clone), "main..evil")


# --- the daemon: a folder that is not a repository ------------------------------


def test_a_folder_that_is_not_a_repository_is_delivered_without_the_workflow(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    reply = daemon.deliver_files(str(plain), _files(), SPEC)

    assert reply["ok"] is True
    assert reply["git_workflow_skipped"] == "not a repository"
    assert "git" not in reply
    assert reply["snapshot"]["files"][0]["path"] == "feature.py"
    assert (plain / "feature.py").read_text() == "FEATURE = True\n"
    # Without a git spec nothing is said about the workflow.
    assert "git_workflow_skipped" not in daemon.deliver_files(
        str(plain), _files("b.py")
    )


# --- the daemon: a sprint's integration ----------------------------------------


def test_an_integration_starts_from_the_remote_tip_and_an_older_one_gets_it_merged(
    behind,
):
    clone, upstream = behind
    git(clone, "remote", "set-url", "origin", str(clone.parent / "unused.git"))
    # Integrated once while offline: from the stale local base.
    daemon.deliver_files(str(clone), _files(), SPEC)
    stale = sprint_integration.integrate(
        str(clone), {"branch": "fabrique/sprint-3", "merge": [SPEC["branch"]]}
    )
    assert stale["base_status"]["remote_unreachable"]
    assert (Path(stale["worktree"]) / "core.py").read_text() == "VERSION = 1\n"

    git(clone, "remote", "set-url", "origin", str(clone.parent / "remote.git"))
    again = sprint_integration.integrate(
        str(clone), {"branch": "fabrique/sprint-3", "merge": [SPEC["branch"]]}
    )

    assert again["ok"], again
    assert again["base_status"]["base_behind_remote"] == 3
    assert again["base_merge"]["branch"] == "origin/main"
    assert again["base_merge"]["source"] == git(upstream, "rev-parse", "main")
    assert (Path(again["worktree"]) / "core.py").read_text() == "VERSION = 4\n"
    assert again["already_merged"] == [SPEC["branch"]]
    assert git(clone, "rev-parse", "main") == stale["start"]  # the person's main

    fresh = sprint_integration.integrate(
        str(clone), {"branch": "fabrique/sprint-4", "merge": [SPEC["branch"]]}
    )
    assert fresh["start"] == git(upstream, "rev-parse", "main")
    assert fresh["base_merge"] is None


def test_the_publication_of_a_branch_behind_origin_is_refused_with_the_count(behind):
    clone, upstream = behind
    git(
        clone, "remote", "set-url", "--push", "origin", str(clone.parent / "remote.git")
    )
    git(clone, "remote", "set-url", "origin", str(clone.parent / "unused.git"))
    daemon.deliver_files(str(clone), _files(), SPEC)
    stale = sprint_integration.integrate(
        str(clone), {"branch": "fabrique/sprint-3", "merge": [SPEC["branch"]]}
    )
    git(clone, "remote", "set-url", "origin", str(clone.parent / "remote.git"))

    with pytest.raises(
        git_workspace.GitError, match="3 commit\\(s\\) behind origin/main"
    ):
        git_workspace.publish(
            str(clone),
            {
                "branch": "fabrique/sprint-3",
                "commit": stale["commit"],
                "base": "main",
                "require_current_base": True,
            },
        )

    current = sprint_integration.integrate(
        str(clone), {"branch": "fabrique/sprint-3", "merge": [SPEC["branch"]]}
    )
    published = git_workspace.publish(
        str(clone),
        {
            "branch": "fabrique/sprint-3",
            "commit": current["commit"],
            "base": "main",
            "require_current_base": True,
        },
    )
    assert published["base_behind_remote"] == 0
    assert git(upstream, "ls-remote", "origin", "fabrique/sprint-3").startswith(
        current["commit"]
    )


# --- the backend -----------------------------------------------------------------


def test_the_task_worker_records_the_skipped_workflow_and_says_so_on_the_board(
    sqlite_session, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    engine = sqlite_session.get_bind()
    worker_stages.use_engine(monkeypatch, engine)
    plain = tmp_path / "plain"
    plain.mkdir()
    company = _company(sqlite_session)  # the workflow on, the folder not a repository
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(plain)))
    sprint = Sprint(company_id=company.id, sprint_name="Auth", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(
        sprint_id=sprint.id, title="Login", status="in_progress", assignee="Developer"
    )
    sqlite_session.add(item)
    sqlite_session.commit()

    class _Timed(_DaemonOverNATS):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        async def request(self, subject, payload, timeout=5.0):
            self.timeouts.append(timeout)
            return await super().request(subject, payload, timeout)

    nats = _Timed()
    asyncio.run(
        worker_deliver._deliver_artifacts(
            nats,
            {"id": item.id, "title": item.title, "description": ""},
            {"role": "Developer", "name": "Dev", "model": "m", "served_model": "m"},
            {"id": sprint.id, "company_id": company.id, "name": sprint.sprint_name},
            ANSWER,
        )
    )

    with Session(engine) as session:
        run = session.exec(
            select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
        ).one()
        verification = session.exec(
            select(Verification).where(Verification.agent_run_id == run.id)
        ).one()
        comments = session.get(BacklogItem, item.id).comments
    assert verification.verdict == "verified"
    assert verification.evidence["git_workflow_skipped"] == "not a repository"
    assert verification.evidence["snapshot"]
    assert (plain / "src" / "auth" / "login.py").exists()
    note = comments[-1]
    assert note["author"] == "System"
    assert "delivered without the Git workflow (not a repository)" in note["text"]
    assert "with a snapshot to undo them" in note["text"]
    # A git spec may fetch the base first: the request waits the fetch allowance too.
    assert nats.timeouts == [
        worker_deliver.settings.WORKSPACE_WRITE_TIMEOUT_SECONDS
        + git_workflow.FETCH_ALLOWANCE_SECONDS
    ]


def test_the_workflow_follows_what_was_observed_in_the_folder(sqlite_session, tmp_path):
    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    inspected = {"is_dir": True, "git": {"is_repo": True, "branch": "main"}}

    assert git_setup.is_repository(inspected, None) is True
    assert (
        git_setup.is_repository({"is_dir": True, "git": {"is_repo": False}}, None)
        is False
    )
    assert git_setup.is_repository(None, str(repo_dir)) is True
    assert git_setup.is_repository(None, str(plain)) is False
    assert git_setup.is_repository(None, str(tmp_path / "elsewhere")) is None
    assert git_setup.workflow_for(True, None) is True
    assert git_setup.workflow_for(True, False) is False
    assert (
        git_setup.workflow_for(False, True) is False
    )  # a claim does not make a repository
    assert git_setup.workflow_for(None, True) is False  # unobserved stays off

    company = _company(sqlite_session)
    git_setup.record(
        sqlite_session,
        company.id,
        {"is_dir": True, "git": {"is_repo": False}},
        str(plain),
    )
    sqlite_session.refresh(company)
    assert company.git_workflow is False
    git_setup.record(sqlite_session, company.id, inspected, str(repo_dir))
    sqlite_session.refresh(company)
    assert company.git_workflow is True


class _InspectingNATS:
    """The daemon's inspection and base status, for the routes."""

    def __init__(self, is_repo, base_status=None):
        self.is_repo = is_repo
        self.base_status = base_status or {}
        self.subjects = []

    async def request(self, subject, payload, timeout=5.0):
        self.subjects.append((subject, payload))
        if subject == "workspace.inspect":
            return {
                "is_dir": True,
                "path": payload["path"],
                "git": {"is_repo": self.is_repo},
            }
        if subject == "workspace.git.base_status":
            return {"is_repo": self.is_repo, **self.base_status}
        raise TimeoutError(subject)

    async def close(self):
        pass


def _created(test_client, sqlite_session, nats, **body):
    test_client.app.state.nats_client = nats
    response = test_client.post(
        "/v1/init_company",
        json={"company_name": "Git Corp", "project_path": "/srv/app", **body},
    )
    assert response.status_code == 200, response.text
    return sqlite_session.get(Company, response.json()["company_id"])


def test_a_new_company_gets_the_workflow_only_for_a_repository(
    test_client, sqlite_session
):
    on = _created(
        test_client, sqlite_session, _InspectingNATS(True), git_base_branch=" develop "
    )
    assert (on.git_workflow, on.git_base_branch) == (True, "develop")
    assert (
        _created(test_client, sqlite_session, _InspectingNATS(False)).git_workflow
        is False
    )
    turned_off = _created(
        test_client, sqlite_session, _InspectingNATS(True), git_workflow=False
    )
    assert turned_off.git_workflow is False
    unobserved = _created(test_client, sqlite_session, None, git_workflow=True)
    assert unobserved.git_workflow is False
    test_client.app.state.nats_client = _InspectingNATS(True)
    bad = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Bad",
            "project_path": "/srv/app",
            "git_base_branch": "--upload-pack=x",
        },
    )
    assert bad.status_code == 422


@pytest.mark.usefixtures("enforcement")
def test_the_settings_read_the_base_status_viewer_reads_manager_edits(
    test_client, sqlite_session
):
    company = _company(sqlite_session)
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path="/srv/app"))
    sqlite_session.commit()
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/git"
    nats = _InspectingNATS(True, {"base_ref": "main", "base_behind_remote": 424})
    test_client.app.state.nats_client = nats

    plain = test_client.get(url, headers=viewer).json()
    assert "base_status" not in plain and nats.subjects == []
    checked = test_client.get(f"{url}?check=true", headers=viewer).json()
    assert checked["base_status"]["state"] == "observed"
    assert checked["base_status"]["base_behind_remote"] == 424
    assert nats.subjects == [
        ("workspace.git.base_status", {"workspace_root": "/srv/app", "base": ""})
    ]
    assert (
        test_client.put(url, json={"git_workflow": False}, headers=viewer).status_code
        == 403
    )

    test_client.app.state.nats_client = None
    unknown = test_client.get(f"{url}?check=true", headers=viewer).json()["base_status"]
    assert unknown == {"state": "unknown", "reason": "No workspace daemon answered"}


def test_the_integration_is_published_only_against_a_current_base(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="test",
        status="tests_passed",
        branch=f"fabrique/sprint-{sprint.id}",
        base="main",
        repository="/srv/app",
        commit="a" * 40,
        tasks=[],
        details={
            "base_status": {
                "base_commit": "b" * 40,
                "started_from": "origin/main",
                "base_behind_remote": 424,
            },
            "base_merge": {"branch": "origin/main", "commit": "c" * 40},
        },
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    spec = integration_publish.publish_spec(sqlite_session, row)

    assert spec["require_current_base"] is True
    assert (
        "Base `origin/main` at `bbbbbbbbbbbb` (the local copy was 424 commit(s) behind)"
        in spec["body"]
    )
    assert "`origin/main` merged in first as `cccccccccccc`" in spec["body"]


def test_the_board_note_is_said_only_when_the_workflow_was_skipped():
    assert git_workflow.skipped_note({}) == ""
    assert "not a repository" in git_workflow.skipped_note(
        {"git_workflow_skipped": "not a repository"}
    )


def test_the_board_note_promises_an_undo_only_when_a_snapshot_was_kept():
    kept = git_workflow.skipped_note(
        {"git_workflow_skipped": "not a repository", "snapshot": {"id": "s1"}}
    )
    assert "with a snapshot to undo them" in kept

    failed = git_workflow.skipped_note(
        {
            "git_workflow_skipped": "not a repository",
            "snapshot_error": "no snapshot was taken, this delivery cannot be undone: disk full",
        }
    )
    assert "snapshot to undo" not in failed
    assert "no snapshot was taken, this delivery cannot be undone: disk full" in failed

    neither = git_workflow.skipped_note({"git_workflow_skipped": "not a repository"})
    assert "snapshot to undo" not in neither
    assert "cannot be undone" in neither
