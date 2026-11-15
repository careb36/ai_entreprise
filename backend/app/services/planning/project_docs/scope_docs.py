"""The scope documents of a pack: charter, scope statement, work breakdown.

Built from the snapshot (``collect``) alone. The structure follows the PMBOK
Guide, 8th edition (PMI, 2025): the charter in Governance (Initiating), the
scope statement and WBS in Scope (Planning); the wording is our own. Each
statement cites the record it came from; a section with no record says so. The
charter's background is the only prose a model writes here, passed in as
``prose`` and marked as a claim.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs.layout import (
    PMBOK,
    build,
    claim_block,
    missing_line,
    person_section,
)
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline

TECH_WRITER = "TechWriter"


def spec_ref(spec: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "specification",
        spec["id"],
        sha256=spec.get("sha256"),
        label=f"version {spec.get('version')}, {spec.get('status')}",
    )


def requirement_ref(spec: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "requirement",
        item.get("id"),
        sha256=digest(item),
        label=f"specification {spec['id']} version {spec.get('version')}",
    )


def task_ref(task: dict[str, Any], sprint_id: Any) -> dict[str, Any]:
    return ref("task", task.get("id"), sha256=digest(task), label=f"sprint {sprint_id}")


def tasks_of(snapshot: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every (sprint, task) pair, in sprint then task order."""
    return [
        (sprint, task)
        for sprint in snapshot.get("sprints") or []
        for task in sprint.get("tasks") or []
    ]


def covering(snapshot: dict[str, Any], req_id: str) -> list[tuple[dict, dict]]:
    """The tasks whose ``covers`` names *req_id* or one of its criteria."""
    prefix = f"{req_id}."
    return [
        (sprint, task)
        for sprint, task in tasks_of(snapshot)
        if any(
            str(c) == req_id or str(c).startswith(prefix)
            for c in task.get("covers") or []
        )
    ]


def _no_spec(key: str, title: str, snapshot: dict, inputs: tuple) -> dict[str, Any]:
    body = [missing_line("the project has no specification in La Fabrique.")]
    return build(key, title, PMBOK, body, Sources(), snapshot, inputs, ["all"])


CHARTER_INPUTS = ("project", "specification")


def charter(snapshot: dict[str, Any], prose: str | None = None) -> dict[str, Any]:
    """The project charter: what the project is for and who authorised it."""
    title = "Project charter"
    spec = snapshot.get("specification")
    if not spec:
        return _no_spec("charter", title, snapshot, CHARTER_INPUTS)
    src, missing = Sources(), []
    s = src.cite(spec_ref(spec))
    body = ["## Project", "", f"- Name: {md_inline(spec.get('title'))} {s}"]
    if spec.get("approved_at"):
        body.append(
            f"- Authorised: the specification was approved by a person on "
            f"{md_inline(spec['approved_at'][:10])} {s}. Who approved it is kept "
            "in La Fabrique and left out of the pack."
        )
    else:
        body.append(
            f"- Authorised: not yet. The specification is "
            f"{md_inline(spec.get('status'))} {s}, so this charter rests on a "
            "document no person has approved."
        )
        missing.append("authorisation")
    body += ["", "## Purpose", ""]
    if spec.get("summary"):
        body.append(f"{md_inline(spec['summary'], 1200)} {s}")
    else:
        body.append(missing_line("the specification has no summary."))
        missing.append("purpose")
    background, wrote = claim_block(TECH_WRITER, prose, "background")
    body += ["", "## Background", "", *background]
    claims = [{"section": "background", "role": TECH_WRITER}] if wrote else []
    if not wrote:
        missing.append("background")
    body += ["", "## Objectives", ""]
    for item in spec.get("requirements") or []:
        mark = src.cite(requirement_ref(spec, item))
        body.append(f"- {md_id(item.get('id'))}: {md_inline(item.get('title'))} {mark}")
    if not spec.get("requirements"):
        body.append(missing_line("the specification lists no requirement."))
        missing.append("objectives")
    body += ["", "## Success measures", ""]
    for item in spec.get("quality") or []:
        mark = src.cite(requirement_ref(spec, item))
        body.append(
            f"- {md_id(item.get('id'))}: {md_inline(item.get('attribute'))}, "
            f"target {md_inline(item.get('target'))} {mark}"
        )
    if not spec.get("quality"):
        body.append(
            missing_line("the specification sets no measurable quality target.")
        )
        missing.append("success measures")
    body += ["", "## Constraints", ""]
    body += [f"- {md_inline(c)} {s}" for c in spec.get("constraints") or []]
    if not spec.get("constraints"):
        body.append(missing_line("the specification names no constraint."))
        missing.append("constraints")
    for heading, what in CHARTER_PERSON_SECTIONS:
        body += ["", f"## {heading}", "", *person_section(what)]
    return build(
        "charter",
        title,
        PMBOK,
        body,
        src,
        snapshot,
        CHARTER_INPUTS,
        missing,
        claims,
        persons=[heading for heading, _ in CHARTER_PERSON_SECTIONS],
    )


# What a charter states that only a person can decide: the business case, the
# benefits and the budget (``docs/design/PROJECT_DOCUMENTATION.md``, Person fields).
CHARTER_PERSON_SECTIONS = (
    ("Business justification", "Why the investment is worth making, and for whom."),
    (
        "Expected benefits",
        "Which benefits are expected, when, and how they will be measured.",
    ),
    (
        "Budget envelope",
        "The money and time the sponsor commits; model spend is only part of it.",
    ),
    ("Sponsor", "Who sponsors the project and can stop it."),
)


SCOPE_INPUTS = ("specification", "sprints")


def _criterion(spec: dict, req: dict, criterion: dict, src: Sources) -> str:
    mark = src.cite(requirement_ref(spec, req))
    return (
        f"  - {md_id(criterion.get('id'))}: given {md_inline(criterion.get('given'))}, "
        f"when {md_inline(criterion.get('when'))}, then "
        f"{md_inline(criterion.get('then'))} {mark}"
    )


def _delivery(snapshot: dict, req_id: str, src: Sources) -> str:
    tasks = covering(snapshot, req_id)
    if not tasks:
        return "- Delivery: no task in the recorded sprints covers it."
    parts = [
        f"{md_id('T-' + str(task.get('id')))} {md_inline(task.get('status'))} "
        f"{src.cite(task_ref(task, sprint.get('id')))}"
        for sprint, task in tasks
    ]
    return "- Delivery: " + "; ".join(parts)


def scope(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The scope statement: what is in, how each part is accepted, what is open."""
    title = "Scope statement"
    spec = snapshot.get("specification")
    if not spec:
        return _no_spec("scope", title, snapshot, SCOPE_INPUTS)
    src, missing = Sources(), []
    s = src.cite(spec_ref(spec))
    body = ["## In scope", ""]
    for req in spec.get("requirements") or []:
        mark = src.cite(requirement_ref(spec, req))
        body += [
            f"### {md_id(req.get('id'))}: {md_inline(req.get('title'))} {mark}",
            "",
        ]
        if req.get("description"):
            body += [md_inline(req["description"], 1200), ""]
        body.append("- Acceptance criteria:")
        criteria = req.get("criteria") or []
        body += [_criterion(spec, req, c, src) for c in criteria]
        if not criteria:
            body.append(f"  - {missing_line('no acceptance criterion.')}")
        body += [_delivery(snapshot, str(req.get("id")), src), ""]
    if not spec.get("requirements"):
        body += [missing_line("the specification lists no requirement."), ""]
        missing.append("in scope")
    body += ["## Quality attributes", ""]
    for item in spec.get("quality") or []:
        mark = src.cite(requirement_ref(spec, item))
        body.append(
            f"- {md_id(item.get('id'))}: {md_inline(item.get('attribute'))}, "
            f"target {md_inline(item.get('target'))} {mark}"
        )
    if not spec.get("quality"):
        body.append(missing_line("no quality attribute is recorded."))
        missing.append("quality attributes")
    body += ["", "## Constraints", ""]
    body += [f"- {md_inline(c)} {s}" for c in spec.get("constraints") or []]
    if not spec.get("constraints"):
        body.append(missing_line("no constraint is recorded."))
        missing.append("constraints")
    body += [
        "",
        "## Out of scope",
        "",
        missing_line(
            "the specification records no exclusions; anything it does not "
            "list is not planned."
        ),
        "",
        "## Open questions",
        "",
    ]
    missing.append("out of scope")
    open_questions = [
        q for q in spec.get("questions") or [] if not (q.get("answer") or "").strip()
    ]
    body += [f"- {md_inline(q.get('question'))} {s}" for q in open_questions]
    if not open_questions:
        body.append(f"None: every question the specification raised is answered {s}.")
    return build("scope", title, PMBOK, body, src, snapshot, SCOPE_INPUTS, missing)


WBS_INPUTS = ("specification", "sprints")


def _task_line(code: str, sprint: dict, task: dict, src: Sources) -> str:
    assignee = md_inline(task.get("assignee") or "unassigned")
    return (
        f"- {code} {md_id('T-' + str(task.get('id')))}: {md_inline(task.get('title'))}"
        f" (sprint {md_id(sprint.get('id'))}, {md_inline(task.get('status'))}, "
        f"{assignee}) {src.cite(task_ref(task, sprint.get('id')))}"
    )


def wbs(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The work breakdown: requirements as work packages, the tasks under them."""
    title = "Work breakdown structure"
    spec = snapshot.get("specification")
    src, missing = Sources(), []
    root = md_inline(spec.get("title")) if spec else "Project"
    body = [
        (
            "Requirements are the work packages; the tasks that name a requirement "
            "or one of its criteria sit under it. A task can cover several."
        ),
        "",
        f"## 1 {root}" + (f" {src.cite(spec_ref(spec))}" if spec else ""),
        "",
    ]
    if not spec:
        body += [
            missing_line("no specification, so no requirement groups the work."),
            "",
        ]
        missing.append("work packages")
    mapped: set[Any] = set()
    for n, req in enumerate((spec or {}).get("requirements") or [], 1):
        mark = src.cite(requirement_ref(spec, req))
        body += [
            f"### 1.{n} {md_id(req.get('id'))}: {md_inline(req.get('title'))} {mark}",
            "",
        ]
        tasks = covering(snapshot, str(req.get("id")))
        for m, (sprint, task) in enumerate(tasks, 1):
            body.append(_task_line(f"1.{n}.{m}", sprint, task, src))
            mapped.add(task.get("id"))
        if not tasks:
            body.append(missing_line("no task covers this requirement yet."))
        body.append("")
    rest = [(sp, t) for sp, t in tasks_of(snapshot) if t.get("id") not in mapped]
    n = len((spec or {}).get("requirements") or []) + 1
    body += [f"### 1.{n} Work not mapped to a requirement", ""]
    for m, (sprint, task) in enumerate(rest, 1):
        body.append(_task_line(f"1.{n}.{m}", sprint, task, src))
    if not tasks_of(snapshot):
        body.append(missing_line("no sprint has a task yet."))
        missing.append("tasks")
    elif not rest:
        body.append("None: every recorded task names a requirement.")
    return build("wbs", title, PMBOK, body, src, snapshot, WBS_INPUTS, missing)
