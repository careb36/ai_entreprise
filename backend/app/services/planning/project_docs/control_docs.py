"""The control documents of a pack: schedule, RACI, risk register, lessons learned.

Built from the snapshot (``collect``) alone; no model writes any of them. The
structure follows the PMBOK Guide, 8th edition (PMI, 2025), each document in
its performance domain and focus area (``layout.PLACEMENT``); the wording is
our own, in the pack's language (``texts``). Where a cell comes from how the
factory works rather than from a record of this project (only a Manager
approves a specification, the Verifier checks every delivery), it cites the
factory rule and its document, never an invented record.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.contracts import roles as role_catalogue
from services.planning.project_docs import specs, statements, texts
from services.planning.project_docs.layout import (
    PMBOK,
    build,
    missing_line,
    tr_of,
)
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import spec_ref, task_ref, tasks_of

REASON_LIMIT = 240
LOOP_DOC = "docs/SPRINT_LOOP.md"
PLANNING_DOC = "docs/contexts/planning.md"
PACK_DOC = "docs/design/PROJECT_DOCUMENTATION.md"


def cell(value: Any, limit: int = 120) -> str:
    return md_inline(value, limit) if value not in (None, "") else "-"


def date(value: Any) -> str:
    return md_inline(str(value)[:10]) if value else "-"


def sprint_ref(sprint: dict[str, Any]) -> dict[str, Any]:
    summary = {k: v for k, v in sprint.items() if k != "tasks"}
    return ref(
        "sprint",
        sprint.get("id"),
        sha256=digest(summary),
        label=sprint.get("name") or "",
    )


def arch_ref(arch: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "architecture",
        arch["id"],
        sha256=arch.get("sha256"),
        label=texts.translator(lang)(
            "label_version",
            version=arch.get("version"),
            status=texts.status(lang, arch.get("status")),
        ),
    )


def event_ref(event: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "event",
        event.get("id"),
        sha256=event.get("chain_hash"),
        label=texts.translator(lang)(
            "label_event",
            kind=texts.status(lang, event.get("kind")),
            actor=event.get("actor"),
        ),
    )


def rule_ref(document: str, rule: str) -> dict[str, Any]:
    return ref("rule", document, label=rule)


def task_states(snapshot: dict[str, Any]) -> dict[Any, str]:
    return {t.get("id"): str(t.get("status")) for _, t in tasks_of(snapshot)}


SCHEDULE_INPUTS = ("specification", "architecture", "sprints", "integrations")


def schedule(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Milestones and sprints, with the dates the records hold."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    rows: list[str] = []
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    milestones = [
        ("schedule.spec", s, (lambda r: src.cite(spec_ref(r, lang))))
        for s in (specs.approved(snapshot) if specs.several(snapshot) else [spec])
    ]
    milestones.append(("schedule.arch", arch, (lambda r: src.cite(arch_ref(r, lang)))))
    for name, record, mark in milestones:
        if not record:
            continue
        state = (
            tr("schedule.reached")
            if record.get("approved_at")
            else tr("schedule.pending", status=texts.status(lang, record.get("status")))
        )
        label = tr(name)
        if name == "schedule.spec" and specs.several(snapshot):
            label = tr("schedule.spec_named", id=md_id(record.get("id")))
        rows.append(
            f"| {label} | {date(record.get('approved_at'))} | {cell(state)} | {mark(record)} |"
        )
    for item in snapshot.get("integrations") or []:
        commit = item.get("tested_commit") or item.get("commit")
        status = texts.status(lang, item.get("status"))
        state = (
            tr("schedule.at_commit", status=status, commit=str(commit)[:12])
            if commit
            else status
        )
        mark = src.cite(
            ref(
                "integration",
                item.get("id"),
                sha256=commit,
                label=tr("label_sprint", id=item.get("sprint_id")),
            )
        )
        rows.append(
            f"| {tr('schedule.integrated', id=md_id(item.get('sprint_id')))} | "
            f"{date(item.get('created_at'))} | {cell(state)} | {mark} |"
        )
    body = [tr("schedule.milestones"), ""]
    if rows:
        body += [tr("schedule.header"), "|---|---|---|---|", *rows]
    else:
        body.append(missing_line(lang, tr("schedule.no_milestones")))
        missing.append(tr("section.milestones"))
    body += ["", tr("schedule.sprints"), ""]
    sprints = snapshot.get("sprints") or []
    if sprints:
        body += [
            tr("schedule.sprints_note"),
            "",
            tr("schedule.sprints_header"),
            "|---|---|---|---|---|---|---|---|",
        ]
        for sprint in sprints:
            tasks = sprint.get("tasks") or []
            done = sum(1 for t in tasks if t.get("status") == "done")
            body.append(
                f"| {md_id(sprint.get('id'))} {cell(sprint.get('name'), 60)} | "
                f"{cell(sprint.get('goal'))} | {cell(texts.status(lang, sprint.get('status')))} | "
                f"{date(sprint.get('start_date'))} | {date(sprint.get('first_event_at'))} | "
                f"{date(sprint.get('last_event_at'))} | {done}/{len(tasks)} | "
                f"{src.cite(sprint_ref(sprint))} |"
            )
    else:
        body.append(missing_line(lang, tr("schedule.no_sprints")))
        missing.append(tr("section.sprints"))
    inputs = specs.inputs(snapshot, SCHEDULE_INPUTS)
    return build("schedule", PMBOK, body, src, snapshot, inputs, missing)


RACI_INPUTS = ("specification", "architecture", "sprints", "roles", "members")


def raci(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Who is responsible, accountable, consulted and informed for each deliverable."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    hired = list(snapshot.get("roles") or [])
    roster = src.cite(
        ref("membership", "roles", sha256=digest(hired), label=tr("label_roles"))
    )
    members = snapshot.get("members") or {}
    people = src.cite(
        ref("membership", "people", sha256=digest(members), label=tr("label_people"))
    )
    plan = src.cite(rule_ref(PLANNING_DOC, tr("raci.rule_plan")))
    loop = src.cite(rule_ref(LOOP_DOC, tr("raci.rule_loop")))
    pack = src.cite(rule_ref(PACK_DOC, tr("raci.rule_pack")))
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    team = tr("raci.team")

    def mark_hired(role: str) -> str:
        known = role_catalogue.canonical(role) in role_catalogue.ROLES
        return tr("raci.not_hired", role=role) if known and role not in hired else role

    def who(drafted_by_role: Any, role: str) -> str:
        return role if drafted_by_role == role else tr("raci.person")

    rows = []
    if spec:
        rows.append(
            (
                tr("raci.spec"),
                who(spec.get("drafted_by_role"), "PO"),
                "Manager",
                "Owner",
                team,
                f"{src.cite(spec_ref(spec, lang))} {plan}",
            )
        )
    else:
        missing.append(tr("section.specification"))
    if arch:
        rows.append(
            (
                tr("raci.arch"),
                who(arch.get("drafted_by_role"), "Architect"),
                "Manager",
                "PO",
                team,
                f"{src.cite(arch_ref(arch, lang))} {plan}",
            )
        )
    else:
        missing.append(tr("section.architecture"))
    by_role: dict[str, list[tuple[dict, dict]]] = {}
    for sprint, task in tasks_of(snapshot):
        key = role_catalogue.canonical(task.get("assignee")) or tr("unassigned")
        by_role.setdefault(key, []).append((sprint, task))
    for role in sorted(by_role):
        pairs = by_role[role]
        marks = " ".join(src.cite(task_ref(t, s.get("id"), lang)) for s, t in pairs[:5])
        more = f" {tr('raci.more', count=len(pairs) - 5)}" if len(pairs) > 5 else ""
        rows.append(
            (
                tr("raci.tasks", role=role, count=len(pairs)),
                mark_hired(role),
                tr("raci.qa_accountable"),
                "Architect",
                "ScrumMaster",
                f"{marks}{more} {loop}",
            )
        )
    if not by_role:
        missing.append(tr("section.tasks"))
    rows += [
        (tr("raci.verification"), tr("raci.verifier"), "-", "-", "QA", loop),
        (
            tr("raci.retro"),
            mark_hired("ScrumMaster"),
            tr("raci.retro_accountable"),
            team,
            team,
            loop,
        ),
        (tr("raci.pack"), mark_hired("TechWriter"), "Owner", "Manager", team, pack),
    ]
    roles_text = ", ".join(md_inline(r) for r in hired) or tr("dash_none")
    people_text = ", ".join(
        f"{md_inline(k)} {int(v)}" for k, v in sorted(members.items())
    ) or tr("dash_none")
    body = [
        tr(
            "raci.roles",
            roles=roles_text,
            mark=roster,
            people=people_text,
            pmark=people,
        ),
        "",
        tr("raci.header"),
        "|---|---|---|---|---|---|",
    ]
    for name, r, a, c, i, marks in rows:
        body.append(
            f"| {cell(name)} | {cell(r)} | {cell(a)} | {cell(c)} | {cell(i)} | {marks} |"
        )
    body += ["", tr("raci.note", mark=loop)]
    return build("raci", PMBOK, body, src, snapshot, RACI_INPUTS, missing)


RISK_INPUTS = ("sprints", "events", "verifications", statements.key_of("risks"))


def risks(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The risk register: the exposure the records show. What already went wrong
    (a block, an escalation) is an issue, in the issue log."""
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    state_of = task_states(snapshot)
    rows: list[tuple[str, str, str, str, str]] = []
    for item in snapshot.get("verifications") or []:
        if item.get("tier") != "high":
            continue
        reasons = "; ".join(str(r) for r in item.get("reasons") or []) or tr(
            "nothing_reason"
        )
        status = state_of.get(item.get("task_id"))
        state = tr("risks.closed_done") if status == "done" else tr("risks.open")
        mark = src.cite(
            ref(
                "verification",
                item.get("verification_id"),
                sha256=item.get("artifact_sha256"),
                label=tr("label_run", id=item.get("run_id")),
            )
        )
        rows.append(
            (
                tr(
                    "risks.change",
                    task=md_id(item.get("task_id")),
                    reasons=md_inline(reasons, REASON_LIMIT),
                ),
                tr("risks.change_cat"),
                state,
                date(item.get("observed_at")),
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
                tr("risks.hold", task=md_id(task_id)),
                tr("risks.hold_cat"),
                tr("risks.open")
                if status == "blocked"
                else tr("risks.closed", status=texts.status(lang, status)),
                date(event.get("created_at")),
                src.cite(event_ref(event, lang)),
            )
        )
    body = [tr("risks.intro"), ""]
    missing = []
    if rows:
        body += [tr("risks.header"), "|---|---|---|---|---|---|"]
        body += [
            f"| R-{n} | {r} | {c} | {cell(s)} | {d} | {m} |"
            for n, (r, c, s, d, m) in enumerate(rows, 1)
        ]
    else:
        body.append(missing_line(lang, tr("risks.none")))
        missing.append(tr("section.observed_risks"))
    heading = tr("person.risk")
    body += [
        "",
        f"## {heading}",
        "",
        *statements.lines(snapshot, "risks.appetite", src),
    ]
    return build(
        "risks",
        PMBOK,
        body,
        src,
        snapshot,
        RISK_INPUTS,
        missing,
        persons=[heading],
        statements=statements.filled(snapshot, "risks"),
    )


LESSONS_INPUTS = ("sprints", "retrospectives", "procedures")


def lessons(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The lessons learned register: each retrospective, and what its procedures measured."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    names = {s.get("id"): s.get("name") for s in snapshot.get("sprints") or []}
    retros = snapshot.get("retrospectives") or []
    procedures = snapshot.get("procedures") or []
    body = [tr("lessons.intro"), ""]
    for retro in retros:
        sprint_id = retro.get("sprint_id")
        mark = src.cite(
            ref(
                "message",
                retro.get("id"),
                sha256=digest(retro),
                label=tr("label_retro", role=retro.get("from_role")),
                claim=True,
            )
        )
        body += [
            tr(
                "lessons.sprint",
                id=md_id(sprint_id),
                name=cell(names.get(sprint_id), 80),
            ),
            "",
            tr("lessons.reported", mark=mark, body=md_inline(retro.get("body"), 800)),
        ]
        followed = [p for p in procedures if p.get("source_sprint_id") == sprint_id]
        for proc in followed:
            result = (
                texts.status(lang, proc.get("result"))
                if proc.get("result")
                else tr("lessons.not_measured")
            )
            pmark = src.cite(
                ref(
                    "procedure",
                    proc.get("id"),
                    sha256=digest(proc),
                    label=texts.status(lang, proc.get("status") or ""),
                )
            )
            body.append(
                tr(
                    "lessons.procedure",
                    role=md_inline(proc.get("role")),
                    title=md_inline(proc.get("title")),
                    status=md_inline(texts.status(lang, proc.get("status"))),
                    result=md_inline(result),
                    mark=pmark,
                )
            )
        if not followed:
            body.append(tr("lessons.no_procedure"))
        body.append("")
    if not retros:
        body.append(missing_line(lang, tr("lessons.none")))
        missing.append(tr("section.retrospectives"))
    return build("lessons", PMBOK, body, src, snapshot, LESSONS_INPUTS, missing)
