"""The release and iteration plan of a pack (issue #530, phase 2).

What was planned in each iteration and what left the factory, from the
figures ``iterations`` counts: a summary, the plan of each iteration, the
requirements by iteration, the carry-over between iterations and the
releases cut, then a person's section for target dates and scope ahead. The
structure follows the PMBOK Guide, 8th edition (PMI, 2025), where release
and iteration planning belongs to the Schedule performance domain and its
Planning focus area; the wording is our own, in the pack's language
(``texts``, ``texts_release_*``).

Every figure cites the records it was counted from: the sprint (its digest),
the specification and requirement (their digests), the task (its digest),
the carry (the task event's chain hash) and the release (the chain hash of
the audit row the backend wrote when a Manager published the sprint, with
the commit the workspace daemon reported). A sprint's goal and team are
shown as the plan recorded when it started, never as a result. The plan
reads no retrospective or agent message: its inputs do not name them, so a
claim can neither appear in it nor make it stale. A target date or a scope
decision ahead is a person's section (``statements``): a template, or a
person's statement quoted as a claim, below the records and never read by
them.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import iterations, specs, statements, texts
from services.planning.project_docs.control_docs import cell, date, sprint_ref
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import (
    requirement_ref,
    spec_ref,
    task_ref,
)

MAX_MARKS = 5
TARGETS = "release_plan.targets"
BASE_INPUTS = (*iterations.ITERATION_INPUTS, statements.key_of("release_plan"))


def carry_ref(carry: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "event",
        carry.get("event_id"),
        sha256=carry.get("chain_hash"),
        label=texts.translator(lang)(
            "release.label_carry",
            task=md_id(carry.get("from_task_id")),
            sprint=md_id(carry.get("to_sprint_id")),
        ),
    )


def release_ref(release: dict[str, Any], lang: str) -> dict[str, Any]:
    commit = str(release.get("published_commit") or "")[:12]
    return ref(
        "release",
        release.get("integration_id"),
        sha256=release.get("audit_chain_hash") or release.get("published_commit"),
        label=texts.translator(lang)(
            "release.label_release", id=release.get("sprint_id"), commit=commit
        ),
    )


def _marks(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "-"
    out = src.cite(*refs[:MAX_MARKS])
    return out + (f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else "")


def _commit(value: Any) -> str:
    return f"`{md_id(str(value)[:12])}`" if value else "-"


def _specs_by_id(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """Every specification the snapshot holds, by id and by each version's id."""
    out: dict[Any, dict[str, Any]] = {}
    for spec in specs.approved(snapshot):
        for version in spec.get("lineage_ids") or []:
            out.setdefault(version, spec)
    for spec in [*specs.approved(snapshot), snapshot.get("specification")]:
        if spec:
            out[spec.get("id")] = spec
    return out


def _summary(snapshot, total, src, lang, tr) -> list[str]:
    sprints = snapshot.get("sprints") or []
    sprint_marks = _marks(src, [sprint_ref(s) for s in sprints])
    spec_marks = _marks(src, [spec_ref(s, lang) for s in specs.documented(snapshot)])
    carry_marks = _marks(
        src, [carry_ref(c, lang) for c in iterations.carries(snapshot)]
    )
    release_marks = _marks(
        src, [release_ref(r, lang) for r in iterations.releases(snapshot)]
    )
    measures = [
        (
            "m_iterations",
            tr(
                "release.iterations_value",
                count=total["iterations"],
                completed=total["completed"],
                open=total["open"],
                cancelled=total["cancelled"],
            ),
            sprint_marks,
        ),
        (
            "m_planned",
            tr("release.of", part=total["planned"], whole=total["requirements"]),
            spec_marks,
        ),
        (
            "m_delivered",
            tr("release.of", part=total["delivered"], whole=total["requirements"]),
            spec_marks,
        ),
        ("m_carries", str(total["carries"]), carry_marks),
        ("m_releases", str(total["releases"]), release_marks),
    ]
    body = [
        tr("release.summary_heading"),
        "",
        tr("release.summary_header"),
        "|---|---|---|---|",
    ]
    for key, value, marks in measures:
        body.append(
            f"| {tr('release.' + key)} | {value} | {tr('release.' + key + '_how')} | {marks} |"
        )
    return body


def _team(sprint: dict[str, Any], plan: dict[str, Any], tr) -> str:
    team = [md_inline(r, 40) for r in sprint.get("team") or []]
    source = plan.get("team_source")
    key = f"release.team_source.{source}"
    if source and key in texts.TEXTS["en"]:
        said = tr(key)
    elif source:
        said = md_inline(source, 40)
    else:
        said = tr("release.team_source_unknown")
    return tr(
        "release.team_value",
        team=", ".join(team) if team else tr("release.team_all"),
        source=said,
    )


def _iterations(snapshot, src, lang, tr) -> list[str]:
    plans, by_id = iterations.plan_of(snapshot), _specs_by_id(snapshot)
    body = [
        tr("release.iteration_heading"),
        "",
        tr("release.iteration_note"),
        "",
        tr("release.iteration_header"),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sprint in snapshot.get("sprints") or []:
        tasks = sprint.get("tasks") or []
        done = sum(1 for t in tasks if t.get("status") == "done")
        plan = plans.get(sprint.get("id")) or {}
        refs = [sprint_ref(sprint)]
        spec_id = sprint.get("specification_id")
        spec = by_id.get(spec_id)
        if spec_id is None:
            planned_from = "-"
        elif spec and spec.get("id") == spec_id:
            planned_from = tr(
                "release.planned_from",
                id=md_id(spec_id),
                version=md_id(spec.get("version")),
            )
            refs.append(spec_ref(spec, lang))
        else:
            planned_from = tr("release.planned_from_id", id=md_id(spec_id))
        carried = ", ".join(md_id(r) for r in iterations.carried_requirements(sprint))
        dates = (
            f"{date(sprint.get('start_date'))}; "
            f"{date(sprint.get('first_event_at'))} – {date(sprint.get('last_event_at'))}"
        )
        body.append(
            f"| {md_id(sprint.get('id'))} {cell(sprint.get('name'), 60)} | {planned_from} | "
            f"{cell(sprint.get('goal'))} | {cell(plan.get('scope_size'), 10)} | "
            f"{_team(sprint, plan, tr)} | {cell(texts.status(lang, sprint.get('status')))} | "
            f"{dates} | {done}/{len(tasks)} | {carried or '-'} | {_marks(src, refs)} |"
        )
    return body


def _requirements_of(snapshot, spec, src, lang, tr) -> list[str]:
    body = [tr("release.requirements_header"), "|---|---|---|---|---|"]
    for row in iterations.requirement_rows(snapshot, spec):
        refs = [requirement_ref(spec, row["requirement"], lang)]
        refs += [task_ref(t, s.get("id"), lang) for s, t in row["tasks"]]
        sprint_ids = list(dict.fromkeys(md_id(s.get("id")) for s, _ in row["tasks"]))
        task_ids = ", ".join(f"T-{md_id(t.get('id'))}" for _, t in row["tasks"])
        body.append(
            f"| {md_id(row['id'])} | {', '.join(sprint_ids) or '-'} | "
            f"{task_ids or tr('dash_none')} | {tr('release.state.' + row['state'])} | "
            f"{_marks(src, refs)} |"
        )
    return body


def _requirements(snapshot, src, missing, lang, tr) -> list[str]:
    listed = specs.documented(snapshot)
    body = [tr("release.requirements_heading"), ""]
    if not listed:
        missing.append(tr("release.section_requirements"))
        return body + [missing_line(lang, tr("no_spec"))]
    body += [tr("release.requirements_note"), ""]
    if len(listed) == 1:
        return body + _requirements_of(snapshot, listed[0], src, lang, tr)
    body += specs.intro(snapshot, src)
    for spec in listed:
        body += [specs.heading(spec, src.cite(spec_ref(spec, lang)), lang, 3), ""]
        body += [*_requirements_of(snapshot, spec, src, lang, tr), ""]
    return body[:-1]


def _carries(snapshot, src, lang, tr) -> list[str]:
    found = iterations.carries(snapshot)
    body = [tr("release.carry_heading"), ""]
    if not found:
        return body + [tr("release.no_carries")]
    body += [tr("release.carry_header"), "|---|---|---|---|---|---|"]
    for c in found:
        to_task = c.get("to_task_id")
        body.append(
            f"| {md_id(c.get('from_sprint_id'))} | T-{md_id(c.get('from_task_id'))} | "
            f"{md_id(c.get('to_sprint_id')) if c.get('to_sprint_id') else '-'} | "
            f"{'T-' + md_id(to_task) if to_task else '-'} | {date(c.get('created_at'))} | "
            f"{src.cite(carry_ref(c, lang))} |"
        )
    return body


def _releases(snapshot, src, missing, lang, tr) -> list[str]:
    found = iterations.releases(snapshot)
    body = [tr("release.releases_heading"), ""]
    if not found:
        missing.append(tr("release.section_releases"))
        return body + [missing_line(lang, tr("release.no_releases"))]
    body += [
        tr("release.releases_note"),
        "",
        tr("release.releases_header"),
        "|---|---|---|---|---|---|---|",
    ]
    for r in found:
        how = str(r.get("how") or "")
        key = f"release.how.{how}"
        shown = tr(key) if key in texts.TEXTS["en"] else cell(how, 20)
        body.append(
            f"| {md_id(r.get('sprint_id'))} | {cell(texts.status(lang, r.get('status')))} | "
            f"{_commit(r.get('tested_commit'))} | {shown} | "
            f"{_commit(r.get('published_commit'))} | {date(r.get('published_at'))} | "
            f"{src.cite(release_ref(r, lang))} |"
        )
    return body


def plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The release and iteration plan from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    total = iterations.totals(snapshot)
    body = [tr("release.intro"), ""]
    body += _summary(snapshot, total, src, lang, tr)
    body += [""]
    if snapshot.get("sprints"):
        body += _iterations(snapshot, src, lang, tr)
    else:
        body += [tr("release.iteration_heading"), ""]
        body.append(missing_line(lang, tr("release.no_sprints")))
        missing.append(tr("release.section_iterations"))
    body += [""] + _requirements(snapshot, src, missing, lang, tr)
    body += [""] + _carries(snapshot, src, lang, tr)
    body += [""] + _releases(snapshot, src, missing, lang, tr)
    body += ["", f"## {tr('person.targets')}", ""]
    body += statements.lines(snapshot, TARGETS, src)
    body += ["", tr("release.read_heading"), "", tr("release.read")]
    return build(
        "release_plan",
        PMBOK,
        body,
        src,
        snapshot,
        specs.inputs(snapshot, BASE_INPUTS),
        missing,
        persons=[tr("person.targets")],
        statements=statements.filled(snapshot, "release_plan"),
    )
