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
import shlex
import sys
from pathlib import Path

import pytest
from config import settings
from models import AgentRun, BacklogItem, ProjectDiscovery, Sprint, Verification
from models.integration import SprintIntegration
from services import integration
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import sprint_integration
import test_git_publish
import test_git_workflow
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
    assert tested_in.endswith("@" + body["commit"][:12])
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
        integration.schedule_after_close(nats, engine, off.id)
        integration.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration._RUNNING))

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
        integration.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration._RUNNING))
        return await integration.retry_pending(nats, engine)

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
    assert asyncio.run(integration.retry_pending(nats, engine)) == 1
    with Session(engine) as session:
        done = integration.latest(session, sprint.id)
    assert (done.status, done.trigger) == ("tests_passed", "sprint_closed")
    assert asyncio.run(integration.retry_pending(nats, engine)) == 0
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
        integration.schedule_after_close(_Refusing(), engine, sprint.id)
        await asyncio.gather(*list(integration._RUNNING))

    asyncio.run(close())

    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [(i["status"], i["reason"]) for i in inbox["integrations"]] == [
        ("error", "fatal: bad base")
    ]
    # An error is not retried on its own: a person integrates again.
    assert asyncio.run(integration.retry_pending(_Refusing(), engine)) == 0
