"""The scope documents of a pack: charter, scope statement, work breakdown.

Built from the snapshot (``collect``) alone. The structure follows the PMBOK
Guide, 8th edition (PMI, 2025): the charter in Governance (Initiating), the
scope statement and WBS in Scope (Planning); the wording is our own, in the
pack's language (``texts``). Each statement cites the record it came from; a
section with no record says so. The charter's background is the only prose a
model writes here, passed in as ``prose`` and marked as a claim.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import texts
from services.planning.project_docs.layout import (
    PMBOK,
    build,
    claim_block,
    missing_line,
    person_section,
    tr_of,
)
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline

TECH_WRITER = "TechWriter"
# What a charter states that only a person can decide: the business case, the
# benefits, the budget and who sponsors it (text keys in ``texts``).
CHARTER_PERSON_SECTIONS = ("justification", "benefits", "budget", "sponsor")


def spec_ref(spec: dict[str, Any], lang: str) -> dict[str, Any]:
    tr = texts.translator(lang)
    return ref(
        "specification",
        spec["id"],
        sha256=spec.get("sha256"),
        label=tr(
            "label_version",
            version=spec.get("version"),
            status=texts.status(lang, spec.get("status")),
        ),
    )


def requirement_ref(
    spec: dict[str, Any], item: dict[str, Any], lang: str
) -> dict[str, Any]:
    tr = texts.translator(lang)
    return ref(
        "requirement",
        item.get("id"),
        sha256=digest(item),
        label=tr("label_spec_version", id=spec["id"], version=spec.get("version")),
    )


def task_ref(task: dict[str, Any], sprint_id: Any, lang: str) -> dict[str, Any]:
    tr = texts.translator(lang)
    return ref(
        "task",
        task.get("id"),
        sha256=digest(task),
        label=tr("label_sprint", id=sprint_id),
    )


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


def _no_spec(key: str, snapshot: dict, inputs: tuple) -> dict[str, Any]:
    lang, tr = tr_of(snapshot)
    body = [missing_line(lang, tr("no_spec"))]
    return build(key, PMBOK, body, Sources(lang), snapshot, inputs, [tr("section.all")])


CHARTER_INPUTS = ("project", "specification")


def charter(snapshot: dict[str, Any], prose: str | None = None) -> dict[str, Any]:
    """The project charter: what the project is for and who authorised it."""
    spec = snapshot.get("specification")
    if not spec:
        return _no_spec("charter", snapshot, CHARTER_INPUTS)
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    s = src.cite(spec_ref(spec, lang))
    body = [
        tr("charter.project"),
        "",
        tr("charter.name", name=md_inline(spec.get("title")), mark=s),
    ]
    if spec.get("approved_at"):
        body.append(
            tr("charter.authorised", date=md_inline(spec["approved_at"][:10]), mark=s)
        )
    else:
        body.append(
            tr(
                "charter.not_authorised",
                status=md_inline(texts.status(lang, spec.get("status"))),
                mark=s,
            )
        )
        missing.append(tr("section.authorisation"))
    body += ["", tr("charter.purpose"), ""]
    if spec.get("summary"):
        body.append(f"{md_inline(spec['summary'], 1200)} {s}")
    else:
        body.append(missing_line(lang, tr("charter.no_summary")))
        missing.append(tr("section.purpose"))
    background, wrote = claim_block(lang, TECH_WRITER, prose, tr("section.background"))
    body += ["", tr("charter.background"), "", *background]
    claims = (
        [{"section": tr("section.background"), "role": TECH_WRITER}] if wrote else []
    )
    if not wrote:
        missing.append(tr("section.background"))
    body += ["", tr("charter.objectives"), ""]
    for item in spec.get("requirements") or []:
        mark = src.cite(requirement_ref(spec, item, lang))
        body.append(f"- {md_id(item.get('id'))}: {md_inline(item.get('title'))} {mark}")
    if not spec.get("requirements"):
        body.append(missing_line(lang, tr("no_requirement")))
        missing.append(tr("section.objectives"))
    body += ["", tr("charter.success"), ""]
    for item in spec.get("quality") or []:
        mark = src.cite(requirement_ref(spec, item, lang))
        body.append(
            "- "
            + tr(
                "charter.target",
                id=md_id(item.get("id")),
                attribute=md_inline(item.get("attribute")),
                target=md_inline(item.get("target")),
                mark=mark,
            )
        )
    if not spec.get("quality"):
        body.append(missing_line(lang, tr("charter.no_quality")))
        missing.append(tr("section.success"))
    body += ["", tr("charter.constraints"), ""]
    body += [f"- {md_inline(c)} {s}" for c in spec.get("constraints") or []]
    if not spec.get("constraints"):
        body.append(missing_line(lang, tr("charter.no_constraints")))
        missing.append(tr("section.constraints"))
    persons = []
    for name in CHARTER_PERSON_SECTIONS:
        heading = tr(f"person.{name}")
        persons.append(heading)
        body += [
            "",
            f"## {heading}",
            "",
            *person_section(lang, tr(f"person.{name}_what")),
        ]
    return build(
        "charter",
        PMBOK,
        body,
        src,
        snapshot,
        CHARTER_INPUTS,
        missing,
        claims,
        persons=persons,
    )


SCOPE_INPUTS = ("specification", "sprints")


def _criterion(spec: dict, req: dict, criterion: dict, src: Sources) -> str:
    tr = texts.translator(src.lang)
    mark = src.cite(requirement_ref(spec, req, src.lang))
    return "  - " + tr(
        "scope.criterion",
        id=md_id(criterion.get("id")),
        given=md_inline(criterion.get("given")),
        when=md_inline(criterion.get("when")),
        then=md_inline(criterion.get("then")),
        mark=mark,
    )


def _delivery(snapshot: dict, req_id: str, src: Sources) -> str:
    tr = texts.translator(src.lang)
    tasks = covering(snapshot, req_id)
    if not tasks:
        return tr("scope.no_delivery")
    parts = [
        f"{md_id('T-' + str(task.get('id')))} {md_inline(texts.status(src.lang, task.get('status')))} "
        f"{src.cite(task_ref(task, sprint.get('id'), src.lang))}"
        for sprint, task in tasks
    ]
    return tr("scope.delivery", tasks="; ".join(parts))


def scope(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The scope statement: what is in, how each part is accepted, what is open."""
    spec = snapshot.get("specification")
    if not spec:
        return _no_spec("scope", snapshot, SCOPE_INPUTS)
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    s = src.cite(spec_ref(spec, lang))
    body = [tr("scope.in"), ""]
    for req in spec.get("requirements") or []:
        mark = src.cite(requirement_ref(spec, req, lang))
        body += [
            f"### {md_id(req.get('id'))}: {md_inline(req.get('title'))} {mark}",
            "",
        ]
        if req.get("description"):
            body += [md_inline(req["description"], 1200), ""]
        body.append(tr("scope.criteria"))
        criteria = req.get("criteria") or []
        body += [_criterion(spec, req, c, src) for c in criteria]
        if not criteria:
            body.append(f"  - {missing_line(lang, tr('scope.no_criterion'))}")
        body += [_delivery(snapshot, str(req.get("id")), src), ""]
    if not spec.get("requirements"):
        body += [missing_line(lang, tr("no_requirement")), ""]
        missing.append(tr("section.in_scope"))
    body += [tr("scope.quality"), ""]
    for item in spec.get("quality") or []:
        mark = src.cite(requirement_ref(spec, item, lang))
        body.append(
            "- "
            + tr(
                "charter.target",
                id=md_id(item.get("id")),
                attribute=md_inline(item.get("attribute")),
                target=md_inline(item.get("target")),
                mark=mark,
            )
        )
    if not spec.get("quality"):
        body.append(missing_line(lang, tr("scope.no_quality")))
        missing.append(tr("section.quality"))
    body += ["", tr("charter.constraints"), ""]
    body += [f"- {md_inline(c)} {s}" for c in spec.get("constraints") or []]
    if not spec.get("constraints"):
        body.append(missing_line(lang, tr("scope.no_constraints")))
        missing.append(tr("section.constraints"))
    body += [
        "",
        tr("scope.out"),
        "",
        missing_line(lang, tr("scope.no_out")),
        "",
        tr("scope.questions"),
        "",
    ]
    missing.append(tr("section.out_of_scope"))
    open_questions = [
        q for q in spec.get("questions") or [] if not (q.get("answer") or "").strip()
    ]
    body += [f"- {md_inline(q.get('question'))} {s}" for q in open_questions]
    if not open_questions:
        body.append(tr("scope.no_questions", mark=s))
    return build("scope", PMBOK, body, src, snapshot, SCOPE_INPUTS, missing)


WBS_INPUTS = ("specification", "sprints")


def _task_line(code: str, sprint: dict, task: dict, src: Sources) -> str:
    tr = texts.translator(src.lang)
    return "- " + tr(
        "wbs.task",
        code=code,
        task=md_id("T-" + str(task.get("id"))),
        title=md_inline(task.get("title")),
        sprint=md_id(sprint.get("id")),
        status=md_inline(texts.status(src.lang, task.get("status"))),
        assignee=md_inline(task.get("assignee") or tr("unassigned")),
        mark=src.cite(task_ref(task, sprint.get("id"), src.lang)),
    )


def wbs(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The work breakdown: requirements as work packages, the tasks under them."""
    lang, tr = tr_of(snapshot)
    spec = snapshot.get("specification")
    src, missing = Sources(lang), []
    root = md_inline(spec.get("title")) if spec else tr("wbs.project")
    body = [
        tr("wbs.intro"),
        "",
        f"## 1 {root}" + (f" {src.cite(spec_ref(spec, lang))}" if spec else ""),
        "",
    ]
    if not spec:
        body += [missing_line(lang, tr("wbs.no_spec")), ""]
        missing.append(tr("section.work_packages"))
    mapped: set[Any] = set()
    for n, req in enumerate((spec or {}).get("requirements") or [], 1):
        mark = src.cite(requirement_ref(spec, req, lang))
        body += [
            f"### 1.{n} {md_id(req.get('id'))}: {md_inline(req.get('title'))} {mark}",
            "",
        ]
        tasks = covering(snapshot, str(req.get("id")))
        for m, (sprint, task) in enumerate(tasks, 1):
            body.append(_task_line(f"1.{n}.{m}", sprint, task, src))
            mapped.add(task.get("id"))
        if not tasks:
            body.append(missing_line(lang, tr("wbs.no_task")))
        body.append("")
    rest = [(sp, t) for sp, t in tasks_of(snapshot) if t.get("id") not in mapped]
    n = len((spec or {}).get("requirements") or []) + 1
    body += [f"### 1.{n} {tr('wbs.unmapped')}", ""]
    for m, (sprint, task) in enumerate(rest, 1):
        body.append(_task_line(f"1.{n}.{m}", sprint, task, src))
    if not tasks_of(snapshot):
        body.append(missing_line(lang, tr("wbs.no_tasks")))
        missing.append(tr("section.tasks"))
    elif not rest:
        body.append(tr("wbs.all_mapped"))
    return build("wbs", PMBOK, body, src, snapshot, WBS_INPUTS, missing)
