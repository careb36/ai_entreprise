"""The logs of a pack: requirements traceability, issue log, change log.

Built from the snapshot (``collect``) alone; no model writes any of them. The
structure follows the PMBOK Guide, 8th edition (PMI, 2025): traceability in
Scope (Monitoring and Controlling), the issue log in Governance (Executing),
the change log in Governance (Monitoring and Controlling); the wording is our
own, in the pack's language (``texts``).

The traceability matrix links each requirement to the tasks that cover it, the
Verifier's verdict on their latest delivery and the commit a sprint
integration merged them at: every link is an observation La Fabrique made, not
a declaration.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.planning.project_docs import specs, texts
from services.planning.project_docs.control_docs import (
    cell,
    date,
    event_ref,
    task_states,
)
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import (
    covering,
    requirement_ref,
    spec_ref,
    task_ref,
)

REASON_LIMIT = 240


def verification_ref(item: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "verification",
        item.get("verification_id"),
        sha256=item.get("artifact_sha256"),
        label=texts.translator(lang)(
            "label_run_verdict",
            id=item.get("run_id"),
            verdict=texts.status(lang, item.get("verdict")),
        ),
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
    lang, tr = tr_of(snapshot)
    listed = specs.documented(snapshot)
    src, missing = Sources(lang), []
    if not listed:
        body = [missing_line(lang, tr("no_spec"))]
        return build(
            "traceability",
            PMBOK,
            body,
            src,
            snapshot,
            TRACE_INPUTS,
            [tr("section.all")],
        )
    verified, merged = latest_verification(snapshot), integrated_at(snapshot)
    rows: list[dict[str, Any]] = []
    if len(listed) == 1:
        body = _trace_of(snapshot, listed[0], src, missing, verified, merged, rows)
    else:
        body = specs.intro(snapshot, src)
        for spec in listed:
            body += [specs.heading(spec, src.cite(spec_ref(spec, lang)), lang), ""]
            body += [
                *_trace_of(snapshot, spec, src, missing, verified, merged, rows),
                "",
            ]
    inputs = specs.inputs(snapshot, TRACE_INPUTS)
    return build(
        "traceability",
        PMBOK,
        body,
        src,
        snapshot,
        inputs,
        missing,
        data={"trace": rows},
    )


def _trace_of(
    snapshot: dict,
    spec: dict,
    src: Sources,
    missing: list,
    verified: dict,
    merged: dict,
    rows: list[dict[str, Any]],
) -> list[str]:
    """The matrix of *spec*; each requirement's links also go to *rows*, as
    data (a requirement with no task has an empty ``tasks``)."""
    lang, tr = src.lang, texts.translator(src.lang)
    body = [
        tr(
            "trace.intro",
            id=md_id(spec["id"]),
            version=md_id(spec.get("version")),
            mark=src.cite(spec_ref(spec, lang)),
        ),
        "",
        tr("trace.header"),
        "|---|---|---|---|---|---|---|",
    ]
    for req in spec.get("requirements") or []:
        req_id = str(req.get("id"))
        rmark = src.cite(requirement_ref(spec, req, lang))
        tasks = covering(snapshot, req_id, spec)
        links: list[dict[str, Any]] = []
        rows.append(
            {"specification": spec.get("id"), "requirement": req_id, "tasks": links}
        )
        if not tasks:
            body.append(
                f"| {md_id(req_id)} | - | {tr('dash_none')} | - | - | - | {rmark} |"
            )
            missing.append(tr("trace.missing_tasks", id=req_id))
            continue
        for sprint, task in tasks:
            marks = [rmark, src.cite(task_ref(task, sprint.get("id"), lang))]
            criteria = [
                md_id(c)
                for c in task.get("covers") or []
                if str(c) == req_id or str(c).startswith(f"{req_id}.")
            ]
            check = verified.get(task.get("id"))
            verdict = tr("trace.none_recorded")
            if check:
                verdict = md_inline(texts.status(lang, check.get("verdict")))
                marks.append(src.cite(verification_ref(check, lang)))
            commit = tr("trace.none_recorded")
            if task.get("id") in merged:
                integration, sha = merged[task.get("id")]
                commit = md_inline(str(sha)[:12]) if sha else tr("trace.no_commit")
                marks.append(
                    src.cite(
                        ref(
                            "integration",
                            integration.get("id"),
                            sha256=sha,
                            label=tr("label_sprint", id=integration.get("sprint_id")),
                        )
                    )
                )
            links.append(
                {
                    "task_id": task.get("id"),
                    "status": task.get("status"),
                    "covers": [
                        str(c)
                        for c in task.get("covers") or []
                        if str(c) == req_id or str(c).startswith(f"{req_id}.")
                    ],
                    "verdict": check.get("verdict") if check else None,
                    "integrated": task.get("id") in merged,
                    "commit": merged[task.get("id")][1]
                    if task.get("id") in merged
                    else None,
                }
            )
            body.append(
                f"| {md_id(req_id)} | {', '.join(criteria) or '-'} | T-{md_id(task.get('id'))} "
                f"{cell(task.get('title'), 60)} | {cell(texts.status(lang, task.get('status')))} | "
                f"{verdict} | {commit} | {' '.join(marks)} |"
            )
    if not spec.get("requirements"):
        body.append(missing_line(lang, tr("no_requirement")))
        missing.append(tr("section.requirements"))
    return body


ISSUE_INPUTS = ("sprints", "events", "verifications")


def issues(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The issue log: what went wrong, who raised it, how it stands now."""
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    state_of = task_states(snapshot)
    events = snapshot.get("events") or []
    rows: list[tuple[str, str, str, str, str]] = []
    for event in events:
        kind, task_id = event.get("kind"), event.get("task_id")
        reason = md_inline(event.get("reason") or tr("nothing_reason"), REASON_LIMIT)
        status = state_of.get(task_id, "unknown")
        if kind == "blocked":
            if status == "blocked":
                resolution = tr("issues.open")
            elif status == "done":
                resolution = tr("issues.resolved")
            else:
                resolution = tr("issues.back", status=texts.status(lang, status))
            text = tr("issues.blocked", task=md_id(task_id), reason=reason)
        elif kind == "escalated":
            decided = any(
                e.get("kind") == "decided"
                and e.get("task_id") == task_id
                and (e.get("id") or 0) > (event.get("id") or 0)
                for e in events
            )
            resolution = tr("issues.decided") if decided else tr("issues.open")
            text = tr("issues.escalated", task=md_id(task_id), reason=reason)
        else:
            continue
        rows.append(
            (
                text,
                md_inline(event.get("actor")),
                date(event.get("created_at")),
                resolution,
                src.cite(event_ref(event, lang)),
            )
        )
    for item in snapshot.get("verifications") or []:
        if item.get("verdict") not in (VERDICT_REFUTED, VERDICT_UNVERIFIABLE):
            continue
        status = state_of.get(item.get("task_id"), "unknown")
        rows.append(
            (
                tr(
                    "issues.failed",
                    task=md_id(item.get("task_id")),
                    verdict=md_inline(texts.status(lang, item.get("verdict"))),
                ),
                tr("issues.verifier"),
                date(item.get("observed_at")),
                tr("issues.resolved")
                if status == "done"
                else tr("issues.task_status", status=texts.status(lang, status)),
                src.cite(verification_ref(item, lang)),
            )
        )
    body = [tr("issues.intro"), ""]
    missing = []
    if rows:
        body += [tr("issues.header"), "|---|---|---|---|---|---|"]
        body += [
            f"| I-{n} | {t} | {a} | {d} | {cell(r)} | {m} |"
            for n, (t, a, d, r, m) in enumerate(rows, 1)
        ]
    else:
        body.append(missing_line(lang, tr("issues.none")))
        missing.append(tr("section.issues"))
    return build("issues", PMBOK, body, src, snapshot, ISSUE_INPUTS, missing)


CHANGE_INPUTS = ("history", "events")


def _requirement_delta(entry: dict, previous: dict | None, lang: str) -> str:
    if previous is None or "requirement_ids" not in entry:
        return ""
    tr = texts.translator(lang)
    now, before = (
        set(entry.get("requirement_ids") or []),
        set(previous.get("requirement_ids") or []),
    )
    added, removed = sorted(now - before), sorted(before - now)
    parts = []
    if added:
        parts.append(tr("changes.added", ids=", ".join(md_id(r) for r in added)))
    if removed:
        parts.append(tr("changes.removed", ids=", ".join(md_id(r) for r in removed)))
    return "; ".join(parts) or tr("changes.same")


def changes(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The change log: each revision of the specification and the architecture,
    and each task carried between sprints."""
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    history = snapshot.get("history") or []
    by_id = {(h.get("kind"), h.get("id")): h for h in history}
    rows: list[tuple[str, str, str, str]] = []
    for entry in history:
        if entry.get("supersedes_id") is None:
            continue
        previous = by_id.get((entry.get("kind"), entry.get("supersedes_id")))
        delta = _requirement_delta(entry, previous, lang)
        what = tr(
            "changes.revision",
            kind=tr(f"kind.{entry.get('kind')}"),
            id=md_id(entry.get("id")),
            version=md_id(entry.get("version")),
            previous=md_id(entry.get("supersedes_id")),
        ) + (f" ({delta})" if delta else "")
        decision = (
            tr("changes.approved", date=date(entry.get("approved_at")))
            if entry.get("approved_at")
            else md_inline(texts.status(lang, entry.get("status")))
        )
        mark = src.cite(
            ref(
                entry.get("kind"),
                entry.get("id"),
                label=tr(
                    "label_version",
                    version=entry.get("version"),
                    status=texts.status(lang, entry.get("status")),
                ),
            )
        )
        rows.append((what, date(entry.get("created_at")), decision, mark))
    for event in snapshot.get("events") or []:
        # A carry writes two events, "to" on the original and "from" on the copy: one row.
        if event.get("kind") != "carried_over" or event.get("direction") != "to":
            continue
        rows.append(
            (
                tr(
                    "changes.carried",
                    task=md_id(event.get("task_id")),
                    sprint=md_id(event.get("sprint_id")),
                ),
                date(event.get("created_at")),
                tr("changes.by", actor=md_inline(event.get("actor"))),
                src.cite(event_ref(event, lang)),
            )
        )
    body = [tr("changes.intro"), ""]
    missing = []
    if rows:
        body += [tr("changes.header"), "|---|---|---|---|---|"]
        body += [
            f"| C-{n} | {w} | {d} | {r} | {m} |"
            for n, (w, d, r, m) in enumerate(rows, 1)
        ]
    else:
        body.append(missing_line(lang, tr("changes.none")))
        missing.append(tr("section.changes"))
    return build("changes", PMBOK, body, src, snapshot, CHANGE_INPUTS, missing)
