"""A sprint's integration on the tasks and in the inbox (issue #314).

Each task's part (merged, skipped on a rerun, or its conflict) is its
``integration`` event by the System; a done task without its branch makes the
integration incomplete; the inbox counts every waiting integration beyond its
limit.

These tests run real git in ``tmp_path``, with a bare repository standing in
for the remote; the shared fakes and rows are in ``sprint_integration_helpers``.
"""

import shlex
import sys
from pathlib import Path

from config import settings
from history_check import check_events
from models import BacklogItem, Sprint
from services.tasks import task_events

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import sprint_integration_helpers
from sprint_integration_helpers import _Daemon, _deliver, _task, _two_done_tasks

# The shared fixtures.
published_repo = sprint_integration_helpers.published_repo
suite = sprint_integration_helpers.suite


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
