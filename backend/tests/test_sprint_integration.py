"""
Integrating a sprint (issue #314): its task branches merged, the suite run, one request.

The daemon merges the done tasks' branches into ``fabrique/sprint-<id>`` in
dependency order with real git (``vscode-daemon/sprint_integration.py``),
the project's test command runs on the integrated worktree, the result is
kept as a ``SprintIntegration`` and a Manager publishes the branch as one
pull or merge request. These tests run real git in ``tmp_path``, with a bare
repository standing in for the remote.
"""

import asyncio
import contextlib
import shlex
import sys
import threading
import weakref
from pathlib import Path

import pytest
from config import settings
from history_check import check_events
from models import AgentRun, BacklogItem, ProjectDiscovery, Sprint, Verification
from models.integration import SprintIntegration
from services import integration, integration_jobs, replicas, sprint_close, task_worker
from services.loop.worker import close as worker_close
from services.platform import hooks, locks
from services.tasks import task_events
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import sprint_integration
import test_git_publish
import test_git_workflow
import worker_stages
from test_git_workflow import _company, _member, daemon, enforcement, git  # noqa: F401

# The fixtures of the Git workflow's tests: a repository, and one with a remote.
repo = test_git_workflow.repo
published_repo = test_git_publish.published_repo


def _deliver(root, task_id, files, merge=()):
    reply = daemon.deliver_files(
        str(root),
        [{"path": p, "content": c} for p, c in files.items()],
        {
            "branch": f"fabrique/task-{task_id}",
            "base": "",
            "message": f"Task {task_id}",
            "merge": list(merge),
        },
    )
    assert reply["ok"], reply
    return reply["git"]["commit"]


# --- the daemon --------------------------------------------------------------


def test_two_task_branches_are_merged_in_order_and_the_checkout_is_untouched(repo):
    main_head = git(repo, "rev-parse", "HEAD")
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    _deliver(repo, 2, {"b.py": "from a import A\nB = A + 1\n"}, ["fabrique/task-1"])

    reply = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-5",
            "base": "",
            "merge": [
                "fabrique/task-1",
                "fabrique/task-2",
                "fabrique/task-99",
                "main",
            ],
        },
    )

    assert reply["ok"] is True and reply["conflict"] is None
    assert [m["branch"] for m in reply["merged"]] == [
        "fabrique/task-1",
        "fabrique/task-2",
    ]
    assert reply["start"] == main_head
    assert git(repo, "rev-parse", "fabrique/sprint-5") == reply["commit"]
    assert (reply["missing"], reply["refused"]) == (["fabrique/task-99"], ["main"])
    worktree = Path(reply["worktree"])
    assert (worktree / "a.py").exists() and (worktree / "b.py").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert not (repo / "a.py").exists()

    again = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-5",
            "merge": ["fabrique/task-1", "fabrique/task-2"],
        },
    )
    assert again["merged"] == []
    assert again["already_merged"] == ["fabrique/task-1", "fabrique/task-2"]
    assert again["commit"] == reply["commit"]


def test_a_conflict_is_observed_with_its_files_and_the_merge_aborted(repo):
    _deliver(repo, 1, {"app.py": "x = 1\n"})
    _deliver(repo, 2, {"app.py": "x = 2\n"})
    _deliver(repo, 3, {"c.py": "c = 3\n"})

    reply = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-6",
            "merge": ["fabrique/task-1", "fabrique/task-2", "fabrique/task-3"],
        },
    )

    assert reply["ok"] is False
    [first] = reply["merged"]
    assert first["branch"] == "fabrique/task-1"
    conflict = reply["conflict"]
    assert (conflict["branch"], conflict["files"]) == ("fabrique/task-2", ["app.py"])
    assert "<<<<<<<" in conflict["hunks"] and "x = 2" in conflict["hunks"]
    assert reply["not_attempted"] == ["fabrique/task-3"]
    # The branch stays at the last clean merge, the worktree clean.
    assert reply["commit"] == first["commit"]
    assert git(Path(reply["worktree"]), "status", "--porcelain") == ""


def test_only_a_sprint_branch_is_integrated(repo):
    with pytest.raises(git_workspace.GitError):
        sprint_integration.integrate(str(repo), {"branch": "main", "merge": []})
    with pytest.raises(git_workspace.GitError):
        sprint_integration.integrate(
            str(repo / "missing"), {"branch": "fabrique/sprint-1", "merge": []}
        )


# --- the backend -------------------------------------------------------------


class _Daemon:
    """The real daemon's handlers behind ``request``."""

    def __init__(self):
        self.requests = []
        self.offline = False

    async def close(self):
        return None

    async def drain(self):
        return None

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if self.offline:
            raise TimeoutError("nobody answered")
        try:
            if subject == "workspace.git.integrate":
                return sprint_integration.integrate(payload["workspace_root"], payload)
            if subject == "workspace.git.publish":
                return git_workspace.publish(payload["workspace_root"], payload)
        except git_workspace.GitError as e:
            return {"error": {"status": 409, "message": str(e)}}
        if subject == "workspace.execute":
            return daemon.execute_shell_command(
                payload["command"], payload["workspace_root"]
            )
        raise AssertionError(subject)


@pytest.fixture
def suite(monkeypatch, tmp_path):
    """The project's test command: both tasks' modules import together."""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    command = f'{shlex.quote(sys.executable)} -c "import a, b"'
    monkeypatch.setattr(settings, "DELIVERY_TEST_COMMAND", command)
    return command


def _sprint(session, root, *, on=True):
    company = _company(session, on=on)
    session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    sprint = Sprint(company_id=company.id, sprint_name="Auth", status="completed")
    session.add(sprint)
    session.commit()
    return company, sprint


def _task(session, sprint, title, status="done", depends_on=None):
    item = BacklogItem(
        sprint_id=sprint.id, title=title, status=status, depends_on=depends_on or []
    )
    session.add(item)
    session.commit()
    return item


def _run(
    session,
    company,
    item,
    summary,
    files=(),
    verdict="verified",
    artifact="x",
    version=1,
):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=version,
        producer="le-fabrique-task-worker",
        agent_role="Developer",
        claims={"summary": summary},
        artifact_text=artifact,
        artifact_digest="d",
        artifact_size=len(artifact),
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "files_written", "outcome": "pass", "observation": "ok"}],
            # None: the daemon never reported (no files read back).
            evidence={
                "workspace_writes": None
                if files is None
                else [
                    {
                        "path": path,
                        "status": "written",
                        "change": "added",
                        "additions": 1,
                        "deletions": 0,
                    }
                    for path in files
                ]
            },
        )
    )
    session.commit()
    return run


def _two_done_tasks(session, root, *, conflict=False):
    company, sprint = _sprint(session, root)
    first = _task(session, sprint, "Constants")
    second = _task(session, sprint, "Use them", depends_on=[first.id])
    left = _task(session, sprint, "Docs", status="blocked")
    if conflict:
        _deliver(root, first.id, {"app.py": "x = 1\n"})
        _deliver(root, second.id, {"app.py": "x = 2\n"})
    else:
        _deliver(root, first.id, {"a.py": "A = 1\n"})
        _deliver(
            root,
            second.id,
            {"b.py": "from a import A\n"},
            [f"fabrique/task-{first.id}"],
        )
    _run(session, company, first, "Adds A | the constant", ["a.py"])
    _run(session, company, second, "Uses A", ["b.py"])
    return company, sprint, first, second, left


@pytest.mark.usefixtures("enforcement")
def test_a_manager_integrates_the_suite_runs_on_the_result_and_one_request_is_published(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, remote, _ = published_repo
    company, sprint, first, second, left = _two_done_tasks(sqlite_session, root)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    url = f"/v1/sprints/{sprint.id}"

    assert test_client.post(f"{url}/integrate", headers=viewer).status_code == 403
    done = test_client.post(f"{url}/integrate", headers=manager)

    assert done.status_code == 201, done.text
    body = done.json()
    assert body["status"] == "tests_passed"
    assert body["branch"] == f"fabrique/sprint-{sprint.id}"
    assert [m["task_id"] for m in body["merged"]] == [first.id, second.id]
    assert body["details"]["left_out"] == [
        {"task_id": left.id, "title": "Docs", "status": "blocked"}
    ]
    assert body["test_run"]["exit_code"] == 0
    assert body["test_run"]["command"].startswith(suite)
    assert [s for s, _ in nats.requests] == [
        "workspace.git.integrate",
        "workspace.execute",
    ]
    # The suite ran at the integrated commit, in a worktree detached there.
    tested_in = nats.requests[1][1]["workspace_root"]
    assert tested_in == body["test_run"]["workspace_root"]
    assert "@" + body["commit"][:12] + "-" in tested_in
    assert git(Path(tested_in), "rev-parse", "HEAD") == body["commit"]
    assert body["tested_commit"] == body["commit"]
    assert [(t["task_id"], t["state"]) for t in body["tasks"]] == [
        (first.id, "merged"),
        (second.id, "merged"),
    ]
    assert body["tasks"][0]["source_commit"] == git(
        root, "rev-parse", f"fabrique/task-{first.id}"
    )
    assert git(root, "rev-parse", body["branch"]) == body["commit"]
    assert body["audit_log_id"] is not None

    read = test_client.get(f"{url}/integration", headers=viewer).json()
    assert read["latest"]["id"] == body["id"]
    assert read["merge_order"][:2] == [first.id, second.id]
    assert [h["status"] for h in read["history"]] == ["tests_passed"]

    assert (
        test_client.post(f"{url}/integration/publish", headers=viewer).status_code
        == 403
    )
    published = test_client.post(f"{url}/integration/publish", headers=manager)
    assert published.status_code == 201, published.text
    publication = published.json()["publication"]
    assert (publication["branch"], publication["commit"], publication["how"]) == (
        body["branch"],
        body["commit"],
        "compare",
    )
    assert git(remote, "rev-parse", f"refs/heads/{body['branch']}") == body["commit"]
    _, spec = nats.requests[-1]
    text = spec["body"]
    assert "**Integration:** tests_passed" in text
    assert f"### Task {first.id}: Constants" in text
    assert f"### Task {second.id}: Use them" in text
    assert "**Verdict:** verified" in text
    assert "- `a.py` added (+1 −0)" in observed_part(text)
    assert "exit code 0" in text
    assert f"Not done, left out: task {left.id}" in text
    observed, claimed = text.split("## Claimed by the producers (not observed)")
    assert "Adds A" not in observed and "Adds A \\| the constant" in claimed
    assert text.endswith(
        f"Fabrique-Sprint: {sprint.id}\nFabrique-Integration: {body['id']}\n"
    )
    # The per-task branch is still there to publish on its own.
    assert git(root, "rev-parse", f"fabrique/task-{first.id}")


def observed_part(text):
    return text.split("## Claimed by the producers (not observed)")[0]


@pytest.mark.usefixtures("enforcement")
def test_a_conflict_waits_for_a_person_and_is_not_published(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, _, second, _ = _two_done_tasks(sqlite_session, root, conflict=True)
    manager = _member(sqlite_session, company.id, "Manager")
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)

    body = test_client.post(f"/v1/sprints/{sprint.id}/integrate", headers=manager)

    assert body.status_code == 201, body.text
    row = body.json()
    assert row["status"] == "conflict"
    assert (row["conflict"]["task_id"], row["conflict"]["files"]) == (
        second.id,
        ["app.py"],
    )
    assert row["test_run"] is None  # nothing integrated to test
    assert [s for s, _ in nats.requests] == ["workspace.git.integrate"]
    inbox = test_client.get(
        "/v1/inbox", params={"company_id": company.id}, headers=manager
    ).json()
    [waiting] = inbox["integrations"]
    assert (waiting["sprint_id"], waiting["status"], waiting["conflict_files"]) == (
        sprint.id,
        "conflict",
        ["app.py"],
    )
    assert inbox["total"] >= 1
    refused = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/publish", headers=manager
    )
    assert refused.status_code == 409
    assert "conflict" in refused.json()["detail"]


def test_a_failing_suite_waits_for_a_person(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    monkeypatch.setattr(
        settings,
        "DELIVERY_TEST_COMMAND",
        f'{shlex.quote(sys.executable)} -c "import sys; sys.exit(3)"',
    )
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())

    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert row["status"] == "tests_failed"
    assert row["test_run"]["exit_code"] == 3
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [i["status"] for i in inbox["integrations"]] == ["tests_failed"]


def test_without_the_tests_enabled_the_integration_says_it_is_untested(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)

    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert row["status"] == "untested"
    assert row["test_run"]["command"] is None
    assert [s for s, _ in nats.requests] == ["workspace.git.integrate"]


def test_what_cannot_be_integrated_is_refused(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    _, off = _sprint(sqlite_session, root, on=False)
    _task(sqlite_session, off, "A")
    assert test_client.post(f"/v1/sprints/{off.id}/integrate").status_code == 409
    _, idle = _sprint(sqlite_session, root)
    _task(sqlite_session, idle, "A", status="blocked")
    refused = test_client.post(f"/v1/sprints/{idle.id}/integrate")
    assert refused.status_code == 409 and "done" in refused.json()["detail"]
    assert (
        test_client.post(f"/v1/sprints/{idle.id}/integration/publish").status_code
        == 409
    )
    assert test_client.post("/v1/sprints/999999/integrate").status_code == 404

    _, ready, *_ = _two_done_tasks(sqlite_session, root)
    nats.offline = True
    assert test_client.post(f"/v1/sprints/{ready.id}/integrate").status_code == 503


@pytest.mark.usefixtures("enforcement")
def test_another_companys_member_neither_reads_nor_integrates(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    other = _company(sqlite_session)
    outsider = _member(sqlite_session, other.id, "Manager")
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    url = f"/v1/sprints/{sprint.id}"

    assert test_client.get(f"{url}/integration", headers=outsider).status_code == 403
    assert test_client.post(f"{url}/integrate", headers=outsider).status_code == 403
    assert (
        test_client.post(f"{url}/integration/publish", headers=outsider).status_code
        == 403
    )
    assert test_client.post(f"{url}/integrate").status_code == 401
    inbox = test_client.get(
        "/v1/inbox", params={"company_id": other.id}, headers=outsider
    ).json()
    assert inbox["integrations"] == []


def test_a_closed_sprint_is_integrated_on_its_own_when_the_workflow_is_on(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    _, off = _sprint(sqlite_session, root, on=False)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def close_both():
        integration_jobs.schedule_after_close(nats, engine, off.id)
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    asyncio.run(close_both())

    with Session(engine) as session:
        rows = session.exec(select(SprintIntegration)).all()
    [row] = [r for r in rows if r.sprint_id in (sprint.id, off.id)]
    assert (row.sprint_id, row.trigger, row.status) == (
        sprint.id,
        "sprint_closed",
        "tests_passed",
    )
    assert row.requested_by == "task_worker"


@pytest.mark.parametrize("reason", ["sprint_completed", sprint_close.RECLOSED])
def test_the_loop_closing_a_sprint_emits_sprint_closed_and_release_integrates_it(
    sqlite_session, published_repo, suite, monkeypatch, reason
):
    """The loop never calls release: it emits ``sprint.closed`` at the close,
    a first one or one after a reopening, and release's handler starts the
    integration in the background, only with the Git workflow on."""
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    _, off = _sprint(sqlite_session, root, on=False)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    emitted = []

    def _record(**payload):
        emitted.append(payload)

    worker_stages.use_engine(monkeypatch, engine)
    monkeypatch.setattr(worker_close, "_build_sprint_retrospective", lambda _id: None)
    monkeypatch.setattr(
        replicas,
        "close_once",
        lambda _engine, _sprint_id, _close, _retro: {
            "completed": True,
            "reason": reason,
        },
    )
    assert integration_jobs.on_sprint_closed in hooks.handlers(hooks.SPRINT_CLOSED)
    hooks.on(hooks.SPRINT_CLOSED, _record)

    async def close_both():
        await task_worker._finalize_sprint_if_ready(nats, off.id)
        await task_worker._finalize_sprint_if_ready(nats, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    try:
        asyncio.run(close_both())
    finally:
        hooks.off(hooks.SPRINT_CLOSED, _record)

    assert emitted == [
        {"nats_client": nats, "engine": engine, "sprint_id": off.id},
        {"nats_client": nats, "engine": engine, "sprint_id": sprint.id},
    ]
    with Session(engine) as session:
        rows = session.exec(select(SprintIntegration)).all()
    [row] = [r for r in rows if r.sprint_id in (sprint.id, off.id)]
    assert (row.sprint_id, row.trigger, row.status) == (
        sprint.id,
        "sprint_closed",
        "tests_passed",
    )


def test_a_sprint_closed_handler_that_fails_never_stops_the_others(monkeypatch):
    calls, scheduled = [], []

    def _fails(**payload):
        raise RuntimeError("down")

    def _records(**payload):
        calls.append(payload)

    monkeypatch.setattr(hooks, "_HANDLERS", {})
    hooks.on(hooks.SPRINT_CLOSED, _fails)
    hooks.on(hooks.SPRINT_CLOSED, _records)
    hooks.on(hooks.SPRINT_CLOSED, _records)  # registered once
    integration_jobs.install()
    integration_jobs.install()
    assert hooks.handlers(hooks.SPRINT_CLOSED) == [
        _fails,
        _records,
        integration_jobs.on_sprint_closed,
    ]
    monkeypatch.setattr(
        integration_jobs, "schedule_after_close", lambda *a: scheduled.append(a)
    )

    hooks.emit(hooks.SPRINT_CLOSED, nats_client="n", engine="e", sprint_id=7)

    assert calls == [{"nats_client": "n", "engine": "e", "sprint_id": 7}]
    assert scheduled == [("n", "e", 7)]


def test_the_status_reads_the_observations():
    assert integration.status_of({"files": ["a"]}, None) == "conflict"
    assert integration.status_of(None, {"exit_code": 1}) == "tests_failed"
    assert integration.status_of(None, {"timed_out": True}) == "tests_failed"
    assert integration.status_of(None, {"exit_code": 0}) == "tests_passed"
    assert integration.status_of(None, {"command": None}) == "untested"


def test_the_daemon_dockerfile_ships_the_module():
    dockerfile = Path(__file__).resolve().parents[2] / "vscode-daemon" / "Dockerfile"
    assert "sprint_integration.py" in dockerfile.read_text(encoding="utf-8")


# --- review findings (PR #380) -----------------------------------------------


def test_the_daemon_tests_a_worktree_a_later_integration_cannot_move(repo):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    first = sprint_integration.integrate(
        str(repo), {"branch": "fabrique/sprint-8", "merge": ["fabrique/task-1"]}
    )
    _deliver(repo, 2, {"b.py": "B = 2\n"})
    second = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-8",
            "merge": ["fabrique/task-1", "fabrique/task-2"],
        },
    )

    assert second["commit"] != first["commit"]
    assert first["test_worktree"] != second["test_worktree"]
    assert git(Path(second["test_worktree"]), "rev-parse", "HEAD") == second["commit"]
    # The sprint branch's own worktree moved on; the snapshot is detached at its commit.
    assert git(Path(second["worktree"]), "rev-parse", "HEAD") == second["commit"]
    assert (
        git(Path(second["test_worktree"]), "rev-parse", "--abbrev-ref", "HEAD")
        == "HEAD"
    )
    assert second["sources"]["fabrique/task-2"] == git(
        repo, "rev-parse", "fabrique/task-2"
    )


class _RacingDaemon(_Daemon):
    """While the first suite runs, another integration moves the sprint branch."""

    def __init__(self, root, task_id):
        super().__init__()
        self.root, self.task_id, self.heads = root, task_id, []

    async def request(self, subject, payload, timeout=5.0):
        if subject == "workspace.execute":
            if not self.heads:
                # Another integration moves the sprint branch and its worktree first.
                _deliver(self.root, self.task_id, {"a2.py": "A2 = 2\n"})
                branch = self.integrate_payload["branch"]
                sprint_integration.integrate(str(self.root), self.integrate_payload)
                self.moved_to = git(self.root, "rev-parse", branch)
            self.heads.append(git(Path(payload["workspace_root"]), "rev-parse", "HEAD"))
            await asyncio.sleep(0)
        if subject == "workspace.git.integrate":
            self.integrate_payload = payload
        return await super().request(subject, payload, timeout)


def test_concurrent_integrations_each_record_the_commit_they_tested(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, first, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _RacingDaemon(root, first.id)

    async def twice():
        return await asyncio.gather(
            *(
                integration.integrate(
                    nats, engine, sprint.id, trigger="manual", requested_by="human:A"
                )
                for _ in range(2)
            )
        )

    rows = asyncio.run(twice())

    # The first attempt tested its own commit, not where the branch was moved to.
    for row, head in zip(rows, nats.heads, strict=True):
        assert row.tested_commit == row.commit == head
    assert rows[0].commit != nats.moved_to == rows[1].commit


def test_the_request_shows_the_runs_recorded_at_integration_not_later_ones(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, first, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    recorded = row["tasks"][0]
    assert recorded["run_id"] is not None and recorded["verification_id"] is not None
    later = _run(
        sqlite_session, company, first, "LATER CLAIM", ["z.py"], "refuted", version=2
    )

    text = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/publish"
    ).json() and _published_body(test_client)

    assert (
        f"Run {recorded['run_id']}, verification {recorded['verification_id']}" in text
    )
    assert f"Run {later.id}" not in text
    assert "LATER CLAIM" not in text and "z.py" not in text


def _published_body(test_client):
    nats = test_client.app.state.nats_client
    [(_, spec)] = [r for r in nats.requests if r[0] == "workspace.git.publish"][-1:]
    return spec["body"]


DIFF = """diff --git a/c.py b/c.py
new file mode 100644
--- /dev/null
+++ b/c.py
@@ -0,0 +1 @@
+c = 1
"""


def test_a_diff_the_producer_sent_is_a_claim_never_observed(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, _, second, _ = _two_done_tasks(sqlite_session, root)
    # The second task's latest run: no files read back, only a diff it sent.
    _run(sqlite_session, company, second, "Sent a diff", None, artifact=DIFF, version=2)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    assert test_client.post(f"/v1/sprints/{sprint.id}/integrate").status_code == 201

    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)

    observed, claimed = text.split("## Claimed by the producers (not observed)")
    assert "c.py" not in observed
    assert f"Task {second.id}, files in the diff it sent: `c.py`" in claimed


def test_an_unchanged_rerun_still_describes_every_task(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, first, second, _ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    test_client.post(f"/v1/sprints/{sprint.id}/integrate")

    again = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert again["merged"] == []
    assert {t["state"] for t in again["tasks"]} == {"already_merged"}
    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)
    assert f"### Task {first.id}: Constants" in text
    assert f"### Task {second.id}: Use them" in text
    assert "already in" in text


def _approved(session, *items):
    for item in items:
        task_events.record(
            session,
            item,
            "reviewed",
            "QA",
            author="QA",
            text="APPROVED: fine",
            status="done",
        )
    session.commit()


def _event_dicts(session, item_id):
    return [
        {
            "id": e.id,
            "kind": e.event_type,
            "task_status": e.task_status,
            "payload": e.payload,
        }
        for e in task_events.for_task(session, item_id)
    ]


def test_each_task_s_part_in_an_integration_is_its_event(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    """Merged, then skipped on a second run: an ``integration`` event per task,
    by the System, rendered as its comment; the status stays done; the task
    left out gets none."""
    root, _, _ = published_repo
    _, sprint, first, second, left = _two_done_tasks(sqlite_session, root)
    _approved(sqlite_session, first, second)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())

    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    again = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    sqlite_session.expire_all()
    for item in (first, second):
        events = [
            e
            for e in task_events.for_task(sqlite_session, item.id)
            if e.event_type == "integration"
        ]
        assert [(e.actor, e.payload["state"]) for e in events] == [
            ("system", "merged"),
            ("system", "already_merged"),
        ]
        assert [e.payload["integration_id"] for e in events] == [row["id"], again["id"]]
        assert all(e.task_status is None for e in events)
        task = sqlite_session.get(BacklogItem, item.id)
        assert task.status == "done"
        assert [c["event_id"] for c in task.comments[-2:]] == [e.id for e in events]
        assert task.comments[-1]["author"] == "System"
        assert "skipped" in task.comments[-1]["text"]
        assert (
            check_events(task.comments, "done", _event_dicts(sqlite_session, item.id))
            == []
        )
    assert task_events.for_task(sqlite_session, left.id) == []


def test_a_conflict_is_an_event_on_the_task_whose_branch_conflicted(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, first, second, _ = _two_done_tasks(sqlite_session, root, conflict=True)
    _approved(sqlite_session, first, second)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())

    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert row["status"] == "conflict"
    sqlite_session.expire_all()
    states = {
        item.id: [
            e.payload["state"]
            for e in task_events.for_task(sqlite_session, item.id)
            if e.event_type == "integration"
        ]
        for item in (first, second)
    }
    assert states == {first.id: ["merged"], second.id: ["conflict"]}
    comment = sqlite_session.get(BacklogItem, second.id).comments[-1]
    assert "conflict" in comment["text"] and "app.py" in comment["text"]


def test_a_done_task_without_its_branch_makes_the_integration_incomplete(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    branchless = _task(sqlite_session, sprint, "Never on a branch")
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())

    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert row["status"] == "incomplete"
    assert [t["state"] for t in row["tasks"] if t["task_id"] == branchless.id] == [
        "missing"
    ]
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    [waiting] = inbox["integrations"]
    assert (waiting["status"], waiting["missing_task_ids"]) == (
        "incomplete",
        [branchless.id],
    )
    refused = test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    assert refused.status_code == 409 and "missing" in refused.json()["detail"]


def test_the_inbox_counts_every_waiting_integration_beyond_the_limit(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    monkeypatch.setattr(
        settings,
        "DELIVERY_TEST_COMMAND",
        f'{shlex.quote(sys.executable)} -c "import sys; sys.exit(1)"',
    )
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    other = Sprint(company_id=company.id, sprint_name="Two", status="completed")
    sqlite_session.add(other)
    sqlite_session.commit()
    task = _task(sqlite_session, other, "More")
    _deliver(root, task.id, {"m.py": "m = 1\n"})
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    for sprint_id in (sprint.id, other.id):
        assert (
            test_client.post(f"/v1/sprints/{sprint_id}/integrate").json()["status"]
            == "tests_failed"
        )

    inbox = test_client.get(
        "/v1/inbox", params={"company_id": company.id, "limit": 1}
    ).json()

    assert len(inbox["integrations"]) == 1
    assert inbox["integrations_total"] == 2
    assert inbox["total"] == inbox["blocked_total"] + 2


def test_an_integration_the_daemon_missed_at_close_is_pending_and_retried(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    nats.offline = True

    async def close_then_retry():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        return await integration_jobs.retry_pending(nats, engine)

    assert asyncio.run(close_then_retry()) == 1  # still offline: tried again
    with Session(engine) as session:
        pending = integration.latest(session, sprint.id)
        assert (pending.status, pending.details["attempts"]) == ("pending", 2)
        assert pending.commit is None
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [(i["status"], i["reason"]) for i in inbox["integrations"]] == [
        ("pending", "the workspace daemon did not answer")
    ]
    refused = test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    assert refused.status_code == 409

    nats.offline = False
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 1
    with Session(engine) as session:
        done = integration.latest(session, sprint.id)
    assert (done.status, done.trigger) == ("tests_passed", "sprint_closed")
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 0
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert inbox["integrations"] == []


def test_a_daemon_that_refuses_at_close_leaves_an_error_for_a_person(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()

    class _Refusing(_Daemon):
        async def request(self, subject, payload, timeout=5.0):
            return {"error": {"status": 409, "message": "fatal: bad base"}}

    async def close():
        integration_jobs.schedule_after_close(_Refusing(), engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    asyncio.run(close())

    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [(i["status"], i["reason"]) for i in inbox["integrations"]] == [
        ("error", "fatal: bad base")
    ]
    # An error is not retried on its own: a person integrates again.
    assert asyncio.run(integration_jobs.retry_pending(_Refusing(), engine)) == 0


# --- review findings (PR #384) -----------------------------------------------


def test_the_suite_never_runs_on_files_a_previous_suite_left(repo):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-9", "merge": ["fabrique/task-1"]}
    first = sprint_integration.integrate(str(repo), {**spec, "attempt": "one"})
    snapshot = Path(first["test_worktree"])
    (snapshot / "a.py").write_text("A = 'changed by a test'\n")
    (snapshot / "generated_test.py").write_text("assert True\n")

    again = sprint_integration.integrate(str(repo), {**spec, "attempt": "two"})

    assert again["commit"] == first["commit"]
    fresh = Path(again["test_worktree"])
    assert fresh != snapshot and fresh.name.endswith("-two")
    assert (fresh / "a.py").read_text() == "A = 1\n"
    assert not (fresh / "generated_test.py").exists()
    # The first attempt's files are its own: a retry never resets them under it.
    assert (snapshot / "a.py").read_text() == "A = 'changed by a test'\n"


def test_a_task_branch_is_read_once_checked_merged_and_recorded_at_that_commit(
    repo, monkeypatch
):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    read = git(repo, "rev-parse", "fabrique/task-1")
    real_merge = sprint_integration.merge_one

    def merge_after_a_delivery(worktree, ref, message):
        # A delivery moves the task branch between the read and the merge.
        _deliver(repo, 1, {"late.py": "LATE = 1\n"})
        return real_merge(worktree, ref, message)

    monkeypatch.setattr(sprint_integration, "merge_one", merge_after_a_delivery)
    reply = sprint_integration.integrate(
        str(repo), {"branch": "fabrique/sprint-10", "merge": ["fabrique/task-1"]}
    )

    [merged] = reply["merged"]
    assert reply["sources"]["fabrique/task-1"] == merged["source"] == read
    assert git(repo, "rev-parse", f"{merged['commit']}^2") == read
    assert git(repo, "rev-parse", "fabrique/task-1") != read
    assert not (Path(reply["worktree"]) / "late.py").exists()


def test_a_closed_sprints_integration_is_kept_before_the_daemon_is_asked(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def close_then_die():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        # The backend stops before the background attempt runs.
        for task in list(integration_jobs._RUNNING):
            task.cancel()
        await asyncio.gather(*list(integration_jobs._RUNNING), return_exceptions=True)

    asyncio.run(close_then_die())
    assert nats.requests == []
    with Session(engine) as session:
        job = integration.latest(session, sprint.id)
        assert (job.status, job.trigger, job.details["attempts"]) == (
            "running",
            "sprint_closed",
            0,
        )
        job_id = job.id

    # After the restart, the retry loop takes the job nobody holds.
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 1
    with Session(engine) as session:
        rows = session.exec(
            select(SprintIntegration).where(SprintIntegration.sprint_id == sprint.id)
        ).all()
    [row] = rows
    assert (row.id, row.status, row.details["attempts"]) == (job_id, "tests_passed", 1)
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 0


def test_two_attempts_at_one_job_integrate_once(sqlite_session, published_repo, suite):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def twice():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        tried = await integration_jobs.retry_pending(nats, engine)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        tried += await integration_jobs.retry_pending(nats, engine)
        return tried

    assert asyncio.run(twice()) == 1
    assert [s for s, _ in nats.requests].count("workspace.git.integrate") == 1


def test_a_sprint_another_replica_integrates_is_left_to_it(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)

    @contextlib.asynccontextmanager
    async def held_elsewhere(engine, sprint_id):
        yield False

    monkeypatch.setattr(integration, "_database_lock", held_elsewhere)
    refused = test_client.post(f"/v1/sprints/{sprint.id}/integrate")
    assert refused.status_code == 409 and "already running" in refused.json()["detail"]

    async def close():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        return await integration_jobs.retry_pending(nats, engine)

    assert asyncio.run(close()) == 0
    assert nats.requests == []
    with Session(engine) as session:
        assert integration.latest(session, sprint.id).status == "running"


class _Conn:
    def __init__(self, owned):
        self.owned, self.sql, self.threads = owned, [], set()

    def execute(self, statement, params):
        self.threads.add(threading.get_ident())
        self.sql.append((str(statement), params))
        return type("R", (), {"scalar": lambda _self: self.owned})()

    def commit(self):
        return None

    def close(self):
        return None

    def invalidate(self):
        raise AssertionError("not expected")


@pytest.mark.parametrize("owned", [True, False])
def test_on_postgres_the_sprint_is_held_with_an_advisory_lock_off_the_loop(
    owned, monkeypatch
):
    conn = _Conn(owned)
    engine = type(
        "E",
        (),
        {"dialect": type("D", (), {"name": "postgresql"})(), "connect": lambda _: conn},
    )()
    monkeypatch.setattr(integration, "_lock_engine", lambda _engine: engine)

    async def hold():
        async with integration._database_lock(engine, 42) as got:
            return got, threading.get_ident()

    got, loop_thread = asyncio.run(hold())

    assert got is owned
    calls = [sql.split("(")[0] for sql, _ in conn.sql]
    expected = ["SELECT pg_try_advisory_lock"] + (
        ["SELECT pg_advisory_unlock"] if owned else []
    )
    assert calls == expected
    key = locks.lock_key(locks.SPRINT_INTEGRATION, 42)
    assert all(p["k"] == key for _, p in conn.sql)
    # Taken and released in a thread: a checkout never blocks the event loop.
    assert loop_thread not in conn.threads


def test_lock_connections_come_from_a_small_pool_of_their_own():
    from sqlalchemy.engine import make_url

    app = type(
        "E",
        (),
        {
            "dialect": type("D", (), {"name": "postgresql"})(),
            "url": make_url("postgresql://u:p@db.invalid/fabrique"),
        },
    )()

    lock_engine = integration._lock_engine(app)

    assert lock_engine is integration._lock_engine(app)
    assert lock_engine.pool.size() == integration.MAX_CONCURRENT
    assert lock_engine.pool._max_overflow == 0
    assert lock_engine.url.password == "p"
    # SQLite has no advisory locks: the same engine, never a pool.
    sqlite = type("E", (), {"dialect": type("D", (), {"name": "sqlite"})()})()
    assert integration._lock_engine(sqlite) is sqlite


class _CountingDaemon(_Daemon):
    def __init__(self):
        super().__init__()
        self.in_flight = self.most = 0

    async def request(self, subject, payload, timeout=5.0):
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return await super().request(subject, payload, timeout)
        finally:
            self.in_flight -= 1


def test_integrations_of_different_sprints_run_within_the_limit(
    sqlite_session, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    sprints = [_two_done_tasks(sqlite_session, root)[1] for _ in range(3)]
    engine = sqlite_session.get_bind()
    monkeypatch.setattr(integration, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(integration, "_SLOTS", weakref.WeakKeyDictionary())
    nats = _CountingDaemon()

    async def all_at_once():
        return await asyncio.gather(
            *(
                integration.integrate(
                    nats, engine, s.id, trigger="manual", requested_by="human:A"
                )
                for s in sprints
            )
        )

    rows = asyncio.run(all_at_once())

    assert [r.status for r in rows] == ["tests_passed"] * 3
    assert nats.most == 1
    attempts = [
        p["attempt"] for s, p in nats.requests if s == "workspace.git.integrate"
    ]
    assert len(set(attempts)) == 3
    assert all(r.test_run["workspace_root"].endswith(a) for r, a in zip(rows, attempts))


def test_a_snapshot_a_command_runs_in_is_never_removed(repo, monkeypatch):
    monkeypatch.setattr(sprint_integration, "SNAPSHOT_GRACE_SECONDS", 0)
    monkeypatch.setattr(sprint_integration, "KEEP_SNAPSHOTS", 1)
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-11", "merge": ["fabrique/task-1"]}
    busy = Path(
        sprint_integration.integrate(str(repo), {**spec, "attempt": "busy"})[
            "test_worktree"
        ]
    )

    def retried_meanwhile():
        # The backend restarted; another replica retries while this suite still runs.
        return Path(
            sprint_integration.integrate(str(repo), {**spec, "attempt": "retry"})[
                "test_worktree"
            ]
        )

    retry = sprint_integration.while_active(busy, retried_meanwhile)

    assert busy.is_dir() and (busy / "a.py").read_text() == "A = 1\n"
    assert retry.is_dir()
    # Once its command ended, the busy snapshot is removed like any other.
    sprint_integration.integrate(str(repo), {**spec, "attempt": "later"})
    assert not busy.exists() and not retry.exists()


def test_a_snapshot_younger_than_the_grace_is_kept(repo, monkeypatch):
    monkeypatch.setattr(sprint_integration, "KEEP_SNAPSHOTS", 1)
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-12", "merge": ["fabrique/task-1"]}
    first = Path(
        sprint_integration.integrate(str(repo), {**spec, "attempt": "a"})[
            "test_worktree"
        ]
    )
    sprint_integration.integrate(str(repo), {**spec, "attempt": "b"})
    # Its suite may not have started yet.
    assert first.is_dir()


def test_the_daemon_marks_each_command_s_folder_in_use():
    seen = []

    def run(folder):
        seen.append(dict(sprint_integration._ACTIVE))
        return "done"

    folder = Path(__file__).parent
    assert sprint_integration.while_active(folder, run, folder) == "done"
    assert seen == [{str(folder.resolve()): 1}]
    assert sprint_integration._ACTIVE == {}
    daemon_source = (
        Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
    ).read_text()
    assert (
        "sprint_integration.while_active, workspace_path, execute_shell_command"
        in daemon_source
    )


def test_the_acceptance_coverage_is_read_once_per_specification(
    sqlite_session, monkeypatch
):
    from models.specification import Specification
    from services import spec_planning

    company = _company(sqlite_session)
    spec = Specification(company_id=company.id, title="Auth", status="approved")
    sqlite_session.add(spec)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", specification_id=spec.id)
    sqlite_session.add(sprint)
    sqlite_session.commit()
    items = [
        BacklogItem(sprint_id=sprint.id, title=f"T{i}", covers=[f"REQ-1.{i}"])
        for i in (1, 2, 3)
    ]
    sqlite_session.add_all(items)
    sqlite_session.commit()
    calls = []

    def coverage(session, spec):
        calls.append(spec.id)
        return {
            "total": 3,
            "verified": 1,
            "criteria": [
                {"id": "REQ-1.1", "verified": True},
                {"id": "REQ-1.2", "verified": False},
                {"id": "REQ-1.3", "verified": False},
            ],
        }

    monkeypatch.setattr(spec_planning, "coverage", coverage)

    lines = spec_planning.acceptance_summaries(sqlite_session, items)

    assert calls == [spec.id]
    assert lines[items[0].id].endswith("this task's: 1 of 1.")
    assert lines[items[1].id].endswith("this task's: 0 of 1.")
    assert '1 of 3 criteria of "Auth"' in lines[items[2].id]
    assert (
        spec_planning.acceptance_summary(sqlite_session, items[0]) == lines[items[0].id]
    )


def test_an_integration_recorded_without_its_tasks_evidence_is_not_published(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    # As a row from before migration 0012 reads.
    legacy = sqlite_session.get(SprintIntegration, row["id"])
    sqlite_session.refresh(legacy)
    legacy.tasks = None
    sqlite_session.add(legacy)
    sqlite_session.commit()

    refused = test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")

    assert refused.status_code == 409
    assert "integrate again" in refused.json()["detail"]


def test_the_acceptance_line_is_the_one_seen_at_integration(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    monkeypatch.setattr(
        integration,
        "acceptance_summaries",
        lambda s, items: {i.id: "**Acceptance:** AT INTEGRATION" for i in items},
    )
    assert test_client.post(f"/v1/sprints/{sprint.id}/integrate").status_code == 201
    # A later run changes the specification's coverage; the integration does not.
    monkeypatch.setattr(
        integration,
        "acceptance_summaries",
        lambda s, items: {i.id: "**Acceptance:** LATER" for i in items},
    )

    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)

    assert "**Acceptance:** AT INTEGRATION" in observed_part(text)
    assert "LATER" not in text
