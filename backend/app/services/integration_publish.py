"""Publishing a sprint's integration branch as one pull or merge request (issue #314).

``services/integration.py`` merged the sprint's done task branches into
``fabrique/sprint-<id>`` and ran the suite on the result. A Manager publishes
that branch the way a task's branch is published (``services/git_publish.py``,
the daemon's ``workspace.git.publish``): pushed to ``origin`` with the
person's own credentials, the request opened as a draft with ``gh`` or
``glab``, or the page where a person opens it.

The description is the record of what ran, as a pull request opened by a
coding agent carries it: what was observed first (the integration's merges,
the suite's result on the integrated branch, and each task's verdict,
checks, criteria verified and files written, as the Verifier observed
them, with the acceptance line as it stood at integration), then each producer's own account, labelled as a claim. The per-task
publication stays available beside it.
"""

from __future__ import annotations

from typing import Any

from models import AgentRun, BacklogItem, Sprint, Verification
from models.integration import PUBLISHABLE, SprintIntegration
from sqlmodel import Session

from services.agent_service import AgentService
from services.git_publish import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    PUBLISH_HOW,
    PublishRefused,
    _cell,
)
from services.review_changes import _workspace_file, run_changes

MAX_FILES_PER_TASK = 30
REFUSALS = {
    "conflict": "The integration stopped at a conflict: integrate again once it is resolved",
    "incomplete": "A done task's branch was missing from the integration: it is not the whole sprint",
    "running": "The integration is still running",
    "pending": "The integration has not run yet: the workspace daemon did not answer",
    "error": "The integration could not run: integrate again",
}


INTEGRATED = ("merged", "already_merged")
STATE_LABELS = {
    "missing": "No branch",
    "refused": "Not a task branch",
    "not_attempted": "Not attempted",
}


def _integration_lines(row: SprintIntegration) -> list[str]:
    tasks = row.tasks or []
    lines = [
        f"**Integration:** {_cell(row.status)}",
        "",
        f"Integrated into `{_cell(row.branch)}` from `{_cell(row.base)}`, in dependency order:",
    ]
    for t in tasks:
        if t.get("state") not in INTEGRATED:
            continue
        how = (
            f"merged as `{_cell(t.get('merge_commit'))[:12]}`"
            if t.get("state") == "merged"
            else "already in"
        )
        lines.append(
            f"- task {_cell(t.get('task_id'))}: `{_cell(t.get('branch'))}`"
            f" at `{_cell(t.get('source_commit'))[:12]}`, {how}"
        )
    for state, label in STATE_LABELS.items():
        ids = [t.get("task_id") for t in tasks if t.get("state") == state]
        if ids:
            lines.append(f"{label}: " + ", ".join(f"task {_cell(i)}" for i in ids))
    left_out = (row.details or {}).get("left_out") or []
    if left_out:
        lines.append(
            "Not done, left out: "
            + ", ".join(f"task {_cell(e.get('task_id'))}" for e in left_out)
        )
    lines += ["", f"Integrated commit `{_cell(row.commit)}`."]
    test_run = row.test_run or {}
    lines.append("")
    if test_run.get("command"):
        outcome = (
            "timed out"
            if test_run.get("timed_out")
            else f"exit code {_cell(test_run.get('exit_code'))}"
        )
        lines.append(
            f"**Test suite on the integrated branch:** `{_cell(test_run['command'])}`, {outcome}"
            f" at `{_cell(row.tested_commit)[:12]}`"
            + (
                " (in a sandbox)"
                if (test_run.get("sandbox") or {}).get("mode") not in (None, "host")
                else ""
            )
        )
        failing = test_run.get("failing_tests") or []
        if failing:
            lines.append("Failing: " + ", ".join(f"`{_cell(t)}`" for t in failing[:30]))
    else:
        lines.append(
            f"**Test suite on the integrated branch:** not run ({_cell(test_run.get('reason') or 'no test run')})"
        )
    return lines


def _file_line(f: dict[str, Any]) -> str:
    return (
        f"- `{_cell(f.get('path'))}` {_cell(f.get('change') or 'changed')}"
        f" (+{f.get('additions', 0)} −{f.get('deletions', 0)})"
    )


def _task_lines(
    session: Session, row: SprintIntegration, task: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """``(observed, claimed)`` for one task, from the run and verification recorded at integration."""
    item = session.get(BacklogItem, task.get("task_id"))
    title = item.title if item is not None else task.get("title")
    header = f"### Task {_cell(task.get('task_id'))}: {_cell(title)}"
    run = session.get(AgentRun, task["run_id"]) if task.get("run_id") else None
    if run is None or run.company_id != row.company_id:
        return [header, "", "No run recorded."], []
    verification = (
        session.get(Verification, task["verification_id"])
        if task.get("verification_id")
        else None
    )
    if verification is not None and verification.agent_run_id != run.id:
        verification = None
    observed = [
        header,
        "",
        (
            f"Run {run.id}, verification {_cell(verification.id if verification else None)}."
            f" **Verdict:** {_cell(verification.verdict if verification else 'not verified')}"
        ),
    ]
    checks = [
        c
        for c in (verification.checks if verification else None) or []
        if isinstance(c, dict)
    ]
    if checks:
        observed.append(
            "Checks: "
            + ", ".join(
                f"{_cell(c.get('name'))} {_cell(c.get('outcome'))}" for c in checks
            )
        )
    # As it stood at integration, never the specification's coverage now.
    acceptance = task.get("acceptance")
    if acceptance:
        observed.append(acceptance)
    # Files the workspace daemon read back, as that verification recorded them.
    writes = ((verification.evidence if verification else None) or {}).get(
        "workspace_writes"
    ) or []
    files = [_workspace_file(w) for w in writes if isinstance(w, dict)]
    observed += [_file_line(f) for f in files[:MAX_FILES_PER_TASK]]
    if len(files) > MAX_FILES_PER_TASK:
        observed.append(f"- … and {len(files) - MAX_FILES_PER_TASK} more")
    claimed = []
    summary = (run.claims or {}).get("summary")
    if summary:
        claimed.append(
            f"- Task {item.id if item else task.get('task_id')} ({_cell(run.producer)}): {_cell(summary)}"
        )
    if not files:
        # A diff the producer sent, never read back from disk: its words.
        changes = run_changes(session, run)
        if changes.get("source") == "artifact":
            paths = [f.get("path") for f in changes.get("files") or []]
            if paths:
                claimed.append(
                    f"- Task {_cell(task.get('task_id'))}, files in the diff it sent: "
                    + ", ".join(f"`{_cell(p)}`" for p in paths[:MAX_FILES_PER_TASK])
                )
    return observed, claimed


def request_body(session: Session, row: SprintIntegration) -> str:
    sprint = session.get(Sprint, row.sprint_id)
    lines = [
        f"Sprint {row.sprint_id} ({_cell(sprint.sprint_name if sprint else '')}) integrated through La Fabrique.",
        "",
        "## Observed",
        "",
        *_integration_lines(row),
    ]
    claims: list[str] = []
    for task in row.tasks or []:
        if task.get("state") not in INTEGRATED:
            continue
        observed, claimed = _task_lines(session, row, task)
        lines += ["", *observed]
        claims += claimed
    if claims:
        lines += ["", "## Claimed by the producers (not observed)", "", *claims]
    trailer = f"\n\nFabrique-Sprint: {row.sprint_id}\nFabrique-Integration: {row.id}\n"
    body = "\n".join(lines)
    return body[: MAX_BODY_CHARS - len(trailer)] + trailer


def publish_spec(session: Session, row: SprintIntegration) -> dict[str, Any]:
    """The ``workspace.git.publish`` request for the sprint's branch, or why not."""
    if row.status not in PUBLISHABLE:
        raise PublishRefused(
            409, REFUSALS.get(row.status, "This integration cannot be published")
        )
    if not row.repository or not row.commit:
        raise PublishRefused(409, "This integration names no commit to publish")
    if row.tasks is None:
        # Recorded before each task's evidence was kept (migration 0012).
        raise PublishRefused(
            409,
            "This integration did not record its tasks' evidence: integrate again",
        )
    sprint = session.get(Sprint, row.sprint_id)
    title = f"Sprint {row.sprint_id}: {sprint.sprint_name if sprint else ''}".strip(
        ": "
    )
    return {
        "workspace_root": row.repository,
        "branch": row.branch,
        # The commit this integration's evidence covers; a branch moved past it is refused.
        "commit": row.commit,
        "base": "" if row.base == "HEAD" else row.base,
        "title": " ".join(title.split())[:MAX_TITLE_CHARS],
        "body": request_body(session, row),
    }


def record_publication(
    session: Session,
    row: SprintIntegration,
    reply: dict[str, Any],
    *,
    published_by: str,
) -> SprintIntegration:
    how = reply.get("how")
    if not reply.get("ok") or how not in PUBLISH_HOW or not reply.get("commit"):
        raise PublishRefused(502, "The workspace daemon sent an unreadable answer")
    url = reply.get("url")
    publication = {
        "provider": reply.get("provider"),
        "branch": str(reply.get("branch") or ""),
        "base": str(reply.get("base") or ""),
        "commit": str(reply["commit"]),
        "url": url if isinstance(url, str) and url.startswith("https://") else None,
        "how": how,
        "updated": bool(reply.get("updated")),
        "published_by": published_by,
    }
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sprint:{row.sprint_id}:integration",
        agent_id=f"human:{published_by}",
        action="sprint_published",
        details={
            "company_id": str(row.company_id),
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
        },
        result=publication,
        decision_signature=None,
        cost_tokens=0,
    )
    row.publication = {**publication, "audit_log_id": audit_log.id}
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
