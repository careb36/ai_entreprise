"""The status and work performance report of a pack (issue #530, phase 2).

Where the project stands, from the figures ``performance`` counts: a summary,
progress per sprint, quality and rework per sprint, model spend per sprint,
then a person's forecast. The structure follows the PMBOK Guide, 8th edition
(PMI, 2025), where status and work performance reports serve Monitoring and
Controlling; the wording is our own, in the pack's language (``texts``,
``texts_status_*``).

Every figure cites the records it was counted from: the sprint (its digest),
the task events (their chain hash: a carry, the backend's effort
observation), the Verifier's readings (the run's artifact digest) and the
sprint's reported model usage (the digest of the usage summary the backend
computed). The report reads no retrospective, agent message or delivery text:
its inputs do not name them, so a claim can neither appear in it nor make it
stale. The forecast is a person's section (``statements``): a template, or a
person's statement quoted as a claim, below the figures and never read by
them.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import performance, statements, texts
from services.planning.project_docs.control_docs import (
    cell,
    date,
    event_ref,
    sprint_ref,
)
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.log_docs import verification_ref
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline

MAX_MARKS = 5
FORECAST = "status_report.forecast"
STATUS_INPUTS = (*performance.PERFORMANCE_INPUTS, statements.key_of("status_report"))


def effort_ref(entry: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "event",
        entry.get("event_id"),
        sha256=entry.get("chain_hash"),
        label=texts.translator(lang)(
            "status.label_effort", task=md_id(entry.get("task_id"))
        ),
    )


def cost_ref(cost: dict[str, Any], lang: str) -> dict[str, Any]:
    return ref(
        "cost",
        cost.get("sprint_id"),
        sha256=digest(cost),
        label=texts.translator(lang)("status.label_cost", id=cost.get("sprint_id")),
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
    found = sorted(str(m) for m in moments if m)
    return found[-1] if found else None


def spend_text(tr: Any, cost: dict[str, Any] | None) -> str:
    """A sprint's spend in dollars, or why it is not known; never a zero for unknown."""
    if not cost or not cost.get("turns"):
        return tr("status.spend_none")
    if performance.known_cost(cost):
        return tr("status.spend_usd", usd=performance.usd(cost["cost_cents"]))
    reason = str(cost.get("reason") or "")
    key = f"status.reason.{reason}"
    if key in texts.TEXTS["en"]:
        return tr("status.spend_unknown", reason=tr(key))
    return tr(
        "status.spend_unknown",
        reason=tr("status.reason_other", reason=md_inline(reason, 60)),
    )


def _summary(snapshot, rows, total, src, lang, tr) -> list[str]:
    sprint_marks = _marks(src, [sprint_ref(r["sprint"]) for r in rows])
    carries = [event_ref(e, lang) for r in rows for e in r["carry_events"]]
    readings = [verification_ref(v, lang) for r in rows for v in r["verifications"]]
    efforts = [effort_ref(e, lang) for r in rows for e in r["efforts"]]
    costs = [
        cost_ref(r["cost"], lang) for r in rows if r["cost"] and r["cost"].get("turns")
    ]
    if total["cost_known"]:
        spend = tr(
            "status.spend_value",
            usd=performance.usd(total["cost_cents"]),
            tokens=total["reported_tokens"],
        )
    elif costs:
        unknown = next(
            r["cost"]
            for r in rows
            if r["cost"]
            and r["cost"].get("turns")
            and not performance.known_cost(r["cost"])
        )
        spend = spend_text(tr, unknown)
    else:
        spend = tr("status.spend_none")
    measures = [
        (
            "m_done",
            tr("status.of", part=total["done"], whole=total["planned"]),
            sprint_marks,
        ),
        (
            "m_sprints",
            tr("status.of", part=total["completed"], whole=total["sprints"]),
            sprint_marks,
        ),
        (
            "m_carried",
            tr("status.of", part=total["carried_out"], whole=total["planned"]),
            _marks(src, carries),
        ),
        (
            "m_readings",
            tr(
                "status.readings_value",
                verified=total["readings"]["verified"],
                refuted=total["readings"]["refuted"],
                unverifiable=total["readings"]["unverifiable"],
            ),
            _marks(src, readings),
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
        (
            "m_holds",
            tr(
                "status.holds_value",
                blocks=total["blocks"],
                escalations=total["escalations"],
                holds=total["risk_holds"],
            ),
            _marks(src, [event_ref(e, lang) for r in rows for e in r["hold_events"]]),
        ),
        ("m_spend", spend, _marks(src, costs)),
    ]
    body = [
        tr("status.summary_heading"),
        "",
        tr("status.summary_header"),
        "|---|---|---|---|",
    ]
    for key, value, marks in measures:
        body.append(
            f"| {tr('status.' + key)} | {value} | {tr('status.' + key + '_how')} | {marks} |"
        )
    return body


def _sprint_cell(sprint: dict[str, Any]) -> str:
    return f"{md_id(sprint.get('id'))} {cell(sprint.get('name'), 60)}"


def _progress(rows, src, lang, tr) -> list[str]:
    body = [
        tr("status.progress_heading"),
        "",
        tr("status.progress_header"),
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        s = r["sprint"]
        marks = _marks(
            src, [sprint_ref(s)] + [event_ref(e, lang) for e in r["carry_events"]]
        )
        body.append(
            f"| {_sprint_cell(s)} | {cell(texts.status(lang, s.get('status')))} | "
            f"{date(s.get('first_event_at'))} – {date(s.get('last_event_at'))} | "
            f"{r['done']}/{r['planned']} | {r['blocked']} | {r['carried_out']} | {r['carried_in']} | {marks} |"
        )
    return body


def _quality(rows, src, lang, tr) -> list[str]:
    body = [
        tr("status.quality_heading"),
        "",
        tr("status.quality_header"),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        refs = [verification_ref(v, lang) for v in r["verifications"]]
        refs += [effort_ref(e, lang) for e in r["efforts"]]
        effort = (
            [
                r["observed"],
                f"{r['first_pass']}/{r['done_observed']}",
                r["rework_cycles"],
                r["rework_requests"],
                r["send_backs"],
                r["review_failures"],
            ]
            if r["observed"]
            else ["0", "-", "-", "-", "-", "-"]
        )
        cells = [r["readings"][v] for v in performance.VERDICTS] + [
            r["high_risk"],
            *effort,
        ]
        body.append(
            f"| {_sprint_cell(r['sprint'])} | "
            + " | ".join(str(c) for c in cells)
            + f" | {_marks(src, refs)} |"
        )
    return body


def _spend(rows, src, lang, tr) -> list[str]:
    body = [
        tr("status.spend_heading"),
        "",
        tr("status.spend_header"),
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cost = r["cost"]
        if cost:
            turns = f"{int(cost.get('reported_turns') or 0)} / {int(cost.get('turns') or 0)}"
            tokens = str(int(cost.get("reported_tokens") or 0))
            marks = src.cite(cost_ref(cost, lang))
        else:
            turns, tokens, marks = "-", "-", "-"
        body.append(
            f"| {_sprint_cell(r['sprint'])} | {turns} | {tokens} | {spend_text(tr, cost)} | {marks} |"
        )
    return body


def report(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The status and work performance report from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    rows = performance.sprint_rows(snapshot)
    total = performance.totals(snapshot, rows)
    body = [tr("status.intro"), ""]
    as_of = _as_of(snapshot)
    if as_of:
        body += [tr("status.as_of", date=date(as_of)), ""]
    if not rows:
        body.append(missing_line(lang, tr("status.no_sprints")))
        missing.append(tr("status.section_sprints"))
    else:
        body += _summary(snapshot, rows, total, src, lang, tr)
        body += [""] + _progress(rows, src, lang, tr)
        body += [""] + _quality(rows, src, lang, tr)
        if not total["observed"]:
            body += ["", missing_line(lang, tr("status.no_effort"))]
        body += [""] + _spend(rows, src, lang, tr)
    if not total["observed"]:
        missing.append(tr("status.section_effort"))
    if not any(r["cost"] and r["cost"].get("turns") for r in rows):
        if rows:
            body += ["", missing_line(lang, tr("status.no_spend"))]
        missing.append(tr("status.section_spend"))
    body += ["", f"## {tr('person.forecast')}", ""]
    body += statements.lines(snapshot, FORECAST, src)
    body += ["", tr("status.read_heading"), "", tr("status.read")]
    filled = statements.filled(snapshot, "status_report")
    return build(
        "status_report",
        PMBOK,
        body,
        src,
        snapshot,
        STATUS_INPUTS,
        missing,
        persons=[tr("person.forecast")],
        statements=filled,
    )
