"""The final report of a pack (issue #530, phase 2).

What the project delivered and what it leaves behind, compiled at closure
from the figures ``closure``, ``performance`` and ``iterations`` count: a
summary, the delivery against each requirement, the measured outcomes, the
releases cut, the holds and risks left open, a pointer to the lessons
learned register, then a person's sections for the sponsor's acceptance, the
benefits outlook and the handover. The structure follows the PMBOK Guide,
8th edition (PMI, 2025), where the final report closes the project in the
Governance performance domain and its Closing focus area; the wording is our
own, in the pack's language (``texts``, ``texts_final_*``).

Every figure cites the records it was counted from: the specification and
requirement (their digests), the task (its digest), the Verifier's reading
(the run's artifact digest), the sprint (its digest), the task event (its
chain hash), the sprint's reported model usage (the digest of the usage
summary the backend computed) and the release (the chain hash of the audit
row the backend wrote when a Manager published it). The report reads no
retrospective, procedure or agent message: its inputs do not name them, so a
claim can neither appear in it nor make it stale, and the lessons are left
to their register. What only a person can say at closure is a person's
section (``statements``): a template, or a person's statement quoted as a
claim, below the records and never read by them.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import (
    closure,
    iterations,
    performance,
    specs,
    statements,
    texts,
)
from services.planning.project_docs.control_docs import (
    cell,
    date,
    event_ref,
    sprint_ref,
)
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.log_docs import verification_ref
from services.planning.project_docs.refs import Sources
from services.planning.project_docs.release_docs import release_ref
from services.planning.project_docs.sanitize import md_id
from services.planning.project_docs.scope_docs import (
    requirement_ref,
    spec_ref,
    task_ref,
)
from services.planning.project_docs.status_docs import (
    cost_ref,
    effort_ref,
    spend_total,
)

MAX_MARKS = 5
PERSON_SECTIONS = (
    "final_report.acceptance",
    "final_report.benefits",
    "final_report.handover",
)
FINAL_INPUTS = (
    *closure.CLOSURE_INPUTS,
    "effort",
    "costs",
    statements.key_of("final_report"),
)


def _marks(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "-"
    out = src.cite(*refs[:MAX_MARKS])
    return out + (f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else "")


def _as_of(snapshot: dict[str, Any]) -> str | None:
    moments = [s.get("last_event_at") for s in snapshot.get("sprints") or []]
    moments += [v.get("observed_at") for v in snapshot.get("verifications") or []]
    moments += [e.get("created_at") for e in snapshot.get("effort") or []]
    moments += [r.get("published_at") for r in iterations.releases(snapshot)]
    found = sorted(str(m) for m in moments if m)
    return found[-1] if found else None


def _hold_refs(lang: str, holds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event_ref(h["event"], lang) for h in holds]


def _summary(snapshot, total, src, lang, tr) -> list[str]:
    sprints = snapshot.get("sprints") or []
    measures = [
        (
            "m_delivered",
            tr("final.of", part=total["delivered"], whole=total["requirements"]),
            _marks(src, [spec_ref(s, lang) for s in specs.documented(snapshot)]),
        ),
        (
            "m_closed",
            tr("final.of", part=total["closed"], whole=total["iterations"]),
            _marks(src, [sprint_ref(s) for s in sprints]),
        ),
        (
            "m_releases",
            str(total["releases"]),
            _marks(src, [release_ref(r, lang) for r in iterations.releases(snapshot)]),
        ),
        (
            "m_holds",
            str(total["open_holds"]),
            _marks(src, _hold_refs(lang, closure.open_holds(snapshot))),
        ),
        (
            "m_risks",
            str(total["open_risks"]),
            _marks(
                src, [verification_ref(v, lang) for v in closure.open_risks(snapshot)]
            ),
        ),
    ]
    body = [
        tr("final.summary_heading"),
        "",
        tr("final.summary_header"),
        "|---|---|---|---|",
    ]
    for key, value, marks in measures:
        body.append(
            f"| {tr('final.' + key)} | {value} | {tr('final.' + key + '_how')} | {marks} |"
        )
    return body


def _outcome(row: dict[str, Any], tr) -> str:
    if row["outcome"] == closure.DELIVERED:
        return tr("final.outcome.delivered")
    return tr("final.outcome." + str(row["why"]))


def _delivery_of(snapshot, spec, src, lang, tr) -> list[str]:
    body = [tr("final.delivery_header"), "|---|---|---|---|---|---|"]
    for row in closure.requirement_outcomes(snapshot, spec):
        refs = [requirement_ref(spec, row["requirement"], lang)]
        refs += [task_ref(t, s.get("id"), lang) for s, t in row["done"]]
        refs += [verification_ref(v, lang) for v in row["readings"]]
        done = ", ".join(f"T-{md_id(t.get('id'))}" for _, t in row["done"])
        readings = ", ".join(
            tr(
                "final.reading",
                task=md_id(v.get("task_id")),
                verdict=texts.status(lang, v.get("verdict")),
            )
            for v in row["readings"]
        )
        body.append(
            f"| {md_id(row['id'])} | {cell(row['requirement'].get('title'), 80)} | "
            f"{_outcome(row, tr)} | {done or '-'} | {readings or '-'} | "
            f"{_marks(src, refs)} |"
        )
    return body


def _delivery(snapshot, src, missing, lang, tr) -> list[str]:
    listed = specs.documented(snapshot)
    body = [tr("final.delivery_heading"), ""]
    if not listed:
        missing.append(tr("final.section_requirements"))
        return body + [missing_line(lang, tr("no_spec"))]
    body += [tr("final.delivery_note"), ""]
    if len(listed) == 1:
        return body + _delivery_of(snapshot, listed[0], src, lang, tr)
    body += specs.intro(snapshot, src)
    for spec in listed:
        body += [specs.heading(spec, src.cite(spec_ref(spec, lang)), lang, 3), ""]
        body += [*_delivery_of(snapshot, spec, src, lang, tr), ""]
    return body[:-1]


def _outcomes(snapshot, src, missing, lang, tr) -> list[str]:
    body = [tr("final.outcomes_heading"), ""]
    rows = performance.sprint_rows(snapshot)
    if not rows:
        missing.append(tr("final.section_sprints"))
        return body + [missing_line(lang, tr("status.no_sprints"))]
    total = performance.totals(snapshot, rows)
    efforts = [effort_ref(e, lang) for r in rows for e in r["efforts"]]
    costs = [
        cost_ref(r["cost"], lang) for r in rows if r["cost"] and r["cost"].get("turns")
    ]
    measures = [
        (
            "m_done",
            tr("status.of", part=total["done"], whole=total["planned"]),
            _marks(src, [sprint_ref(r["sprint"]) for r in rows]),
        ),
        (
            "m_carried",
            tr("status.of", part=total["carried_out"], whole=total["planned"]),
            _marks(src, [event_ref(e, lang) for r in rows for e in r["carry_events"]]),
        ),
        (
            "m_readings",
            tr(
                "status.readings_value",
                verified=total["readings"]["verified"],
                refuted=total["readings"]["refuted"],
                unverifiable=total["readings"]["unverifiable"],
            ),
            _marks(
                src,
                [verification_ref(v, lang) for r in rows for v in r["verifications"]],
            ),
        ),
        (
            "m_first_pass",
            tr(
                "check.first_pass_obs",
                part=total["first_pass"],
                whole=total["done_observed"],
            ),
            _marks(src, efforts),
        ),
        (
            "m_rework",
            tr(
                "status.rework_value",
                cycles=total["rework_cycles"],
                requests=total["rework_requests"],
                send_backs=total["send_backs"],
                failures=total["review_failures"],
                tasks=total["observed"],
            ),
            _marks(src, efforts),
        ),
        ("m_spend", spend_total(tr, rows, total), _marks(src, costs)),
    ]
    body += [
        tr("final.outcomes_note"),
        "",
        tr("status.summary_header"),
        "|---|---|---|---|",
    ]
    for key, value, marks in measures:
        body.append(
            f"| {tr('status.' + key)} | {value} | {tr('status.' + key + '_how')} | {marks} |"
        )
    if not total["observed"]:
        body += ["", missing_line(lang, tr("status.no_effort"))]
    return body


def _releases(snapshot, src, missing, lang, tr) -> list[str]:
    found = iterations.releases(snapshot)
    body = [tr("final.releases_heading"), ""]
    if not found:
        missing.append(tr("final.section_releases"))
        return body + [missing_line(lang, tr("final.no_releases"))]
    body += [tr("final.releases_header"), "|---|---|---|---|---|"]
    for r in found:
        how = str(r.get("how") or "")
        key = f"release.how.{how}"
        shown = tr(key) if key in texts.TEXTS["en"] else cell(how, 20)
        commit = str(r.get("published_commit") or "")[:12]
        body.append(
            f"| {md_id(r.get('sprint_id'))} | {f'`{md_id(commit)}`' if commit else '-'} | "
            f"{shown} | {date(r.get('published_at'))} | {src.cite(release_ref(r, lang))} |"
        )
    return body


def _left_open(snapshot, src, lang, tr) -> list[str]:
    holds, risks = closure.open_holds(snapshot), closure.open_risks(snapshot)
    body = [tr("final.open_heading"), "", tr("final.open_note"), ""]
    if not holds and not risks:
        return body + [tr("final.open_none")]
    body += [tr("final.open_header"), "|---|---|---|---|---|"]
    for hold in holds:
        event = hold["event"]
        body.append(
            f"| {texts.status(lang, event.get('kind'))} | T-{md_id(event.get('task_id'))} | "
            f"{date(event.get('created_at'))} | {cell(texts.status(lang, hold['status']))} | "
            f"{src.cite(event_ref(event, lang))} |"
        )
    status_of = {t.get("id"): t.get("status") for _, t in specs.tasks_of(snapshot)}
    for item in risks:
        body.append(
            f"| {tr('final.high_risk')} | T-{md_id(item.get('task_id'))} | "
            f"{date(item.get('observed_at'))} | "
            f"{cell(texts.status(lang, status_of.get(item.get('task_id'))))} | "
            f"{src.cite(verification_ref(item, lang))} |"
        )
    return body


def report(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The final report from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    total = closure.totals(snapshot)
    body = [tr("final.intro"), ""]
    as_of = _as_of(snapshot)
    if as_of:
        body += [tr("final.as_of", date=date(as_of)), ""]
    still_open = closure.open_iterations(snapshot)
    if still_open:
        ids = ", ".join(md_id(s.get("id")) for s in still_open)
        body += [tr("final.interim", count=len(still_open), ids=ids), ""]
    body += _summary(snapshot, total, src, lang, tr)
    body += [""] + _delivery(snapshot, src, missing, lang, tr)
    body += [""] + _outcomes(snapshot, src, missing, lang, tr)
    body += [""] + _releases(snapshot, src, missing, lang, tr)
    body += [""] + _left_open(snapshot, src, lang, tr)
    body += ["", tr("final.lessons_heading"), "", tr("final.lessons")]
    persons = []
    for section in PERSON_SECTIONS:
        heading = tr(statements.SECTIONS[section][1])
        persons.append(heading)
        body += ["", f"## {heading}", ""]
        body += statements.lines(snapshot, section, src)
    body += ["", tr("final.read_heading"), "", tr("final.read")]
    return build(
        "final_report",
        PMBOK,
        body,
        src,
        snapshot,
        specs.inputs(snapshot, FINAL_INPUTS),
        missing,
        persons=persons,
        statements=statements.filled(snapshot, "final_report"),
    )
