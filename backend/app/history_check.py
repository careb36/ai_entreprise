"""
An independent checker of task histories.

It reads what the database holds about each task (its status, its comment
thread, the task worker's runs of it and the company's messages about it)
and reports histories the loop must never produce:

- ``done_without_approval``: done with neither an explicit ``APPROVED:`` from
  a reviewer nor a note from a person;
- ``approved_without_delivery``: an ``APPROVED:`` with no deliverable before it;
- ``done_without_verified_delivery``: closed by an approval while the task
  worker's latest run of it was refuted, unverifiable or never recorded;
- ``high_risk_done_without_person``: a high-risk hold closed with no note
  from a person after it (a hold only for autonomy level 1 may also close
  with the Manager agent's acceptance under the company's policy);
- ``carried_twice``: one task carried into another sprint more than once;
- ``worked_before_dependency``: a task that delivered before every task it
  depends on was done: the dependency is not done, or it closed (its last
  approval or person's note) after this task's first deliverable;
- ``decision_without_escalation``: a ScrumMaster decision that names an
  escalation the task never had;
- ``criteria_verified_without_run``: a run whose Verifier marked a criterion's
  acceptance test as passed with no observed test run (no exit status from the
  daemon) in its evidence.

The task's events (``task_events``, issue #324) are a second record, read
beside the thread and never instead of it:

- ``comment_without_event``: a comment names an event the task does not have;
- ``comment_differs_from_event``: a comment says something else than the
  event it renders (its author or its text);
- and, once the events render the whole thread (every comment names one and
  none is a ``legacy`` marker): ``done_without_approval_event`` (done with no
  ``reviewed`` approval and no ``approved_by_person`` event) and
  ``status_differs_from_events`` (the status the latest transition left is
  not the task's).

It deliberately imports none of the production readers (``services/board.py``,
``services/carry_over.py``, ``services/scrum_master.py``) and restates the
markers it looks for, so a bug shared with them cannot hide here. It runs in
the tests (``tests/test_history_check.py``) and at the end of
``scripts/e2e/run.sh`` against the run's database::

    PYTHONPATH=.:app python app/history_check.py

which prints every violation and exits 1 when there is one.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

# Restated on purpose; see the module docstring.
_GATE_AUTHORS = {"quality gate", "verifier"}
_PERSON = "user"
_CARRIED_TO = "Carried over to sprint"
_RISK_HOLD = "High-risk delivery approved by QA, waiting for a person to accept it"
_ANSWERS = "Answers escalation #"
_DECISION_FIRST_LINES = ("Triage decision:",)
_TASK_WORKER = "le-fabrique-task-worker"
_HUMAN_LED = "autonomy level 1: every approved task waits for a person"
_MANAGER_AGENT = "manager agent"
_AGENT_ACCEPTED = "Accepted by the Manager agent"


def _author(comment: dict[str, Any]) -> str:
    return str(comment.get("author") or "").strip().lower()


def _text(comment: dict[str, Any]) -> str:
    return str(comment.get("text") or "").strip()


def _is_approval(comment: dict[str, Any]) -> bool:
    return _author(comment) not in _GATE_AUTHORS | {"system", _PERSON} and _text(
        comment
    ).upper().startswith("APPROVED:")


def _is_agent_acceptance(comment: dict[str, Any]) -> bool:
    return _author(comment) == _MANAGER_AGENT and _text(comment).startswith(
        _AGENT_ACCEPTED
    )


def _is_deliverable(comment: dict[str, Any]) -> bool:
    text = _text(comment)
    return (
        bool(text)
        and not _is_agent_acceptance(comment)
        and _author(comment) not in _GATE_AUTHORS | {"system", _PERSON}
        and not text.upper().startswith(("APPROVED:", "NEEDS WORK:"))
        and not text.startswith("ARCHITECT_CONSULTED:")
    )


def _answered(body: str) -> int | None:
    for line in (body or "").split("\n"):
        line = line.strip()
        if line.startswith(_ANSWERS) and line[len(_ANSWERS) :].isdigit():
            return int(line[len(_ANSWERS) :])
    return None


def _when(comment: dict[str, Any]) -> datetime | None:
    raw = str(comment.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (
        parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)
    )


def _started_at(comments: list[dict[str, Any]]) -> datetime | None:
    """When the task's first deliverable was posted."""
    return next((_when(c) for c in comments if _is_deliverable(c)), None)


def _closed_at(comments: list[dict[str, Any]]) -> datetime | None:
    """When the task was closed: its last approval, person's note or the
    Manager agent's acceptance (which releases an autonomy-1 hold)."""
    closing = [
        c
        for c in comments
        if _is_approval(c) or _author(c) == _PERSON or _is_agent_acceptance(c)
    ]
    return _when(closing[-1]) if closing else None


def check_events(
    comments: list[dict[str, Any]], status: Any, events: list[dict[str, Any]]
) -> list[str]:
    """Where the thread and the events disagree.

    Nothing for a task with neither events nor comments naming one (a thread
    from before events existed). Every comment that names an event must
    render it, and every event that renders text (``text`` in its payload;
    an escalation or a bare block carries none) must keep its comment, in
    the same order, so a comment deleted or moved is found.
    """
    if not events and not any("event_id" in c for c in comments):
        return []
    violations: list[str] = []
    by_id = {e.get("id"): e for e in events}
    for index, comment in enumerate(comments):
        if "event_id" not in comment:
            continue
        event = by_id.get(comment.get("event_id"))
        if event is None:
            violations.append(
                f"comment_without_event: comment {index} names event "
                f"{comment.get('event_id')}, which the task does not have"
            )
            continue
        payload = event.get("payload") or {}
        said = (_author(comment), _text(comment))
        rendered = (
            str(payload.get("author") or "").strip().lower(),
            str(payload.get("text") or "").strip(),
        )
        if said != rendered:
            violations.append(
                f"comment_differs_from_event: comment {index} and event "
                f"{event.get('id')} ({event.get('kind')}) say different things"
            )
    rendering = [e.get("id") for e in events if "text" in (e.get("payload") or {})]
    named = [c.get("event_id") for c in comments if c.get("event_id") in by_id]
    kept = set(named)
    for event_id in rendering:
        if event_id not in kept:
            event = by_id[event_id]
            violations.append(
                f"event_without_comment: event {event_id} ({event.get('kind')}) "
                "renders a comment the thread no longer has"
            )
    shown = set(rendering)
    if [i for i in named if i in shown] != [i for i in rendering if i in kept]:
        violations.append(
            "comments_out_of_order: the thread's comments are not in the order "
            "of the events they render"
        )
    whole = (
        bool(comments)
        and all(c.get("event_id") in by_id for c in comments)
        and not any(e.get("kind") == "legacy" for e in events)
    )
    if not whole:
        return violations
    if status == "done" and not any(
        e.get("kind") == "approved_by_person"
        or (
            e.get("kind") == "reviewed"
            and (e.get("payload") or {}).get("verdict") == "approved"
        )
        for e in events
    ):
        violations.append("done_without_approval_event")
    left = next(
        (e.get("task_status") for e in reversed(events) if e.get("task_status")),
        "todo",
    )
    if status and left != status:
        violations.append(
            f"status_differs_from_events: the task is {status}, its events left it {left}"
        )
    return violations


def check_task(
    task: dict[str, Any],
    runs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    others: dict[int, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """The violations in one task's history, as ``"<rule>: <detail>"`` lines.

    *task* has ``id``, ``status``, ``comments`` and ``depends_on``; *runs*
    are the task worker's runs of it with ``version``, ``status``,
    ``verified_criteria`` (the criteria its latest verification marked as
    passed) and ``observed_test_run`` (whether that verification's evidence
    holds an exit status from the daemon);
    ``reads_without_hash`` (the files a workspace read recorded with no
    sha256); *messages* are the company's messages about it with ``id``, ``kind``
    and ``body``; *others* maps the ids of the tasks it depends on to their
    ``status`` and ``comments`` (a bare status string is accepted); *events*
    are its events in order, with ``id``, ``kind``, ``task_status`` and
    ``payload``.
    """
    comments = [c for c in task.get("comments") or [] if isinstance(c, dict)]
    status = task.get("status")
    violations: list[str] = []

    violations.extend(check_events(comments, status, events or []))
    approvals = [i for i, c in enumerate(comments) if _is_approval(c)]
    person_notes = [i for i, c in enumerate(comments) if _author(c) == _PERSON]

    for index in approvals:
        if not any(_is_deliverable(c) for c in comments[:index]):
            violations.append(f"approved_without_delivery: comment {index}")

    if status == "done":
        if not approvals and not person_notes:
            violations.append("done_without_approval")
        closed_by_person = bool(person_notes) and (
            not approvals or person_notes[-1] > approvals[-1]
        )
        if approvals and not closed_by_person:
            latest = max(runs, key=lambda r: r.get("version") or 0) if runs else None
            if latest is None:
                violations.append("done_without_verified_delivery: no run recorded")
            elif latest.get("status") != "verified":
                violations.append(
                    "done_without_verified_delivery: latest run "
                    f"v{latest.get('version')} is {latest.get('status')}"
                )
        holds = [
            i
            for i, c in enumerate(comments)
            if _author(c) == "system" and _text(c).startswith(_RISK_HOLD)
        ]
        agent_accepts = [i for i, c in enumerate(comments) if _is_agent_acceptance(c)]
        human_led = bool(holds) and _HUMAN_LED in _text(comments[holds[-1]])
        if (
            holds
            and not any(i > holds[-1] for i in person_notes)
            and not (human_led and any(i > holds[-1] for i in agent_accepts))
        ):
            violations.append("high_risk_done_without_person")

    started = _started_at(comments)
    worked = started is not None or status in ("done", "in_progress", "in_review")
    for dep in task.get("depends_on") or [] if worked else []:
        other = (others or {}).get(dep)
        if isinstance(other, str):
            other = {"status": other, "comments": []}
        dep_status = (other or {}).get("status", "missing")
        if dep_status != "done":
            violations.append(
                f"worked_before_dependency: task {dep} is {dep_status}, not done"
            )
            continue
        closed = _closed_at(
            [c for c in (other or {}).get("comments") or [] if isinstance(c, dict)]
        )
        if started is not None and closed is not None and started < closed:
            violations.append(
                f"worked_before_dependency: task {dep} got done at "
                f"{closed.isoformat()}, after this task delivered at {started.isoformat()}"
            )

    for run in runs:
        if run.get("verified_criteria") and not run.get("observed_test_run"):
            violations.append(
                f"criteria_verified_without_run: run v{run.get('version')} marks "
                f"{', '.join(str(c) for c in run['verified_criteria'])} as passed with no test run observed"
            )
        # A file the Developer read is evidence only with the hash the daemon
        # reported; a read without one is a claim dressed as an observation.
        if run.get("reads_without_hash"):
            violations.append(
                f"read_without_hash: run v{run.get('version')} records a read of "
                f"{', '.join(str(p) for p in run['reads_without_hash'])} with no sha256"
            )

    carried = sum(1 for c in comments if _text(c).startswith(_CARRIED_TO))
    if carried > 1:
        violations.append(f"carried_twice: carried {carried} times")

    escalations = {m.get("id") for m in messages if m.get("kind") == "escalation"}
    for message in messages:
        if message.get("kind") != "decision":
            continue
        body = str(message.get("body") or "")
        if not body.startswith(_DECISION_FIRST_LINES):
            continue
        answers = _answered(body)
        if answers is not None and answers not in escalations:
            violations.append(
                f"decision_without_escalation: message {message.get('id')} "
                f"answers #{answers}, which this task never had"
            )
        elif answers is None and not escalations:
            violations.append(
                f"decision_without_escalation: message {message.get('id')} "
                "on a task never escalated"
            )
    return violations


def _run_facts(verification: Any) -> dict[str, Any]:
    """What a verification row says: the criteria its ``criteria_tests`` check
    marked as passed, and whether its evidence holds an observed test run."""
    if verification is None:
        return {
            "verified_criteria": [],
            "observed_test_run": False,
            "reads_without_hash": [],
        }
    passed = [
        str(cid)
        for check in verification.checks or []
        if isinstance(check, dict) and check.get("name") == "criteria_tests"
        for cid, result in (check.get("results") or {}).items()
        if result == "pass"
    ]
    exit_code = ((verification.evidence or {}).get("test_run") or {}).get("exit_code")
    # The daemon reports an integer; one that travelled as text still counts.
    observed = (isinstance(exit_code, int) and not isinstance(exit_code, bool)) or (
        isinstance(exit_code, str) and exit_code.strip().lstrip("-").isdigit()
    )
    reads_without_hash = [
        str(call.get("path") or "?")
        for call in (verification.evidence or {}).get("tool_calls") or []
        if isinstance(call, dict)
        and call.get("server") == "workspace"
        and not call.get("is_error")
        and (
            (call.get("tool") == "read" and not call.get("sha256"))
            # An applied edit carries the file's hash before and after.
            or (
                call.get("tool") == "edit"
                and not (call.get("sha256_before") and call.get("sha256_after"))
            )
        )
    ]
    return {
        "verified_criteria": passed,
        "observed_test_run": observed,
        "reads_without_hash": reads_without_hash,
    }


def check_database() -> list[str]:
    """Every task in the database, checked; violations prefixed with the task id."""
    from database import engine
    from models import AgentMessage, AgentRun, BacklogItem, TaskEvent, Verification
    from sqlmodel import Session, select

    found: list[str] = []
    with Session(engine) as session:
        runs_by_ref: dict[str, list[dict[str, Any]]] = {}
        for run in session.exec(
            select(AgentRun).where(AgentRun.producer == _TASK_WORKER)
        ).all():
            verification = session.exec(
                select(Verification)
                .where(Verification.agent_run_id == run.id)
                .order_by(Verification.id.desc())
            ).first()
            runs_by_ref.setdefault(run.external_ref, []).append(
                {
                    "version": run.version,
                    "status": run.status,
                    **_run_facts(verification),
                }
            )
        messages_by_task: dict[int, list[dict[str, Any]]] = {}
        for message in session.exec(select(AgentMessage)).all():
            if message.backlog_item_id is not None:
                messages_by_task.setdefault(message.backlog_item_id, []).append(
                    {"id": message.id, "kind": message.kind, "body": message.body}
                )
        events_by_task: dict[int, list[dict[str, Any]]] = {}
        for event in session.exec(
            select(TaskEvent)
            .where(TaskEvent.task_id.is_not(None))  # type: ignore[union-attr]
            .order_by(TaskEvent.task_id, TaskEvent.id)
        ).all():
            events_by_task.setdefault(event.task_id, []).append(
                {
                    "id": event.id,
                    "kind": event.event_type,
                    "task_status": event.task_status,
                    "payload": event.payload or {},
                }
            )
        items = session.exec(select(BacklogItem)).all()
        others = {
            item.id: {"status": item.status, "comments": item.comments or []}
            for item in items
        }
        for item in items:
            task = {
                "id": item.id,
                "status": item.status,
                "comments": item.comments,
                "depends_on": item.depends_on
                if isinstance(item.depends_on, list)
                else [],
            }
            for violation in check_task(
                task,
                runs_by_ref.get(f"task-{item.id}", []),
                messages_by_task.get(item.id, []),
                others,
                events_by_task.get(item.id, []),
            ):
                found.append(f"task {item.id} ({item.title}): {violation}")
    return found


if __name__ == "__main__":
    problems = check_database()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} illegal history(ies)")
    sys.exit(1 if problems else 0)
