"""Check results per performance domain: outcomes a pack can test against the records.

The PMBOK Guide, 8th edition (PMI, 2025) closes each performance domain with
target outcomes and a way to confirm each. Many of those confirmations are
computable from La Fabrique's records without a model; this module computes a
first set, in our own wording, and says plainly which outcomes only a person
can judge.

A check is ``(outcome, how it is checked, function)``; the function takes the
snapshot and the document's ``Sources`` and returns ``(result, observed,
marks)``. ``result`` is one of:

- ``met`` / ``not met``: the records answer the question;
- ``measured``: a number is reported, but the threshold is the Owner's to set
  (requirements stability, for instance), so no verdict is given;
- ``not computable``: nothing recorded yet to measure;
- ``person``: the outcome is a judgement or needs data La Fabrique does not
  hold (budget, external stakeholders).

``CHECKS`` is the extension point: a later phase adds a check by appending to
its domain's list. Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.contracts import roles as role_catalogue
from services.planning.project_docs.layout import DOMAINS, PMBOK, build
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_inline
from services.planning.project_docs.scope_docs import covering, spec_ref, tasks_of

MET, NOT_MET, MEASURED, NOT_COMPUTABLE, PERSON = (
    "met",
    "not met",
    "measured",
    "not computable",
    "person",
)
Result = tuple[str, str, str]
CheckFn = Callable[[dict[str, Any], Sources], Result]
MAX_MARKS = 5


def _sprint_marks(sprints: list[dict], src: Sources) -> str:
    marks = [
        src.cite(
            ref(
                "sprint",
                s.get("id"),
                sha256=digest({k: v for k, v in s.items() if k != "tasks"}),
                label=s.get("name") or "",
            )
        )
        for s in sprints[:MAX_MARKS]
    ]
    more = f" and {len(sprints) - MAX_MARKS} more" if len(sprints) > MAX_MARKS else ""
    return " ".join(marks) + more


def _ratio(part: int, whole: int) -> str:
    return f"{part} of {whole}"


def lessons_during(snapshot: dict, src: Sources) -> Result:
    done = [s for s in snapshot.get("sprints") or [] if s.get("status") == "completed"]
    if not done:
        return NOT_COMPUTABLE, "no sprint has completed yet", ""
    with_retro = {r.get("sprint_id") for r in snapshot.get("retrospectives") or []}
    count = sum(1 for s in done if s.get("id") in with_retro)
    return (
        (MET if count == len(done) else NOT_MET),
        f"{_ratio(count, len(done))} completed sprints have a retrospective",
        _sprint_marks(done, src),
    )


def plan_decided(snapshot: dict, src: Sources) -> Result:
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    if not spec:
        return NOT_MET, "no specification is recorded", ""
    marks = src.cite(spec_ref(spec))
    approved = [
        "specification" if spec.get("approved_at") else None,
        "architecture" if arch and arch.get("approved_at") else None,
    ]
    named = [a for a in approved if a]
    if arch:
        marks += " " + src.cite(
            ref("architecture", arch["id"], sha256=arch.get("sha256"))
        )
    return (
        (MET if len(named) == 2 else NOT_MET),
        (
            f"approved by a person: {', '.join(named)}"
            if named
            else "nothing approved yet"
        ),
        marks,
    )


def requirements_understood(snapshot: dict, src: Sources) -> Result:
    spec = snapshot.get("specification")
    if not spec or not spec.get("requirements"):
        return NOT_COMPUTABLE, "no requirement is recorded", ""
    reqs = spec["requirements"]
    with_criteria = sum(1 for r in reqs if r.get("criteria"))
    open_q = sum(
        1 for q in spec.get("questions") or [] if not (q.get("answer") or "").strip()
    )
    ok = with_criteria == len(reqs) and open_q == 0
    return (
        (MET if ok else NOT_MET),
        f"{_ratio(with_criteria, len(reqs))} requirements have acceptance criteria; {open_q} open questions",
        src.cite(spec_ref(spec)),
    )


def requirements_stability(snapshot: dict, src: Sources) -> Result:
    versions = [
        h for h in snapshot.get("history") or [] if h.get("kind") == "specification"
    ]
    if not versions:
        return NOT_COMPUTABLE, "no specification is recorded", ""
    first, last = (
        set(versions[0].get("requirement_ids") or []),
        set(versions[-1].get("requirement_ids") or []),
    )
    marks = " ".join(
        src.cite(ref("specification", v.get("id"), label=f"version {v.get('version')}"))
        for v in versions[:MAX_MARKS]
    )
    observed = f"{len(versions)} specification versions; since the first, {len(last - first)} requirements added and {len(first - last)} removed"
    return MEASURED, observed, marks


def requirements_covered(snapshot: dict, src: Sources) -> Result:
    spec = snapshot.get("specification")
    if not spec or not spec.get("requirements"):
        return NOT_COMPUTABLE, "no requirement is recorded", ""
    reqs = spec["requirements"]
    covered = sum(1 for r in reqs if covering(snapshot, str(r.get("id"))))
    return (
        (MET if covered == len(reqs) else NOT_MET),
        f"{_ratio(covered, len(reqs))} requirements are covered by a task",
        src.cite(spec_ref(spec)),
    )


def schedule_documented(snapshot: dict, src: Sources) -> Result:
    sprints = snapshot.get("sprints") or []
    if not sprints:
        return NOT_COMPUTABLE, "no sprint is recorded", ""
    dated = sum(1 for s in sprints if s.get("start_date") or s.get("first_event_at"))
    tasks = [t for _, t in tasks_of(snapshot)]
    assigned = sum(1 for t in tasks if t.get("assignee"))
    ok = dated == len(sprints) and assigned == len(tasks)
    observed = f"{_ratio(dated, len(sprints))} sprints dated; {_ratio(assigned, len(tasks))} tasks assigned"
    return (MET if ok else NOT_MET), observed, _sprint_marks(sprints, src)


def work_to_hired_roles(snapshot: dict, src: Sources) -> Result:
    tasks = [t for _, t in tasks_of(snapshot) if t.get("assignee")]
    if not tasks:
        return NOT_COMPUTABLE, "no task is assigned", ""
    hired = set(snapshot.get("roles") or [])
    inside = sum(
        1 for t in tasks if role_catalogue.canonical(t.get("assignee")) in hired
    )
    mark = src.cite(
        ref(
            "membership",
            "roles",
            sha256=digest(sorted(hired)),
            label="the roles the company hired",
        )
    )
    return (
        (MET if inside == len(tasks) else NOT_MET),
        f"{_ratio(inside, len(tasks))} assigned tasks go to a hired role",
        mark,
    )


def people_recorded(snapshot: dict, src: Sources) -> Result:
    members = snapshot.get("members") or {}
    mark = src.cite(
        ref(
            "membership",
            "people",
            sha256=digest(members),
            label="people by role, counted",
        )
    )
    return (
        (MET if members.get("Owner") else NOT_MET),
        ("an Owner is recorded" if members.get("Owner") else "no Owner is recorded"),
        mark,
    )


def risk_on_every_delivery(snapshot: dict, src: Sources) -> Result:
    checks = snapshot.get("verifications") or []
    if not checks:
        return NOT_COMPUTABLE, "no delivery has been verified yet", ""
    tiered = sum(1 for c in checks if c.get("tier"))
    marks = " ".join(
        src.cite(
            ref(
                "verification",
                c.get("verification_id"),
                sha256=c.get("artifact_sha256"),
                label=f"run {c.get('run_id')}",
            )
        )
        for c in checks[:MAX_MARKS]
    )
    return (
        (MET if tiered == len(checks) else NOT_MET),
        f"{_ratio(tiered, len(checks))} verified deliveries carry a risk tier",
        marks,
    )


def _person(_snapshot: dict, _src: Sources) -> Result:
    return PERSON, "a judgement or data La Fabrique does not hold", ""


CHECKS: dict[str, list[tuple[str, str, CheckFn]]] = {
    "Governance": [
        (
            "Lessons are learned during the project, not only at its end",
            "each completed sprint has a retrospective",
            lessons_during,
        ),
        (
            "The plan's decisions are taken by a person and recorded",
            "the specification and the architecture are approved",
            plan_decided,
        ),
    ],
    "Scope": [
        (
            "Requirements are clearly understood",
            "every requirement has acceptance criteria and no question is open",
            requirements_understood,
        ),
        (
            "Requirements stability is measured",
            "versions of the specification and requirement ids added or removed",
            requirements_stability,
        ),
        (
            "All the required work is planned",
            "every requirement is covered by at least one task",
            requirements_covered,
        ),
    ],
    "Schedule": [
        (
            "Schedule documentation is complete",
            "every sprint is dated and every task assigned",
            schedule_documented,
        ),
    ],
    "Finance": [
        (
            "The project delivers within its budget",
            "a budget set by the sponsor, compared with the spend",
            _person,
        ),
    ],
    "Stakeholders": [
        (
            "The people who decide are identified",
            "an Owner is recorded for the company",
            people_recorded,
        ),
        (
            "Stakeholders outside the company are engaged",
            "an external stakeholder register kept by a person",
            _person,
        ),
    ],
    "Resources": [
        (
            "Work goes to the team that was formed",
            "assigned tasks go to roles the company hired",
            work_to_hired_roles,
        ),
    ],
    "Risk": [
        (
            "The risk process runs on every delivery",
            "every verified delivery carries the Verifier's risk tier",
            risk_on_every_delivery,
        ),
    ],
}
CHECK_INPUTS = (
    "specification",
    "architecture",
    "sprints",
    "history",
    "roles",
    "members",
    "retrospectives",
    "verifications",
)


def run(snapshot: dict[str, Any], src: Sources | None = None) -> list[dict[str, Any]]:
    """Every check's result, in domain order, as plain data."""
    src = src or Sources()
    out = []
    for domain in DOMAINS:
        for outcome, how, fn in CHECKS.get(domain, []):
            result, observed, marks = fn(snapshot, src)
            out.append(
                {
                    "domain": domain,
                    "outcome": outcome,
                    "check": how,
                    "result": result,
                    "observed": observed,
                    "marks": marks,
                }
            )
    return out


def document(snapshot: dict[str, Any]) -> dict[str, Any]:
    title = "Check results by performance domain"
    src = Sources()
    results = run(snapshot, src)
    body = [
        (
            "Each row is an outcome, how La Fabrique checks it from its records, and "
            'what it found. "Measured" reports a number whose threshold is the '
            'Owner\'s to set; "person" marks an outcome only a person can judge.'
        ),
        "",
        "| Domain | Outcome | How it is checked | Result | Observed | Sources |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        body.append(
            f"| {r['domain']} | {md_inline(r['outcome'])} | {md_inline(r['check'])} | "
            f"{r['result']} | {md_inline(r['observed'])} | {r['marks'] or '-'} |"
        )
    missing = [r["outcome"] for r in results if r["result"] == NOT_COMPUTABLE]
    persons = [r["outcome"] for r in results if r["result"] == PERSON]
    return build(
        "checks",
        title,
        PMBOK,
        body,
        src,
        snapshot,
        CHECK_INPUTS,
        missing,
        persons=persons,
    )
