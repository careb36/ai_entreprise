"""Integrating a sprint (issue #314) through the API.

A Manager integrates, the project's test command runs on the integrated
worktree and one request is published; a conflict, a failing suite or the
tests off are recorded as such and wait for a person; what cannot be
integrated is refused, and another company's member neither reads nor
integrates.

These tests run real git in ``tmp_path``, with a bare repository standing in
for the remote; the shared fakes and rows are in ``sprint_integration_helpers``.
"""

import shlex
import sys
from pathlib import Path

import pytest
from config import settings
from services.release import integration

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import sprint_integration_helpers
import test_git_workflow
from sprint_integration_helpers import (
    _Daemon,
    _sprint,
    _task,
    _two_done_tasks,
    observed_part,
)
from test_git_workflow import _company, _member, git

# The shared fixtures.
published_repo = sprint_integration_helpers.published_repo
suite = sprint_integration_helpers.suite
enforcement = test_git_workflow.enforcement


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
        # The project's sandbox image first (container default, issue #766).
        "workspace.sandbox.image",
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


def test_the_status_reads_the_observations():
    assert integration.status_of({"files": ["a"]}, None) == "conflict"
    assert integration.status_of(None, {"exit_code": 1}) == "tests_failed"
    assert integration.status_of(None, {"timed_out": True}) == "tests_failed"
    assert integration.status_of(None, {"exit_code": 0}) == "tests_passed"
    assert integration.status_of(None, {"command": None}) == "untested"
