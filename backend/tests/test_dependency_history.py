"""The independent history check on dependencies (``history_check``): a task
worked before its dependency is refused, and a task's close is read from the
right comment (a person's note on a task already done is not its close, a
held task closes when a person accepts it, a reworked or overridden task at
its last close).
"""

import history_check


def test_the_history_check_refuses_a_task_worked_before_its_dependency():
    task = {
        "id": 2,
        "status": "done",
        "comments": [{"author": "user", "text": "accepted", "created_at": ""}],
        "depends_on": [1, 3],
    }
    # Timing: the dependency closed after this task's first deliverable.
    delivered = {
        "id": 2,
        "status": "in_review",
        "depends_on": [1],
        "comments": [
            {
                "author": "Developer",
                "text": "Implemented it.",
                "created_at": "2026-11-07T10:00:00",
            },
            {
                "author": "QA",
                "text": "APPROVED: fine",
                "created_at": "2026-11-07T10:30:00",
            },
        ],
    }
    closed_late = {
        "status": "done",
        "comments": [
            {
                "author": "Developer",
                "text": "Did it.",
                "created_at": "2026-11-07T09:00:00",
            },
            {
                "author": "QA",
                "text": "APPROVED: ok",
                "created_at": "2026-11-07T10:10:00+00:00",
            },
        ],
    }
    closed_early = {
        **closed_late,
        "comments": [
            {**closed_late["comments"][0]},
            {**closed_late["comments"][1], "created_at": "2026-11-07T09:50:00Z"},
        ],
    }
    assert history_check.check_task(delivered, [], [], {1: closed_late}) == [
        (
            "worked_before_dependency: task 1 got done at 2026-11-07T10:10:00, "
            "after this task delivered at 2026-11-07T10:00:00"
        )
    ]
    assert history_check.check_task(delivered, [], [], {1: closed_early}) == []
    # A task sent back to todo after delivering early is still caught.
    assert (
        history_check.check_task(
            {**delivered, "status": "todo"}, [], [], {1: closed_late}
        )
        != []
    )
    assert history_check.check_task(
        {**delivered, "status": "todo"}, [], [], {1: "in_progress"}
    ) == ["worked_before_dependency: task 1 is in_progress, not done"]
    assert history_check.check_task(task, [], [], {1: "done", 3: "blocked"}) == [
        "worked_before_dependency: task 3 is blocked, not done"
    ]
    assert history_check.check_task(
        {**task, "status": "in_progress"}, [], [], {1: "todo", 3: "done"}
    ) == ["worked_before_dependency: task 1 is todo, not done"]
    assert history_check.check_task(task, [], [], {1: "done", 3: "done"}) == []
    assert history_check.check_task({**task, "status": "todo"}, [], [], {}) == []
    assert history_check.check_task(task, [], [], None) == [
        "worked_before_dependency: task 1 is missing, not done",
        "worked_before_dependency: task 3 is missing, not done",
    ]


def _c(author: str, text: str, at: str) -> dict:
    return {"author": author, "text": text, "created_at": at}


def test_a_person_s_note_on_a_task_already_done_is_not_its_close():
    """Run 43752094404: QA's approval closed the greeting at 19:39:46, the
    counter delivered at 19:39:50, and a person's note on the greeting at
    19:40:17 was read as its close, a false worked_before_dependency."""
    counter = {
        "id": 19,
        "status": "in_review",
        "depends_on": [18],
        "comments": [_c("Developer", "Counter done.", "2026-11-15T19:39:50")],
    }
    greeting = {
        "status": "done",
        "comments": [
            _c("Developer", "Greeting done.", "2026-11-15T19:39:17"),
            _c("QA", "APPROVED: fine", "2026-11-15T19:39:46"),
            _c("user", "accepted", "2026-11-15T19:40:17"),
        ],
    }
    assert history_check.check_task(counter, [], [], {18: greeting}) == []


def test_a_held_task_closes_when_a_person_accepts_it_not_when_qa_approves():
    counter = {
        "id": 19,
        "status": "in_review",
        "depends_on": [18],
        "comments": [_c("Developer", "Counter done.", "2026-11-15T19:39:50")],
    }
    held = {
        "status": "done",
        "comments": [
            _c("Developer", "Greeting done.", "2026-11-15T19:39:17"),
            _c("QA", "APPROVED: fine", "2026-11-15T19:39:46"),
            _c(
                "system",
                "High-risk delivery approved by QA, waiting for a person to accept it.",
                "2026-11-15T19:39:47",
            ),
            _c("user", "accepted", "2026-11-15T19:40:17"),
            _c("user", "a later remark", "2026-11-15T19:45:00"),
        ],
    }
    assert history_check.check_task(counter, [], [], {18: held}) == [
        (
            "worked_before_dependency: task 18 got done at 2026-11-15T19:40:17, "
            "after this task delivered at 2026-11-15T19:39:50"
        )
    ]


def test_reworked_or_overridden_tasks_close_at_their_last_close():
    counter = {
        "id": 19,
        "status": "in_review",
        "depends_on": [18],
        "comments": [_c("Developer", "Counter done.", "2026-11-15T10:30:00")],
    }
    reworked = {
        "status": "done",
        "comments": [
            _c("Developer", "First try.", "2026-11-15T10:00:00"),
            _c("QA", "APPROVED: ok", "2026-11-15T10:10:00"),
            _c("Developer", "Second try.", "2026-11-15T10:40:00"),
            _c("QA", "APPROVED: ok", "2026-11-15T10:50:00"),
        ],
    }
    overridden = {
        "status": "done",
        "comments": [
            _c("Developer", "Done.", "2026-11-15T10:00:00"),
            _c(
                "manager agent",
                "Accepted by the Manager agent (rule x).",
                "2026-11-15T10:10:00",
            ),
            _c(
                "user",
                "Override: ana took back the Manager agent's acceptance (decision #3); "
                "the delivery waits for a person again.",
                "2026-11-15T10:20:00",
            ),
            _c("user", "accepted", "2026-11-15T10:45:00"),
        ],
    }
    for dep, at in ((reworked, "10:50:00"), (overridden, "10:45:00")):
        assert history_check.check_task(counter, [], [], {18: dep}) == [
            (
                f"worked_before_dependency: task 18 got done at 2026-11-15T{at}, "
                "after this task delivered at 2026-11-15T10:30:00"
            )
        ]
