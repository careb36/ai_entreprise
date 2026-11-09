"""
Publishing a task's branch: the push, and its pull request or merge request.

The daemon pushes ``fabrique/task-<id>`` to ``origin`` with the person's own
Git credentials and opens the request with ``gh`` or ``glab`` when they are
installed, else answers with the page where a person opens it
(``vscode-daemon/git_workspace.py``). These tests push to a bare repository
in ``tmp_path`` standing in for GitHub, and put a fake ``gh`` on the PATH.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
from test_git_workflow import SPEC, daemon, git

PUBLISH = {
    "branch": "fabrique/task-7",
    "base": "",
    "title": "Add login",
    "body": "Evidence",
}


@pytest.fixture
def published_repo(tmp_path, monkeypatch):
    """A repository whose origin reads as GitHub and pushes to a bare one here."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "first")
    git(root, "remote", "add", "origin", "https://token@github.com/ana/app.git")
    git(root, "config", "remote.origin.pushurl", str(remote))
    # Fetches (the base's check against its remote) read the bare one too.
    git(
        root,
        "config",
        f"url.{remote}.insteadOf",
        "https://token@github.com/ana/app.git",
    )
    git(root, "push", "-q", "origin", "main")
    # No gh or glab unless a test puts one there.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv(
        "PATH",
        f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin",
    )
    return root, remote, bin_dir


def _fake_cli(bin_dir, name, script):
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_without_gh_the_branch_is_pushed_and_the_answer_is_the_compare_page(
    published_repo,
):
    root, remote, _ = published_repo
    commit = daemon.deliver_files(
        str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC
    )["git"]["commit"]

    reply = git_workspace.publish(str(root), PUBLISH)

    assert reply == {
        "ok": True,
        "provider": "github",
        "branch": "fabrique/task-7",
        "base": "main",
        "commit": commit,
        "url": "https://github.com/ana/app/compare/main...fabrique%2Ftask-7?expand=1",
        "how": "compare",
        "updated": False,
    }
    assert git(remote, "rev-parse", "refs/heads/fabrique/task-7") == commit
    assert "token" not in reply["url"]


def test_with_gh_a_draft_pull_request_is_opened_with_the_evidence(
    published_repo, tmp_path
):
    root, _, bin_dir = published_repo
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)
    seen = tmp_path / "gh-args"
    _fake_cli(
        bin_dir,
        "gh",
        f'[ "$2" = list ] && exit 0; printf "%s\\n" "$@" > {seen}; cat > {seen}.body; '
        "echo https://github.com/ana/app/pull/12",
    )

    reply = git_workspace.publish(str(root), {**PUBLISH, "base": "main"})

    assert (reply["how"], reply["url"]) == ("gh", "https://github.com/ana/app/pull/12")
    args = seen.read_text().splitlines()
    assert args[:3] == ["pr", "create", "--draft"]
    assert args[args.index("--head") + 1] == "fabrique/task-7"
    assert args[args.index("--title") + 1] == "Add login"
    assert Path(f"{seen}.body").read_text() == "Evidence"


def test_a_gh_that_fails_still_leaves_the_compare_page(published_repo):
    root, _, bin_dir = published_repo
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)
    # A diagnostic with a link is not a pull request.
    _fake_cli(
        bin_dir,
        "gh",
        "echo 'see https://github.com/ana/app/pull/1 or https://cli.github.com'; exit 1",
    )

    reply = git_workspace.publish(str(root), PUBLISH)

    assert reply["how"] == "compare"
    assert reply["url"].startswith("https://github.com/ana/app/compare/")


def test_an_open_pull_request_gets_this_runs_evidence(published_repo, tmp_path):
    root, _, bin_dir = published_repo
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)
    seen = tmp_path / "gh-args"
    _fake_cli(
        bin_dir,
        "gh",
        '[ "$2" = list ] && echo https://github.com/ana/app/pull/9 && exit 0; '
        f'[ "$2" = edit ] && printf "%s\\n" "$@" > {seen} && cat > {seen}.body && exit 0; exit 3',
    )

    reply = git_workspace.publish(str(root), {**PUBLISH, "body": "Second run"})

    assert (reply["how"], reply["url"], reply["updated"]) == (
        "gh",
        "https://github.com/ana/app/pull/9",
        True,
    )
    assert seen.read_text().splitlines()[:3] == [
        "pr",
        "edit",
        "https://github.com/ana/app/pull/9",
    ]
    assert Path(f"{seen}.body").read_text() == "Second run"


def test_a_branch_moved_past_the_runs_commit_is_not_published(published_repo):
    root, remote, _ = published_repo
    first = daemon.deliver_files(
        str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC
    )["git"]["commit"]
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 2\n"}], SPEC)

    with pytest.raises(git_workspace.GitError, match="moved on"):
        git_workspace.publish(str(root), {**PUBLISH, "commit": first})
    pushed = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "refs/heads/fabrique/task-7"],
        cwd=remote,
        capture_output=True,
        check=False,
    )
    assert pushed.returncode != 0  # nothing was pushed
    with pytest.raises(git_workspace.GitError, match="not a commit"):
        git_workspace.publish(str(root), {**PUBLISH, "commit": "HEAD~1"})


def test_what_cannot_be_published_is_refused(published_repo, tmp_path):
    root, _, _ = published_repo
    with pytest.raises(git_workspace.GitError, match="branch name"):
        git_workspace.publish(str(root), {**PUBLISH, "branch": "main"})
    with pytest.raises(git_workspace.GitError):  # never delivered: nothing to push
        git_workspace.publish(str(root), PUBLISH)
    lone = tmp_path / "lone"
    lone.mkdir()
    git(lone, "init", "-q", "-b", "main")
    with pytest.raises(git_workspace.GitError, match="no origin"):
        git_workspace.publish(str(lone), PUBLISH)


@pytest.mark.parametrize(
    ("provider", "web", "expected"),
    [
        (
            "github",
            "https://github.com/a/b",
            "https://github.com/a/b/compare/main...fabrique%2Ftask-1?expand=1",
        ),
        (
            "gitlab",
            "https://gitlab.com/g/p",
            (
                "https://gitlab.com/g/p/-/merge_requests/new?merge_request%5Bsource_branch%5D=fabrique%2Ftask-1"
                "&merge_request%5Btarget_branch%5D=main"
            ),
        ),
        (
            "bitbucket",
            "https://bitbucket.org/a/b",
            "https://bitbucket.org/a/b/pull-requests/new?source=fabrique%2Ftask-1&dest=main",
        ),
        ("other", None, None),
    ],
)
def test_the_compare_page_per_platform(provider, web, expected):
    assert (
        git_workspace.compare_url(provider, web, "main", "fabrique/task-1") == expected
    )


# --- the backend -------------------------------------------------------------

from models.agent_run import AgentRun, Verification
from test_git_workflow import _company, _member, enforcement  # noqa: F401


class _Daemon:
    """What the app's shutdown calls on its NATS client."""

    async def close(self):
        return None

    async def drain(self):
        return None


class _PublishingDaemon(_Daemon):
    """``request`` runs the real publish against the repository on disk."""

    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        try:
            return git_workspace.publish(payload["workspace_root"], payload)
        except git_workspace.GitError as e:
            return {"error": {"status": 409, "message": str(e)}}


def _delivered_run(session, company, root, *, git=True, ref="task-7"):
    reply = daemon.deliver_files(
        str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC
    )
    run = AgentRun(
        company_id=company.id,
        external_ref=ref,
        producer="le-fabrique-task-worker",
        agent_role="Developer",
        claims={"summary": "Adds a | b", "files": ["a.py", "b.py"]},
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    evidence = {"workspace_writes": reply["files"]}
    if git:
        evidence["git"] = reply["git"]
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[
                {
                    "name": "files_written",
                    "outcome": "pass",
                    "observation": "1 of 1 file read back",
                }
            ],
            evidence=evidence,
        )
    )
    session.commit()
    return run, reply["git"]


@pytest.mark.usefixtures("enforcement")
def test_a_manager_publishes_a_run_with_its_evidence_and_a_viewer_reads_it(
    sqlite_session,
    test_client,
    published_repo,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    root, remote, _ = published_repo
    company = _company(sqlite_session)
    run, info = _delivered_run(sqlite_session, company, root)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    nats = _PublishingDaemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    url = f"/v1/agent_runs/{run.id}/publish?company_id={company.id}"

    assert test_client.post(url, headers=viewer).status_code == 403
    published = test_client.post(url, headers=manager)

    assert published.status_code == 201, published.text
    body = published.json()
    assert (body["branch"], body["base"], body["commit"], body["how"]) == (
        "fabrique/task-7",
        "main",
        info["commit"],
        "compare",
    )
    assert body["url"].startswith("https://github.com/ana/app/compare/main...")
    assert git(remote, "rev-parse", "refs/heads/fabrique/task-7") == info["commit"]
    subject, spec = nats.requests[0]
    assert subject == "workspace.git.publish"
    assert spec["base"] == ""  # started from the checkout: the remote's default branch
    assert spec["commit"] == info["commit"]  # pinned to what this run's evidence covers
    assert "**Verdict:** verified" in spec["body"]
    assert "| files_written | pass | 1 of 1 file read back |" in spec["body"]
    assert "- `a.py` added (+1 −0)" in spec["body"]
    # The producer's words stay apart, labelled as its claims.
    claimed = spec["body"].split("## Claimed by the producer (not observed)")[1]
    assert "Adds a \\| b" in claimed and "`b.py`" in claimed
    assert spec["body"].endswith(f"Fabrique-Run: {run.id}\n")

    listed = test_client.get(
        f"/v1/agent_runs/{run.id}/publications",
        params={"company_id": company.id},
        headers=viewer,
    )
    assert [p["url"] for p in listed.json()["publications"]] == [body["url"]]
    assert body["audit_log_id"] is not None


def test_a_run_without_a_branch_or_a_daemon_is_not_published(
    sqlite_session, test_client, published_repo, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    root, _, _ = published_repo
    company = _company(sqlite_session)
    plain, _ = _delivered_run(sqlite_session, company, root, git=False)
    branched, _ = _delivered_run(sqlite_session, company, root, ref="task-8")

    class _NoDaemon(_Daemon):
        async def request(self, subject, payload, timeout=5.0):
            raise TimeoutError("nobody answered")

    monkeypatch.setattr(test_client.app.state, "nats_client", _NoDaemon())
    refused = test_client.post(
        f"/v1/agent_runs/{plain.id}/publish", params={"company_id": company.id}
    )
    assert refused.status_code == 409
    assert "Git branch" in refused.json()["detail"]
    offline = test_client.post(
        f"/v1/agent_runs/{branched.id}/publish", params={"company_id": company.id}
    )
    assert offline.status_code == 503
    other = _company(sqlite_session)
    assert (
        test_client.post(
            f"/v1/agent_runs/{branched.id}/publish", params={"company_id": other.id}
        ).status_code
        == 404
    )
