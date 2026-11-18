"""Check results per performance domain: outcomes a pack can test against the records.

The PMBOK Guide, 8th edition (PMI, 2025) closes each performance domain with
target outcomes and a way to confirm each. Many of those confirmations are
computable from La Fabrique's records without a model; this module computes a
first set, in our own wording (``texts``, ``check.*``), and says plainly which
outcomes only a person can judge.

A check is ``(id, function)``; its outcome and method are the texts
``check.<id>`` and ``check.<id>_how``. The function takes the snapshot, the
document's ``Sources`` and the translator and returns ``(result, observed,
marks)``. ``result`` is one of:

- ``met`` / ``not met``: the records answer the question;
- ``measured``: a number is reported, but the threshold is the Owner's to set
  (requirements stability, for instance), so no verdict is given;
- ``not computable``: nothing recorded yet to measure;
- ``person``: the outcome is a judgement or needs data La Fabrique does not
  hold (budget, external stakeholders).

``CHECKS`` is the extension point: a later phase adds a check by appending to
its domain's list (and its texts in both languages). Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.contracts import roles as role_catalogue
from services.planning.project_docs import (
    performance,
    specs,
    statements,
    texts,
    wstg_status,
)
from services.planning.project_docs.layout import DOMAINS, PMBOK, build, tr_of
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
Tr = Callable[..., str]
CheckFn = Callable[[dict[str, Any], Sources, Tr], Result]
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
    more = f" (+{len(sprints) - MAX_MARKS})" if len(sprints) > MAX_MARKS else ""
    return " ".join(marks) + more


def lessons_during(snapshot: dict, src: Sources, tr: Tr) -> Result:
    done = [s for s in snapshot.get("sprints") or [] if s.get("status") == "completed"]
    if not done:
        return NOT_COMPUTABLE, tr("check.lessons_none"), ""
    with_retro = {r.get("sprint_id") for r in snapshot.get("retrospectives") or []}
    count = sum(1 for s in done if s.get("id") in with_retro)
    return (
        MET if count == len(done) else NOT_MET,
        tr("check.lessons_obs", part=count, whole=len(done)),
        _sprint_marks(done, src),
    )


def plan_decided(snapshot: dict, src: Sources, tr: Tr) -> Result:
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    if not spec:
        return NOT_MET, tr("check.decided_no_spec"), ""
    marks = src.cite(spec_ref(spec, src.lang))
    named = []
    if spec.get("approved_at"):
        named.append(tr("check.word_spec"))
    if arch:
        marks += " " + src.cite(
            ref("architecture", arch["id"], sha256=arch.get("sha256"))
        )
        if arch.get("approved_at"):
            named.append(tr("check.word_arch"))
    observed = (
        tr("check.decided_obs", names=", ".join(named))
        if named
        else tr("check.decided_none")
    )
    return (MET if len(named) == 2 else NOT_MET), observed, marks


def requirements_understood(snapshot: dict, src: Sources, tr: Tr) -> Result:
    spec = snapshot.get("specification")
    if not spec or not spec.get("requirements"):
        return NOT_COMPUTABLE, tr("check.no_requirements"), ""
    reqs = spec["requirements"]
    with_criteria = sum(1 for r in reqs if r.get("criteria"))
    open_q = sum(
        1 for q in spec.get("questions") or [] if not (q.get("answer") or "").strip()
    )
    ok = with_criteria == len(reqs) and open_q == 0
    observed = tr(
        "check.understood_obs", part=with_criteria, whole=len(reqs), open=open_q
    )
    return (MET if ok else NOT_MET), observed, src.cite(spec_ref(spec, src.lang))


def requirements_stability(snapshot: dict, src: Sources, tr: Tr) -> Result:
    versions = [
        h for h in snapshot.get("history") or [] if h.get("kind") == "specification"
    ]
    if not versions:
        return NOT_COMPUTABLE, tr("check.decided_no_spec"), ""
    first = set(versions[0].get("requirement_ids") or [])
    last = set(versions[-1].get("requirement_ids") or [])
    marks = " ".join(
        src.cite(
            ref(
                "specification",
                v.get("id"),
                label=tr(
                    "label_version",
                    version=v.get("version"),
                    status=texts.status(src.lang, v.get("status")),
                ),
            )
        )
        for v in versions[:MAX_MARKS]
    )
    observed = tr(
        "check.stability_obs",
        versions=len(versions),
        added=len(last - first),
        removed=len(first - last),
    )
    return MEASURED, observed, marks


def requirements_covered(snapshot: dict, src: Sources, tr: Tr) -> Result:
    """Requirements with a task, over every approved specification when there
    are several (each requirement against its own specification's sprints)."""
    listed = [s for s in specs.documented(snapshot) if s.get("requirements")]
    if not listed:
        return NOT_COMPUTABLE, tr("check.no_requirements"), ""
    whole = sum(len(s["requirements"]) for s in listed)
    covered = sum(
        1
        for s in listed
        for r in s["requirements"]
        if covering(snapshot, str(r.get("id")), s)
    )
    observed = tr("check.covered_obs", part=covered, whole=whole)
    return (
        (MET if covered == whole else NOT_MET),
        observed,
        " ".join(src.cite(spec_ref(s, src.lang)) for s in listed),
    )


def schedule_documented(snapshot: dict, src: Sources, tr: Tr) -> Result:
    sprints = snapshot.get("sprints") or []
    if not sprints:
        return NOT_COMPUTABLE, tr("check.no_sprint"), ""
    dated = sum(1 for s in sprints if s.get("start_date") or s.get("first_event_at"))
    tasks = [t for _, t in tasks_of(snapshot)]
    assigned = sum(1 for t in tasks if t.get("assignee"))
    ok = dated == len(sprints) and assigned == len(tasks)
    observed = tr(
        "check.schedule_obs",
        dated=dated,
        sprints=len(sprints),
        assigned=assigned,
        tasks=len(tasks),
    )
    return (MET if ok else NOT_MET), observed, _sprint_marks(sprints, src)


def work_to_hired_roles(snapshot: dict, src: Sources, tr: Tr) -> Result:
    tasks = [t for _, t in tasks_of(snapshot) if t.get("assignee")]
    if not tasks:
        return NOT_COMPUTABLE, tr("check.no_assigned"), ""
    hired = set(snapshot.get("roles") or [])
    inside = sum(
        1 for t in tasks if role_catalogue.canonical(t.get("assignee")) in hired
    )
    mark = src.cite(
        ref(
            "membership", "roles", sha256=digest(sorted(hired)), label=tr("label_roles")
        )
    )
    return (
        (MET if inside == len(tasks) else NOT_MET),
        tr("check.team_obs", part=inside, whole=len(tasks)),
        mark,
    )


def people_recorded(snapshot: dict, src: Sources, tr: Tr) -> Result:
    members = snapshot.get("members") or {}
    mark = src.cite(
        ref("membership", "people", sha256=digest(members), label=tr("label_people"))
    )
    if members.get("Owner"):
        return MET, tr("check.people_yes"), mark
    return NOT_MET, tr("check.people_no"), mark


def risk_on_every_delivery(snapshot: dict, src: Sources, tr: Tr) -> Result:
    checks = snapshot.get("verifications") or []
    if not checks:
        return NOT_COMPUTABLE, tr("check.no_verified"), ""
    tiered = sum(1 for c in checks if c.get("tier"))
    marks = " ".join(
        src.cite(
            ref(
                "verification",
                c.get("verification_id"),
                sha256=c.get("artifact_sha256"),
                label=tr("label_run", id=c.get("run_id")),
            )
        )
        for c in checks[:MAX_MARKS]
    )
    return (
        (MET if tiered == len(checks) else NOT_MET),
        tr("check.risk_obs", part=tiered, whole=len(checks)),
        marks,
    )


def security_tests_run(snapshot: dict, src: Sources, tr: Tr) -> Result:
    """Measured: the threshold is the specification's security target (STD-PO-1)."""
    rows = [r for r in wstg_status.rows(snapshot) if r["applicable"]]
    if not rows:
        return NOT_COMPUTABLE, tr("check.security_none"), ""
    count = wstg_status.counts(rows)
    observed = [
        r["evidence"]
        for r in rows
        if r["evidence"] and r["evidence"]["kind"] == "verification"
    ]
    marks = " ".join(
        dict.fromkeys(
            src.cite(
                ref(
                    "verification",
                    e.get("verification_id"),
                    sha256=e.get("artifact_sha256"),
                    label=tr("label_run", id=e.get("run_id")),
                )
            )
            for e in observed[:MAX_MARKS]
        )
    )
    return (
        MEASURED,
        tr(
            "check.security_obs",
            passing=count[wstg_status.OBSERVED_PASS],
            applicable=len(rows),
            failing=count[wstg_status.OBSERVED_FAIL],
            open=count[wstg_status.NOT_RUN] + count[wstg_status.NEEDS_MANUAL],
        ),
        marks,
    )


def _effort_marks(entries: list[dict], src: Sources, tr: Tr) -> str:
    marks = [
        src.cite(
            ref(
                "event",
                e.get("event_id"),
                sha256=e.get("chain_hash"),
                label=tr("status.label_effort", task=e.get("task_id")),
            )
        )
        for e in entries[:MAX_MARKS]
    ]
    more = f" (+{len(entries) - MAX_MARKS})" if len(entries) > MAX_MARKS else ""
    return " ".join(marks) + more


def carry_over(snapshot: dict, src: Sources, tr: Tr) -> Result:
    """Measured: how much carry-over is acceptable is the Owner's to set."""
    rows = performance.sprint_rows(snapshot)
    planned = sum(r["planned"] for r in rows)
    if not planned:
        return NOT_COMPUTABLE, tr("check.no_tasks"), ""
    carried = sum(r["carried_out"] for r in rows)
    return (
        MEASURED,
        tr("check.carry_over_obs", part=carried, whole=planned),
        _sprint_marks([r["sprint"] for r in rows], src),
    )


def first_pass(snapshot: dict, src: Sources, tr: Tr) -> Result:
    """Measured from the backend's effort observations, never a producer's account."""
    rows = performance.sprint_rows(snapshot)
    whole = sum(r["done_observed"] for r in rows)
    if not whole:
        return NOT_COMPUTABLE, tr("check.no_effort"), ""
    part = sum(r["first_pass"] for r in rows)
    entries = [e for r in rows for e in r["efforts"]]
    return (
        MEASURED,
        tr("check.first_pass_obs", part=part, whole=whole),
        _effort_marks(entries, src, tr),
    )


def _completed(snapshot: dict) -> list[dict]:
    return [
        r
        for r in performance.sprint_rows(snapshot)
        if r["sprint"].get("status") == performance.COMPLETED
    ]


def spend_known(snapshot: dict, src: Sources, tr: Tr) -> Result:
    done = _completed(snapshot)
    if not done:
        return NOT_COMPUTABLE, tr("check.lessons_none"), ""
    known = [r for r in done if performance.known_cost(r["cost"])]
    marks = " ".join(
        src.cite(
            ref(
                "cost",
                r["cost"].get("sprint_id"),
                sha256=digest(r["cost"]),
                label=tr("status.label_cost", id=r["cost"].get("sprint_id")),
            )
        )
        for r in done[:MAX_MARKS]
        if r["cost"]
    )
    return (
        (MET if len(known) == len(done) else NOT_MET),
        tr("check.spend_obs", part=len(known), whole=len(done)),
        marks or _sprint_marks([r["sprint"] for r in done], src),
    )


def effort_observed(snapshot: dict, src: Sources, tr: Tr) -> Result:
    done = _completed(snapshot)
    whole = sum(r["planned"] for r in done)
    if not whole:
        return NOT_COMPUTABLE, tr("check.lessons_none"), ""
    part = sum(r["observed"] for r in done)
    entries = [e for r in done for e in r["efforts"]]
    return (
        (MET if part == whole else NOT_MET),
        tr("check.effort_obs", part=part, whole=whole),
        _effort_marks(entries, src, tr)
        or _sprint_marks([r["sprint"] for r in done], src),
    )


def _person(_snapshot: dict, _src: Sources, tr: Tr) -> Result:
    return PERSON, tr("check.person_obs"), ""


CHECKS: dict[str, list[tuple[str, CheckFn]]] = {
    "Governance": [("lessons", lessons_during), ("decided", plan_decided)],
    "Scope": [
        ("understood", requirements_understood),
        ("stability", requirements_stability),
        ("covered", requirements_covered),
        ("first_pass", first_pass),
    ],
    "Schedule": [("schedule", schedule_documented), ("carry_over", carry_over)],
    "Finance": [("budget", _person), ("spend", spend_known)],
    "Stakeholders": [("people", people_recorded), ("external", _person)],
    "Resources": [("team", work_to_hired_roles), ("effort", effort_observed)],
    "Risk": [("risk", risk_on_every_delivery), ("security", security_tests_run)],
}
CHECK_INPUTS = (
    "stack",
    "wstg_observations",
    "wstg_reviews",
    "specification",
    "architecture",
    "sprints",
    "history",
    "roles",
    "members",
    "retrospectives",
    "verifications",
    "events",
    "effort",
    "costs",
    statements.key_of("checks"),
)


def run(snapshot: dict[str, Any], src: Sources | None = None) -> list[dict[str, Any]]:
    """Every check's result, in domain order, as plain data (outcome and method in the snapshot's language)."""
    lang, tr = tr_of(snapshot)
    src = src or Sources(lang)
    out = []
    for domain in DOMAINS:
        for check_id, fn in CHECKS.get(domain, []):
            result, observed, marks = fn(snapshot, src, tr)
            out.append(
                {
                    "id": check_id,
                    "domain": domain,
                    "outcome": tr(f"check.{check_id}"),
                    "check": tr(f"check.{check_id}_how"),
                    "result": result,
                    "observed": observed,
                    "marks": marks,
                }
            )
    return out


def document(snapshot: dict[str, Any]) -> dict[str, Any]:
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    results = run(snapshot, src)
    body = [tr("checks.intro"), "", tr("checks.header"), "|---|---|---|---|---|---|"]
    for r in results:
        body.append(
            f"| {texts.domain(lang, r['domain'])} | {md_inline(r['outcome'])} | {md_inline(r['check'])} | "
            f"{tr('result.' + r['result'])} | {md_inline(r['observed'])} | {r['marks'] or '-'} |"
        )
    missing = [r["outcome"] for r in results if r["result"] == NOT_COMPUTABLE]
    persons = [r["outcome"] for r in results if r["result"] == PERSON]
    filled = statements.filled(snapshot, "checks")
    if filled:
        # A person's statement sits below the table: it never sets a result.
        body += ["", tr("statements_heading"), "", tr("statements_intro")]
        for item in filled:
            heading = tr(statements.SECTIONS[item["section"]][1])
            body += ["", f"### {md_inline(heading)}", ""]
            body += statements.lines(snapshot, item["section"], src)
    return build(
        "checks",
        PMBOK,
        body,
        src,
        snapshot,
        specs.inputs(snapshot, CHECK_INPUTS),
        missing,
        persons=persons,
        statements=filled,
    )
