"""More computed checks per performance domain (issue #692).

Five checks, appended to ``checks.CHECKS`` with the same signature
(``(snapshot, sources, translator) -> (result, observed, marks)``), each
decided from records the platform wrote itself and never from a model:

- ``approved_first`` (Governance, met or not met): the sprints planned from a
  specification started (their first task event) after that specification
  version was approved (``history``).
- ``churn`` (Scope, measured): the tasks that joined a sprint after it was
  planned, accepted from an intake request (``check_basis.intake_tasks``),
  over the sprints' tasks; how much a project accepts is the Owner's.
- ``on_time`` (Schedule, measured): the completed sprints whose last task
  event falls on or before the last day their start date and planned length
  give (``check_basis.sprint_lengths``); a sprint without a length is
  counted apart, never given one.
- ``quota`` (Finance, measured): the tokens counted against the business
  units' configured token quota this period (``check_basis.quotas``). It is
  the only spending limit the records hold; a budget in money is not
  recorded and stays a person's section (``budget``).
- ``sprint_team`` (Resources, met or not met): the tasks of the sprints that
  record a team go to a role on that team.

The result names are ``checks``' own (the tests hold them equal); they are
repeated here so ``checks`` can import this module. Pure; no I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from services.contracts import roles as role_catalogue
from services.planning.project_docs.collect_checks import KEY
from services.planning.project_docs.control_docs import event_ref
from services.planning.project_docs.refs import Sources, digest, ref

MET, NOT_MET, MEASURED, NOT_COMPUTABLE = "met", "not met", "measured", "not computable"
MAX_MARKS = 5
COMPLETED = "completed"


def _cite(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return ""
    more = f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else ""
    return src.cite(*refs[:MAX_MARKS]) + more


def _sprint_ref(sprint: dict[str, Any]) -> dict[str, Any]:
    """The same reference ``checks`` cites for a sprint (its record without tasks)."""
    return ref(
        "sprint",
        sprint.get("id"),
        sha256=digest({k: v for k, v in sprint.items() if k != "tasks"}),
        label=sprint.get("name") or "",
    )


def _basis(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    value = snapshot.get(KEY)
    return value if isinstance(value, dict) else None


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> date | None:
    moment = _moment(value)
    return moment.date() if moment else None


def approved_first(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    sprints = snapshot.get("sprints") or []
    planned = [s for s in sprints if s.get("specification_id") is not None]
    started = [s for s in planned if _moment(s.get("first_event_at"))]
    if not started:
        return NOT_COMPUTABLE, tr("check.approved_first_none"), ""
    versions = {
        h.get("id"): h
        for h in snapshot.get("history") or []
        if h.get("kind") == "specification"
    }
    after = before = unknown = 0
    refs = []
    for sprint in started:
        version = versions.get(sprint["specification_id"])
        refs.append(_sprint_ref(sprint))
        if version is None:
            unknown += 1
            continue
        refs.append(ref("specification", version.get("id"), sha256=digest(version)))
        approved = _moment(version.get("approved_at"))
        if approved is not None and approved <= _moment(sprint["first_event_at"]):
            after += 1
        else:
            before += 1
    marks = _cite(src, list({digest(r): r for r in refs}.values()))
    if before == 0 and unknown:
        observed = tr("check.approved_first_unknown", part=unknown, whole=len(started))
        return NOT_COMPUTABLE, observed, marks
    observed = tr(
        "check.approved_first_obs",
        part=after,
        whole=len(started),
        unplanned=len(sprints) - len(planned),
    )
    return (MET if before == 0 else NOT_MET), observed, marks


def churn(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    found = _basis(snapshot)
    if found is None:
        return NOT_COMPUTABLE, tr("check.basis_not_read"), ""
    tasks = {
        t.get("id") for s in snapshot.get("sprints") or [] for t in s.get("tasks") or []
    }
    if not tasks:
        return NOT_COMPUTABLE, tr("check.no_tasks"), ""
    added = [e for e in found.get("intake_tasks") or [] if e.get("task_id") in tasks]
    refs = [
        event_ref(
            {
                "id": e.get("event_id"),
                "task_id": e.get("task_id"),
                "kind": "note",
                "actor": "system",
                "chain_hash": e.get("chain_hash"),
            },
            src.lang,
        )
        for e in added
    ]
    marks = _cite(src, refs) or _cite(
        src, [_sprint_ref(s) for s in snapshot.get("sprints") or []]
    )
    observed = tr("check.churn_obs", part=len(added), whole=len(tasks))
    return MEASURED, observed, marks


def on_time(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    found = _basis(snapshot)
    if found is None:
        return NOT_COMPUTABLE, tr("check.basis_not_read"), ""
    done = [s for s in snapshot.get("sprints") or [] if s.get("status") == COMPLETED]
    if not done:
        return NOT_COMPUTABLE, tr("check.lessons_none"), ""
    lengths = {
        r.get("sprint_id"): r.get("duration_days")
        for r in found.get("sprint_lengths") or []
    }
    kept, refs = 0, []
    timed = []
    for sprint in done:
        days = lengths.get(sprint.get("id"))
        start, closed = (
            _day(sprint.get("start_date")),
            _day(sprint.get("last_event_at")),
        )
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            continue
        if start is None or closed is None:
            continue
        timed.append(sprint)
        # A sprint of one day ends on its start date.
        kept += closed <= start + timedelta(days=days - 1)
        refs.append(_sprint_ref(sprint))
        refs.append(
            ref(
                "sprint",
                sprint.get("id"),
                sha256=digest({"sprint_id": sprint.get("id"), "duration_days": days}),
                label=tr("check.label_length", days=days),
            )
        )
    if not timed:
        return NOT_COMPUTABLE, tr("check.on_time_none"), ""
    observed = tr(
        "check.on_time_obs",
        part=kept,
        whole=len(timed),
        unplanned=len(done) - len(timed),
    )
    return MEASURED, observed, _cite(src, refs)


def quota(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    found = _basis(snapshot)
    if found is None:
        return NOT_COMPUTABLE, tr("check.basis_not_read"), ""
    units = [q for q in found.get("quotas") or [] if (q.get("limit") or 0) > 0]
    if not units:
        return NOT_COMPUTABLE, tr("check.quota_none"), ""
    refs = [ref("quota", q.get("unit_id"), sha256=q.get("sha256")) for q in units]
    observed = tr(
        "check.quota_obs",
        used=sum(q.get("used") or 0 for q in units),
        limit=sum(q["limit"] for q in units),
        units=len(units),
        reached=sum(1 for q in units if (q.get("used") or 0) >= q["limit"]),
    )
    return MEASURED, observed, _cite(src, refs)


def sprint_team(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    teamed = [s for s in snapshot.get("sprints") or [] if s.get("team")]
    if not teamed:
        return NOT_COMPUTABLE, tr("check.sprint_team_none"), ""
    whole = inside = 0
    for sprint in teamed:
        team = {role_catalogue.canonical(r) for r in sprint["team"]}
        for task in sprint.get("tasks") or []:
            whole += 1
            inside += role_catalogue.canonical(task.get("assignee")) in team
    marks = _cite(src, [_sprint_ref(s) for s in teamed])
    if not whole:
        return NOT_COMPUTABLE, tr("check.no_tasks"), marks
    observed = tr("check.sprint_team_obs", part=inside, whole=whole)
    return (MET if inside == whole else NOT_MET), observed, marks
