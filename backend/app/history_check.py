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


def _is_deliverable(comment: dict[str, Any]) -> bool:
    text = _text(comment)
    return (
        bool(text)
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
    """When the task was closed: its last approval or person's note."""
    closing = [c for c in comments if _is_approval(c) or _author(c) == _PERSON]
    return _when(closing[-1]) if closing else None


def check_task(
    task: dict[str, Any],
    runs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    others: dict[int, Any] | None = None,
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
    ``status`` and ``comments`` (a bare status string is accepted).
    """
    comments = [c for c in task.get("comments") or [] if isinstance(c, dict)]
    status = task.get("status")
    violations: list[str] = []

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
        agent_accepts = [
            i
            for i, c in enumerate(comments)
            if _author(c) == _MANAGER_AGENT and _text(c).startswith(_AGENT_ACCEPTED)
        ]
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
    from models import AgentMessage, AgentRun, BacklogItem, Verification
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
            ):
                found.append(f"task {item.id} ({item.title}): {violation}")
    return found


if __name__ == "__main__":
    problems = check_database()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} illegal history(ies)")
    sys.exit(1 if problems else 0)
