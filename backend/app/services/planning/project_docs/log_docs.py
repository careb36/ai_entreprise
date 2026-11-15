"""The logs of a pack: requirements traceability, issue log, change log.

Built from the snapshot (``collect``) alone; no model writes any of them. The
structure follows the PMBOK Guide, 8th edition (PMI, 2025): traceability in
Scope (Monitoring and Controlling), the issue log in Governance (Executing),
the change log in Governance (Monitoring and Controlling); the wording is our
own.

The traceability matrix links each requirement to the tasks that cover it, the
Verifier's verdict on their latest delivery and the commit a sprint
integration merged them at: every link is an observation La Fabrique made, not
a declaration.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.planning.project_docs.layout import PMBOK, build, missing_line
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import (
    covering,
    requirement_ref,
    spec_ref,
    task_ref,
    tasks_of,
)

REASON_LIMIT = 240


def _cell(value: Any, limit: int = 120) -> str:
    return md_inline(value, limit) if value not in (None, "") else "-"


def _date(value: Any) -> str:
    return md_inline(str(value)[:10]) if value else "-"


def event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "event",
        event.get("id"),
        sha256=event.get("chain_hash"),
        label=f"{event.get('kind')} by {event.get('actor')}",
    )


def verification_ref(item: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "verification",
        item.get("verification_id"),
        sha256=item.get("artifact_sha256"),
        label=f"run {item.get('run_id')}, {item.get('verdict')}",
    )


def latest_verification(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """The newest verification per task."""
    out: dict[Any, dict[str, Any]] = {}
    for item in snapshot.get("verifications") or []:
        out[item.get("task_id")] = item
    return out


def integrated_at(snapshot: dict[str, Any]) -> dict[Any, tuple[dict, str | None]]:
    """The newest integration that merged each task, with the commit it reported."""
    out: dict[Any, tuple[dict, str | None]] = {}
    for integration in snapshot.get("integrations") or []:
        for task in integration.get("tasks") or []:
            out[task.get("task_id")] = (integration, task.get("commit"))
    return out


TRACE_INPUTS = ("specification", "sprints", "verifications", "integrations")


def traceability(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Requirement, then task, then verified delivery, then integration commit."""
    title = "Requirements traceability matrix"
    spec = snapshot.get("specification")
    src, missing = Sources(), []
    if not spec:
        body = [missing_line("the project has no specification in La Fabrique.")]
        return build(
            "traceability", title, PMBOK, body, src, snapshot, TRACE_INPUTS, ["all"]
        )
    verified, merged = latest_verification(snapshot), integrated_at(snapshot)
    body = [
        (
            f"From specification {md_id(spec['id'])} version {md_id(spec.get('version'))} "
            f"{src.cite(spec_ref(spec))}. Each link is an observation: the task names "
            "the criterion, the Verifier read the delivery, the integration reported "
            "the commit."
        ),
        "",
        "| Requirement | Criteria covered | Task | Task status | Latest verification | Integrated at | Sources |",
        "|---|---|---|---|---|---|---|",
    ]
    for req in spec.get("requirements") or []:
        req_id = str(req.get("id"))
        rmark = src.cite(requirement_ref(spec, req))
        tasks = covering(snapshot, req_id)
        if not tasks:
            body.append(f"| {md_id(req_id)} | - | none | - | - | - | {rmark} |")
            missing.append(f"tasks for {req_id}")
            continue
        for sprint, task in tasks:
            marks = [rmark, src.cite(task_ref(task, sprint.get("id")))]
            criteria = [
                md_id(c)
                for c in task.get("covers") or []
                if str(c) == req_id or str(c).startswith(f"{req_id}.")
            ]
            check = verified.get(task.get("id"))
            verdict = "none recorded"
            if check:
                verdict = md_inline(check.get("verdict"))
                marks.append(src.cite(verification_ref(check)))
            commit = "none recorded"
            if task.get("id") in merged:
                integration, sha = merged[task.get("id")]
                commit = md_inline(str(sha)[:12]) if sha else "no commit reported"
                marks.append(
                    src.cite(
                        ref(
                            "integration",
                            integration.get("id"),
                            sha256=sha,
                            label=f"sprint {integration.get('sprint_id')}",
                        )
                    )
                )
            body.append(
                f"| {md_id(req_id)} | {', '.join(criteria) or '-'} | T-{md_id(task.get('id'))} "
                f"{_cell(task.get('title'), 60)} | {_cell(task.get('status'))} | {verdict} | "
                f"{commit} | {' '.join(marks)} |"
            )
    if not spec.get("requirements"):
        body.append(missing_line("the specification lists no requirement."))
        missing.append("requirements")
    return build(
        "traceability", title, PMBOK, body, src, snapshot, TRACE_INPUTS, missing
    )


ISSUE_INPUTS = ("sprints", "events", "verifications")


def issues(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The issue log: what went wrong, who raised it, how it stands now."""
    title = "Issue log"
    src = Sources()
    state_of = {t.get("id"): str(t.get("status")) for _, t in tasks_of(snapshot)}
    events = snapshot.get("events") or []
    rows: list[tuple[str, str, str, str, str]] = []
    for event in events:
        kind, task_id = event.get("kind"), event.get("task_id")
        reason = md_inline(event.get("reason") or "no reason recorded", REASON_LIMIT)
        status = state_of.get(task_id, "unknown")
        if kind == "blocked":
            resolution = {"blocked": "open", "done": "resolved: task done"}.get(
                status, f"back in work: {status}"
            )
            text = f"Task T-{md_id(task_id)} was blocked. Reason as recorded: {reason}"
        elif kind == "escalated":
            decided = any(
                e.get("kind") == "decided"
                and e.get("task_id") == task_id
                and (e.get("id") or 0) > (event.get("id") or 0)
                for e in events
            )
            resolution = "decided by the ScrumMaster" if decided else "open"
            text = f"Task T-{md_id(task_id)} was escalated to the ScrumMaster. Reason as recorded: {reason}"
        else:
            continue
        rows.append(
            (
                text,
                md_inline(event.get("actor")),
                _date(event.get("created_at")),
                resolution,
                src.cite(event_ref(event)),
            )
        )
    for item in snapshot.get("verifications") or []:
        if item.get("verdict") not in (VERDICT_REFUTED, VERDICT_UNVERIFIABLE):
            continue
        status = state_of.get(item.get("task_id"), "unknown")
        rows.append(
            (
                f"The Verifier found the delivery of task T-{md_id(item.get('task_id'))} {md_inline(item.get('verdict'))}",
                "Verifier",
                _date(item.get("observed_at")),
                "resolved: task done" if status == "done" else f"task {status}",
                src.cite(verification_ref(item)),
            )
        )
    body = [
        (
            "Issues are what the records show went wrong: a blocked task, an "
            "escalation, a delivery the Verifier refuted or could not verify. A "
            "reason quoted from an agent is what it wrote, not a finding."
        ),
        "",
    ]
    missing = []
    if rows:
        body += [
            "| ID | Issue | Raised by | Raised | Status | Source |",
            "|---|---|---|---|---|---|",
        ]
        body += [
            f"| I-{n} | {t} | {a} | {d} | {_cell(r)} | {m} |"
            for n, (t, a, d, r, m) in enumerate(rows, 1)
        ]
    else:
        body.append(
            missing_line("no block, escalation or failed verification is recorded.")
        )
        missing.append("issues")
    return build("issues", title, PMBOK, body, src, snapshot, ISSUE_INPUTS, missing)


CHANGE_INPUTS = ("history", "events")


def _requirement_delta(entry: dict, previous: dict | None) -> str:
    if previous is None or "requirement_ids" not in entry:
        return ""
    now, before = (
        set(entry.get("requirement_ids") or []),
        set(previous.get("requirement_ids") or []),
    )
    added, removed = sorted(now - before), sorted(before - now)
    parts = []
    if added:
        parts.append("requirements added: " + ", ".join(md_id(r) for r in added))
    if removed:
        parts.append("requirements removed: " + ", ".join(md_id(r) for r in removed))
    return "; ".join(parts) or "same requirement ids"


def changes(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The change log: each revision of the specification and the architecture,
    and each task carried between sprints."""
    title = "Change log"
    src = Sources()
    history = snapshot.get("history") or []
    by_id = {(h.get("kind"), h.get("id")): h for h in history}
    rows: list[tuple[str, str, str, str]] = []
    for entry in history:
        if entry.get("supersedes_id") is None:
            continue
        previous = by_id.get((entry.get("kind"), entry.get("supersedes_id")))
        delta = _requirement_delta(entry, previous)
        what = (
            f"{md_inline(entry.get('kind')).capitalize()} {md_id(entry.get('id'))} version "
            f"{md_id(entry.get('version'))} revises {md_id(entry.get('supersedes_id'))}"
            + (f" ({delta})" if delta else "")
        )
        decision = (
            f"approved {_date(entry.get('approved_at'))}"
            if entry.get("approved_at")
            else md_inline(entry.get("status"))
        )
        mark = src.cite(
            ref(
                entry.get("kind"),
                entry.get("id"),
                label=f"version {entry.get('version')}, {entry.get('status')}",
            )
        )
        rows.append((what, _date(entry.get("created_at")), decision, mark))
    for event in snapshot.get("events") or []:
        # A carry writes two events, "to" on the original and "from" on the copy: one row.
        if event.get("kind") != "carried_over" or event.get("direction") != "to":
            continue
        rows.append(
            (
                f"Task T-{md_id(event.get('task_id'))} of sprint {md_id(event.get('sprint_id'))} carried to another sprint",
                _date(event.get("created_at")),
                f"by {md_inline(event.get('actor'))}",
                src.cite(event_ref(event)),
            )
        )
    body = [
        (
            "Changes are recorded as data: a revision of an approved document is a "
            "new version that supersedes it once approved, and work moved between "
            "sprints is a task event. In adaptive work the backlog itself carries "
            "most change; see the WBS."
        ),
        "",
    ]
    missing = []
    if rows:
        body += [
            "| ID | Change | Raised | Decision | Source |",
            "|---|---|---|---|---|",
        ]
        body += [
            f"| C-{n} | {w} | {d} | {r} | {m} |"
            for n, (w, d, r, m) in enumerate(rows, 1)
        ]
    else:
        body.append(missing_line("no revision and no carried task is recorded."))
        missing.append("changes")
    return build("changes", title, PMBOK, body, src, snapshot, CHANGE_INPUTS, missing)
