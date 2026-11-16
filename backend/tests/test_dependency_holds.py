"""A task waiting on one a person must act on (``services.tasks.dependencies``,
``services.loop.sprint_control``): it is blocked with the reason and released
when that one gets done, a comment anyone appends does not hide the hold and
a send-back is held again, a sprint reconciles its holds before it counts as
settled, and a closed sprint is left alone. The shared rows are in
``dependencies_helpers``.
"""

import worker_stages
from dependencies_helpers import _company, _sprint, _task
from services.tasks import dependencies


def test_a_task_waiting_on_one_left_for_a_person_is_blocked_with_the_reason_and_released_when_it_gets_done(
    sqlite_session,
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(
        sqlite_session,
        sprint,
        "Store",
        status="blocked",
        comments=[
            {
                "author": "Quality gate",
                "text": "Pre-review quality gate failed: too short",
                "created_at": "",
            }
        ],
    )
    endpoint = _task(sqlite_session, sprint, "Endpoint", depends_on=[store.id])
    page = _task(sqlite_session, sprint, "Page", depends_on=[endpoint.id])
    free = _task(sqlite_session, sprint, "Docs")
    retrying = _task(
        sqlite_session,
        sprint,
        "Retrying",
        status="blocked",
        comments=[
            {"author": "System", "text": "Agent error: timeout", "created_at": ""}
        ],
    )
    waits_on_retry = _task(
        sqlite_session, sprint, "After retry", depends_on=[retrying.id]
    )
    assert not dependencies.settle(sqlite_session, 999999)["blocked"]

    result = dependencies.settle_active(sqlite_session.get_bind())
    assert result == {"blocked": [endpoint.id, page.id], "released": []}
    for item in (store, endpoint, page, free, retrying, waits_on_retry):
        sqlite_session.refresh(item)
    assert (endpoint.status, page.status, free.status) == ("blocked", "blocked", "todo")
    assert (
        endpoint.comments[-1]["text"]
        == f'Blocked: waits for task {store.id} ("Store"), which waits for a person.'
    )
    assert (
        page.comments[-1]["text"]
        == f'Blocked: waits for task {endpoint.id} ("Endpoint"), which waits for a person.'
    )
    # The sprint can close around them: nothing the worker does moves them.
    from services.tasks.board import block_reason, item_is_settled

    assert item_is_settled(endpoint) and item_is_settled(page)
    assert block_reason(endpoint.comments).startswith("Blocked: waits for task")
    # One waiting on a task the worker still retries is not blocked.
    assert waits_on_retry.status == "todo"

    # The person sends the dependency back to work, and it gets done.
    store.status = "done"
    sqlite_session.add(store)
    sqlite_session.commit()
    assert dependencies.settle_active(sqlite_session.get_bind()) == {
        "blocked": [],
        "released": [endpoint.id, page.id],
    }
    sqlite_session.refresh(endpoint)
    sqlite_session.refresh(page)
    assert (
        endpoint.status == "todo"
        and endpoint.comments[-1]["text"] == "Released: task it waited for got done."
    )
    # The one behind it is back in todo too: nothing it waits for is stuck
    # any more, and the worker queues it once the endpoint is done.
    assert page.status == "todo"
    assert page.comments[-1]["text"] == (
        "Released: task it waited for are back in work; "
        f'Waits for task {endpoint.id} ("Endpoint") to be done first.'
    )
    assert not dependencies.is_ready(sqlite_session, page)
    endpoint.status = "done"
    sqlite_session.add(endpoint)
    sqlite_session.commit()
    assert dependencies.settle_active(sqlite_session.get_bind()) == {
        "blocked": [],
        "released": [],
    }
    assert dependencies.is_ready(sqlite_session, page)


def test_a_comment_anyone_appends_does_not_hide_the_hold_and_a_send_back_is_held_again(
    sqlite_session,
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    gate = [
        {
            "author": "Quality gate",
            "text": "Pre-review quality gate failed",
            "created_at": "",
        }
    ]
    root = _task(sqlite_session, sprint, "Root", status="blocked", comments=gate)
    middle = _task(sqlite_session, sprint, "Middle", depends_on=[root.id])
    leaf = _task(sqlite_session, sprint, "Leaf", depends_on=[middle.id])
    assert dependencies.settle_active(sqlite_session.get_bind())["blocked"] == [
        middle.id,
        leaf.id,
    ]
    # A person comments on the held task: the hold is still the System's last word.
    sqlite_session.refresh(middle)
    middle.comments = [
        *middle.comments,
        {"author": "user", "text": "Looking into it", "created_at": ""},
    ]
    sqlite_session.add(middle)
    # A person sends the leaf back to work while the root is still stuck.
    sqlite_session.refresh(leaf)
    leaf.status = "todo"
    sqlite_session.add(leaf)
    sqlite_session.commit()
    assert dependencies.settle_active(sqlite_session.get_bind()) == {
        "blocked": [leaf.id],
        "released": [],
    }
    # The root gets done: the whole chain is released, the comment notwithstanding.
    root.status = "done"
    sqlite_session.add(root)
    sqlite_session.commit()
    assert dependencies.settle_active(sqlite_session.get_bind())["released"] == [
        middle.id,
        leaf.id,
    ]
    sqlite_session.refresh(middle)
    assert middle.status == "todo"


def test_a_sprint_reconciles_its_holds_before_it_counts_as_settled(
    sqlite_session, monkeypatch
):
    from services.loop.worker import close as worker_close

    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    gate = [
        {
            "author": "Quality gate",
            "text": "Pre-review quality gate failed",
            "created_at": "",
        }
    ]
    root = _task(sqlite_session, sprint, "Root", status="blocked", comments=gate)
    held = _task(sqlite_session, sprint, "Held", depends_on=[root.id])
    assert dependencies.settle_active(sqlite_session.get_bind())["blocked"] == [held.id]
    assert dependencies.sprint_settled(sqlite_session, sprint.id)
    # The root is accepted (done) and the completion check runs before any
    # settlement pass: the hold is released first, so the sprint stays open.
    root.status = "done"
    sqlite_session.add(root)
    sqlite_session.commit()
    assert not dependencies.sprint_settled(sqlite_session, sprint.id)
    sqlite_session.refresh(held)
    assert held.status == "todo"
    assert not dependencies.sprint_settled(sqlite_session, 999999)
    assert (
        worker_close._complete_sprint_and_plan_follow_up(sprint.id)["reason"]
        == "not_all_done"
    )


def test_a_closed_sprint_is_left_alone(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company, status="completed")
    store = _task(
        sqlite_session,
        sprint,
        "Store",
        status="blocked",
        comments=[
            {
                "author": "Quality gate",
                "text": "Pre-review quality gate failed",
                "created_at": "",
            }
        ],
    )
    endpoint = _task(sqlite_session, sprint, "Endpoint", depends_on=[store.id])
    assert dependencies.settle_active(sqlite_session.get_bind()) == {
        "blocked": [],
        "released": [],
    }
    sqlite_session.refresh(endpoint)
    assert endpoint.status == "todo"
