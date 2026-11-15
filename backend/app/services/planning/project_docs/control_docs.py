"""The control documents of a pack: schedule, RACI, risk register, lessons learned.

Built from the snapshot (``collect``) alone; no model writes any of them. The
structure follows the PMBOK Guide, 8th edition (PMI, 2025), each document in
its performance domain and focus area (``layout.PLACEMENT``); the wording is
our own. Where a cell
comes from how the factory works rather than from a record of this project
(only a Manager approves a specification, the Verifier checks every delivery),
it cites the factory rule and its document, never an invented record.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.contracts import roles as role_catalogue
from services.planning.project_docs.layout import (
    PMBOK,
    build,
    missing_line,
    person_section,
)
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import (
    spec_ref,
    task_ref,
    tasks_of,
)

REASON_LIMIT = 240


def _cell(value: Any, limit: int = 120) -> str:
    return md_inline(value, limit) if value not in (None, "") else "-"


def _date(value: Any) -> str:
    return md_inline(str(value)[:10]) if value else "-"


def sprint_ref(sprint: dict[str, Any]) -> dict[str, Any]:
    summary = {k: v for k, v in sprint.items() if k != "tasks"}
    return ref(
        "sprint",
        sprint.get("id"),
        sha256=digest(summary),
        label=sprint.get("name") or "",
    )


def arch_ref(arch: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "architecture",
        arch["id"],
        sha256=arch.get("sha256"),
        label=f"version {arch.get('version')}, {arch.get('status')}",
    )


def event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "event",
        event.get("id"),
        sha256=event.get("chain_hash"),
        label=f"{event.get('kind')} by {event.get('actor')}",
    )


def rule_ref(document: str, rule: str) -> dict[str, Any]:
    return ref("rule", document, label=rule)


SCHEDULE_INPUTS = ("specification", "architecture", "sprints", "integrations")


def schedule(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Milestones and sprints, with the dates the records hold."""
    title = "Schedule and milestones"
    src, missing = Sources(), []
    rows: list[str] = []
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    if spec:
        state = (
            "reached" if spec.get("approved_at") else f"pending ({spec.get('status')})"
        )
        rows.append(
            f"| Specification approved | {_date(spec.get('approved_at'))} | "
            f"{_cell(state)} | {src.cite(spec_ref(spec))} |"
        )
    if arch:
        state = (
            "reached" if arch.get("approved_at") else f"pending ({arch.get('status')})"
        )
        rows.append(
            f"| Architecture approved | {_date(arch.get('approved_at'))} | "
            f"{_cell(state)} | {src.cite(arch_ref(arch))} |"
        )
    for item in snapshot.get("integrations") or []:
        commit = item.get("tested_commit") or item.get("commit")
        state = str(item.get("status")) + (
            f" at commit {str(commit)[:12]}" if commit else ""
        )
        mark = src.cite(
            ref(
                "integration",
                item.get("id"),
                sha256=commit,
                label=f"sprint {item.get('sprint_id')}",
            )
        )
        rows.append(
            f"| Sprint {md_id(item.get('sprint_id'))} integrated | "
            f"{_date(item.get('created_at'))} | {_cell(state)} | {mark} |"
        )
    body = ["## Milestones", ""]
    if rows:
        body += ["| Milestone | Date | State | Source |", "|---|---|---|---|", *rows]
    else:
        body.append(missing_line("no approval or integration is recorded yet."))
        missing.append("milestones")
    body += ["", "## Sprints", ""]
    sprints = snapshot.get("sprints") or []
    if sprints:
        body += [
            (
                "Dates come from the sprint's start date and its first and last task "
                "events; La Fabrique records no planned end date."
            ),
            "",
            "| Sprint | Goal | Status | Start | First event | Last event | Done | Source |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for sprint in sprints:
            tasks = sprint.get("tasks") or []
            done = sum(1 for t in tasks if t.get("status") == "done")
            body.append(
                f"| {md_id(sprint.get('id'))} {_cell(sprint.get('name'), 60)} | "
                f"{_cell(sprint.get('goal'))} | {_cell(sprint.get('status'))} | "
                f"{_date(sprint.get('start_date'))} | {_date(sprint.get('first_event_at'))} | "
                f"{_date(sprint.get('last_event_at'))} | {done}/{len(tasks)} | "
                f"{src.cite(sprint_ref(sprint))} |"
            )
    else:
        body.append(missing_line("no sprint has been planned."))
        missing.append("sprints")
    return build(
        "schedule", title, PMBOK, body, src, snapshot, SCHEDULE_INPUTS, missing
    )


RACI_INPUTS = ("specification", "architecture", "sprints", "roles", "members")
LOOP_DOC = "docs/SPRINT_LOOP.md"
PLANNING_DOC = "docs/contexts/planning.md"
PACK_DOC = "docs/design/PROJECT_DOCUMENTATION.md"


def _who(drafted_by_role: Any, role: str) -> str:
    return role if drafted_by_role == role else "a person"


def raci(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Who is responsible, accountable, consulted and informed for each deliverable."""
    title = "Responsibility assignment (RACI)"
    src, missing = Sources(), []
    hired = list(snapshot.get("roles") or [])
    roster = src.cite(
        ref(
            "membership",
            "roles",
            sha256=digest(hired),
            label="the roles the company hired",
        )
    )
    members = snapshot.get("members") or {}
    people = src.cite(
        ref(
            "membership",
            "people",
            sha256=digest(members),
            label="people by role, counted",
        )
    )
    plan = src.cite(
        rule_ref(
            PLANNING_DOC, "only a Manager approves a specification or an architecture"
        )
    )
    loop = src.cite(
        rule_ref(
            LOOP_DOC,
            "QA reviews and the Verifier checks every delivery; a high risk tier waits for a person",
        )
    )
    pack = src.cite(
        rule_ref(
            PACK_DOC,
            "the TechWriter compiles a documentation pack and an Owner approves it",
        )
    )
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")

    def mark_hired(role: str) -> str:
        known = role_catalogue.canonical(role) in role_catalogue.ROLES
        return f"{role} (not hired)" if known and role not in hired else role

    rows = []
    if spec:
        rows.append(
            (
                "Specification",
                _who(spec.get("drafted_by_role"), "PO"),
                "Manager",
                "Owner",
                "the team",
                f"{src.cite(spec_ref(spec))} {plan}",
            )
        )
    else:
        missing.append("specification")
    if arch:
        rows.append(
            (
                "Architecture",
                _who(arch.get("drafted_by_role"), "Architect"),
                "Manager",
                "PO",
                "the team",
                f"{src.cite(arch_ref(arch))} {plan}",
            )
        )
    else:
        missing.append("architecture")
    by_role: dict[str, list[tuple[dict, dict]]] = {}
    for sprint, task in tasks_of(snapshot):
        by_role.setdefault(
            role_catalogue.canonical(task.get("assignee")) or "unassigned", []
        ).append((sprint, task))
    for role in sorted(by_role):
        pairs = by_role[role]
        marks = " ".join(src.cite(task_ref(t, s.get("id"))) for s, t in pairs[:5])
        more = f" and {len(pairs) - 5} more" if len(pairs) > 5 else ""
        rows.append(
            (
                f"Tasks assigned to {role} ({len(pairs)})",
                mark_hired(role),
                "QA (review), a person for a high risk tier",
                "Architect",
                "ScrumMaster",
                f"{marks}{more} {loop}",
            )
        )
    if not by_role:
        missing.append("tasks")
    rows += [
        (
            "Verification of each delivery",
            "the Verifier (deterministic checks, not a role)",
            "-",
            "-",
            "QA",
            loop,
        ),
        (
            "Retrospective and procedures",
            mark_hired("ScrumMaster"),
            "Manager, or the board's numbers at autonomy 3",
            "the team",
            "the team",
            loop,
        ),
        (
            "Documentation pack",
            mark_hired("TechWriter"),
            "Owner",
            "Manager",
            "the team",
            pack,
        ),
    ]
    body = [
        (
            f"Roles hired: {', '.join(md_inline(r) for r in hired) or 'none'} {roster}. "
            f"People: {', '.join(f'{md_inline(k)} {int(v)}' for k, v in sorted(members.items())) or 'none'} "
            f"{people}; their names stay in La Fabrique."
        ),
        "",
        "| Deliverable | Responsible | Accountable | Consulted | Informed | Sources |",
        "|---|---|---|---|---|---|",
    ]
    for name, r, a, c, i, marks in rows:
        body.append(
            f"| {_cell(name)} | {_cell(r)} | {_cell(a)} | {_cell(c)} | {_cell(i)} | {marks} |"
        )
    body += [
        "",
        (
            "Consulted and informed follow how the factory routes its messages "
            f"{loop}; they are not recorded per deliverable."
        ),
    ]
    return build("raci", title, PMBOK, body, src, snapshot, RACI_INPUTS, missing)


RISK_INPUTS = ("sprints", "events", "verifications")


def task_states(snapshot: dict[str, Any]) -> dict[Any, str]:
    return {t.get("id"): str(t.get("status")) for _, t in tasks_of(snapshot)}


def risks(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The risk register: the exposure the records show. What already went wrong
    (a block, an escalation) is an issue, in the issue log."""
    title = "Risk register"
    src = Sources()
    state_of = task_states(snapshot)
    rows: list[tuple[str, str, str, str, str]] = []
    for item in snapshot.get("verifications") or []:
        if item.get("tier") != "high":
            continue
        reasons = (
            "; ".join(str(r) for r in item.get("reasons") or []) or "no reason recorded"
        )
        state = (
            "closed: task done"
            if state_of.get(item.get("task_id")) == "done"
            else "open"
        )
        mark = src.cite(
            ref(
                "verification",
                item.get("verification_id"),
                sha256=item.get("artifact_sha256"),
                label=f"run {item.get('run_id')}",
            )
        )
        rows.append(
            (
                (
                    f"The delivery of task T-{md_id(item.get('task_id'))} touches high-risk "
                    f"paths: {md_inline(reasons, REASON_LIMIT)}"
                ),
                "Change risk (Verifier tier high)",
                state,
                _date(item.get("observed_at")),
                mark,
            )
        )
    for event in snapshot.get("events") or []:
        if event.get("kind") != "risk_hold":
            continue
        task_id = event.get("task_id")
        status = state_of.get(task_id, "unknown")
        rows.append(
            (
                f"QA approved task T-{md_id(task_id)}, which waits for a person's acceptance",
                "Acceptance hold",
                "open" if status == "blocked" else f"closed: task {status}",
                _date(event.get("created_at")),
                src.cite(event_ref(event)),
            )
        )
    body = [
        (
            "Each risk below was observed in the records: a delivery the Verifier "
            "classified as high risk, or an approved delivery held for a person. "
            "Project-level risks (market, staffing, budget) are not recorded by "
            "La Fabrique and are left to a person below."
        ),
        "",
    ]
    missing = []
    if rows:
        body += [
            "| ID | Risk | Category | Status | Raised | Source |",
            "|---|---|---|---|---|---|",
        ]
        body += [
            f"| R-{n} | {r} | {c} | {_cell(s)} | {d} | {m} |"
            for n, (r, c, s, d, m) in enumerate(rows, 1)
        ]
    else:
        body.append(missing_line("no high risk tier or acceptance hold is recorded."))
        missing.append("observed risks")
    body += ["", "## Risk appetite and project-level risks", ""]
    body += person_section(
        "How much risk the sponsor accepts, the thresholds that call for a person, "
        "and the risks of the project as a whole, each with an owner and a response."
    )
    return build(
        "risks",
        title,
        PMBOK,
        body,
        src,
        snapshot,
        RISK_INPUTS,
        missing,
        persons=["Risk appetite and project-level risks"],
    )


LESSONS_INPUTS = ("sprints", "retrospectives", "procedures")


def lessons(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Lessons learned: each retrospective, and what its procedures measured."""
    title = "Lessons learned register"
    src, missing = Sources(), []
    names = {s.get("id"): s.get("name") for s in snapshot.get("sprints") or []}
    retros = snapshot.get("retrospectives") or []
    procedures = snapshot.get("procedures") or []
    body = [
        (
            "A retrospective is the ScrumMaster's message to the team: a claim. "
            "Whether a procedure it led to helped is measured by the board's numbers "
            "in the sprints after it, never by a model."
        ),
        "",
    ]
    for retro in retros:
        sprint_id = retro.get("sprint_id")
        mark = src.cite(
            ref(
                "message",
                retro.get("id"),
                sha256=digest(retro),
                label=f"retrospective by {retro.get('from_role')}",
                claim=True,
            )
        )
        body += [
            f"## Sprint {md_id(sprint_id)}: {_cell(names.get(sprint_id), 80)}",
            "",
            f"- Reported {mark}: {md_inline(retro.get('body'), 800)}",
        ]
        followed = [p for p in procedures if p.get("source_sprint_id") == sprint_id]
        for proc in followed:
            result = proc.get("result") or "not measured yet"
            pmark = src.cite(
                ref(
                    "procedure",
                    proc.get("id"),
                    sha256=digest(proc),
                    label=proc.get("status") or "",
                )
            )
            body.append(
                f"- Procedure for {md_inline(proc.get('role'))}: {md_inline(proc.get('title'))}, {md_inline(proc.get('status'))}; measured result: {md_inline(result)} {pmark}"
            )
        if not followed:
            body.append("- No procedure was proposed after it.")
        body.append("")
    if not retros:
        body.append(missing_line("no sprint has closed with a retrospective."))
        missing.append("retrospectives")
    return build("lessons", title, PMBOK, body, src, snapshot, LESSONS_INPUTS, missing)
