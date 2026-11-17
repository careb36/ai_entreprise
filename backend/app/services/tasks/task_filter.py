"""A company's tasks filtered on the evidence (issue #535).

``query`` evaluates a parsed filter (``services/contracts/task_filters``)
over a company's tasks and answers ``{"tasks": [...], "count": n}``. The
facts a condition reads come from the same records the board reads:

- ``status``, ``sprint_id``, ``assignee_role`` and ``covers`` from the task's
  row, with a status outside the five read as ``todo`` exactly as the board
  places it (``routers/sprint.py``);
- ``rework_count`` and ``waits_for_person`` from its ``TaskRecord``
  (``task_projection``), the projection every decision reads;
- ``verdict`` and ``risk_tier`` from the Verifier's latest verification of
  the task's latest run, an observation, never what an agent said of its own
  work (``docs/POSITIONING.md``), and ``producer`` from that run.

Nothing here builds SQL from the filter. The query is the company's tasks,
narrowed by bound parameters to the sprints a filter is limited to
(``task_filters.sprint_ids``); every condition is then evaluated in Python
against the facts. Only the facts the filter, the order and the grouping need
are loaded for every task; the rest are loaded for the page returned.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from models import AgentRun, BacklogItem, Sprint, Verification
from sqlmodel import Session, select

from services.contracts import roles, task_filters
from services.tasks import task_projection

DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
_CHUNK = 900

_RECORD_FIELDS = {"rework_count", "waits_for_person"}
_RUN_FIELDS = {"verdict", "risk_tier", "producer"}
# The areas ``services/evidence/risk.classify`` names, ``grader`` first (a
# protected path); ``tests/test_board_observed.py`` keeps them in step.
RISK_AREAS = (
    "grader",
    "secrets",
    "CI pipeline",
    "authentication",
    "payments",
    "shell script",
    "database migration",
    "dependencies",
    "containers and infrastructure",
)
# The Verifier's check of protected paths (``services/evidence/protected_check``).
PROTECTED_CHECK = "protected_paths"
_QUOTED_PATHS = 20


def _chunks(values: list[Any]) -> Iterable[list[Any]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def tasks_of(
    session: Session, company_id: int, sprint_ids: set[int] | None = None
) -> list[BacklogItem]:
    """The company's tasks, only those of *sprint_ids* when given, by id."""
    statement = (
        select(BacklogItem)
        .join(Sprint, BacklogItem.sprint_id == Sprint.id)  # type: ignore[arg-type]
        .where(Sprint.company_id == company_id)
    )
    if sprint_ids is not None:
        if not sprint_ids:
            return []
        statement = statement.where(
            BacklogItem.sprint_id.in_(sorted(sprint_ids))  # type: ignore[attr-defined]
        )
    return list(session.exec(statement.order_by(BacklogItem.id)).all())  # type: ignore[arg-type]


def board_status(status: str | None) -> str:
    """The column the board shows a status in."""
    return status if status in task_filters.STATUSES else "todo"


def _row_facts(item: BacklogItem) -> dict[str, Any]:
    covers = item.covers if isinstance(item.covers, list) else []
    return {
        "id": item.id,
        "sprint_id": item.sprint_id,
        "title": item.title,
        "status": board_status(item.status),
        "priority": item.priority,
        "assignee": item.assignee,
        "assignee_role": roles.canonical(item.assignee) or None,
        "kind": item.kind,
        "covers": [str(c) for c in covers],
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _add_record_facts(
    session: Session, items: list[BacklogItem], facts: dict[int, dict[str, Any]]
) -> None:
    records = task_projection.records_for(session, items)
    for item in items:
        record = records[item.id]
        facts[item.id]["rework_count"] = record.rework_request_count()
        facts[item.id]["waits_for_person"] = record.waits_for_a_person(item.status)


def latest_runs(
    session: Session, company_id: int, task_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Per task: its latest run's producer and its latest verification's verdict
    and risk tier, or ``None`` for each when there is none."""
    refs = {f"task-{task_id}": task_id for task_id in task_ids}
    runs: dict[int, tuple[int, int, str]] = {}
    for chunk in _chunks(list(refs)):
        rows = session.exec(
            select(
                AgentRun.id, AgentRun.external_ref, AgentRun.version, AgentRun.producer
            )
            .where(AgentRun.company_id == company_id)
            .where(AgentRun.external_ref.in_(chunk))  # type: ignore[attr-defined]
        ).all()
        for run_id, ref, version, producer in rows:
            task_id = refs[ref]
            if task_id not in runs or version > runs[task_id][1]:
                runs[task_id] = (run_id, version, producer)
    by_run = {run_id: task_id for task_id, (run_id, _v, _p) in runs.items()}
    verified: dict[int, tuple[int, str, Any]] = {}
    for chunk in _chunks(sorted(by_run)):
        rows = session.exec(
            select(
                Verification.id,
                Verification.agent_run_id,
                Verification.verdict,
                Verification.checks,
            )
            .where(Verification.company_id == company_id)
            .where(Verification.agent_run_id.in_(chunk))  # type: ignore[attr-defined]
        ).all()
        for verification_id, run_id, verdict, checks in rows:
            if run_id not in verified or verification_id > verified[run_id][0]:
                verified[run_id] = (verification_id, verdict, checks)
    out: dict[int, dict[str, Any]] = {}
    for task_id in task_ids:
        run = runs.get(task_id)
        facts: dict[str, Any] = {
            "producer": None,
            "verdict": None,
            "risk_tier": None,
            "risk_areas": [],
            "protected_paths": [],
            "declared_protected_paths": [],
            "run_id": None,
        }
        if run is not None:
            facts["producer"] = run[2]
            facts["run_id"] = run[0]
            seen = verified.get(run[0])
            if seen is not None:
                facts["verdict"] = seen[1]
                facts["risk_tier"] = _tier(seen[2])
                facts["risk_areas"] = _areas(seen[2])
                facts["protected_paths"] = _protected(seen[2], observed=True)
                facts["declared_protected_paths"] = _protected(seen[2], observed=False)
        out[task_id] = facts
    return out


def _tier(checks: Any) -> str | None:
    for check in checks or []:
        if isinstance(check, dict) and check.get("name") == "risk_tier":
            tier = check.get("tier")
            return tier if tier in task_filters.RISK_TIERS else None
    return None


def _areas(checks: Any) -> list[str]:
    """The areas the Verifier's ``risk_tier`` check named (``area: path``).

    Only the areas the classification itself writes (``RISK_AREAS``): a
    reason the producer raised (``raised by the producer: ...``) is its claim
    and never becomes an area here.
    """
    found: list[str] = []
    for check in checks or []:
        if not isinstance(check, dict) or check.get("name") != "risk_tier":
            continue
        for reason in check.get("reasons") or []:
            area = str(reason).partition(":")[0].strip()
            if area in RISK_AREAS and area not in found:
                found.append(area)
    return found


def _protected(checks: Any, *, observed: bool) -> list[str]:
    """The protected paths the Verifier's ``protected_paths`` check matched.

    With *observed*, only the matches the daemon saw written (``observed:
    true``); otherwise the rest, which only the producer declared: a claim. A
    match recorded without the flag is never read as observed.
    """
    paths: list[str] = []
    for check in checks or []:
        if not isinstance(check, dict) or check.get("name") != PROTECTED_CHECK:
            continue
        for match in check.get("matches") or []:
            if not isinstance(match, dict):
                continue
            path = match.get("path")
            if (match.get("observed") is True) != observed:
                continue
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
    return paths[:_QUOTED_PATHS]


def board_observations(
    session: Session, company_id: int, items: list[BacklogItem]
) -> dict[int, dict[str, Any]]:
    """Per task, what the board shows (``GET /v1/sprints/{id}/board``), in two
    fields that never share a value.

    ``observed``: the latest run's id, the Verifier's verdict, risk tier, the
    areas it named and the protected paths the daemon saw written, and
    whether the task waits for a person (its ``TaskRecord``, which holds a
    task for a declared protected path too). ``reported``: the protected
    paths only the producer declared, a claim.
    """
    if not items:
        return {}
    runs = latest_runs(session, company_id, [item.id for item in items])
    records = task_projection.records_for(session, items)
    out: dict[int, dict[str, Any]] = {}
    for item in items:
        facts = runs[item.id]
        out[item.id] = {
            "observed": {
                "run_id": facts["run_id"],
                "verdict": facts["verdict"],
                "risk_tier": facts["risk_tier"],
                "risk_areas": facts["risk_areas"],
                "protected_paths": facts["protected_paths"],
                "waits_for_person": records[item.id].waits_for_a_person(item.status),
            },
            "reported": {"protected_paths": facts["declared_protected_paths"]},
        }
    return out


def _add_run_facts(
    session: Session,
    company_id: int,
    items: list[BacklogItem],
    facts: dict[int, dict[str, Any]],
) -> None:
    for task_id, run_facts in latest_runs(
        session, company_id, [item.id for item in items]
    ).items():
        facts[task_id].update({key: run_facts[key] for key in _RUN_FIELDS})


def _load(
    session: Session,
    company_id: int,
    items: list[BacklogItem],
    facts: dict[int, dict[str, Any]],
    needed: set[str],
) -> None:
    if needed & _RECORD_FIELDS:
        _add_record_facts(session, items, facts)
    if needed & _RUN_FIELDS:
        _add_run_facts(session, company_id, items, facts)


def _sort_key(field: str):
    def key(facts: dict[str, Any]) -> tuple[Any, int]:
        value = facts[field]
        if field == "status":
            value = task_filters.STATUSES.index(value)
        elif field == "risk_tier":
            value = task_filters.RISK_TIERS.index(value)
        return (value, facts["id"])

    return key


def _order(rows: list[dict[str, Any]], order_by: str | None) -> list[dict[str, Any]]:
    if not order_by:
        return rows
    field = order_by.lstrip("-")
    present = [r for r in rows if r.get(field) is not None]
    absent = [r for r in rows if r.get(field) is None]
    present.sort(key=_sort_key(field), reverse=order_by.startswith("-"))
    return present + absent


def _label(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def query(
    session: Session,
    company_id: int,
    tree: dict[str, Any] | None,
    *,
    order_by: str | None = None,
    group_by: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """The company's tasks that pass *tree*, a filter ``parse_filter`` returned.

    ``count`` is every task that passes; ``tasks`` the page of them from
    *offset*, at most *limit*, in *order_by* (by id when none; a task without
    the value last). With *group_by*, ``groups`` counts the passing tasks per
    value (``""`` for none).
    """
    limit = max(0, min(limit, MAX_LIMIT))
    items = tasks_of(session, company_id, task_filters.sprint_ids(tree))
    facts = {item.id: _row_facts(item) for item in items}
    needed = task_filters.fields_of(tree)
    for extra in (order_by.lstrip("-") if order_by else None, group_by):
        if extra:
            needed.add(extra)
    _load(session, company_id, items, facts, needed)
    passing = [facts[i.id] for i in items if task_filters.matches(tree, facts[i.id])]
    ordered = _order(passing, order_by)
    page = ordered[offset : offset + limit]
    missing = (_RECORD_FIELDS | _RUN_FIELDS) - needed
    if page and missing:
        page_ids = {row["id"] for row in page}
        _load(
            session,
            company_id,
            [i for i in items if i.id in page_ids],
            facts,
            missing,
        )
    result: dict[str, Any] = {"tasks": page, "count": len(passing)}
    if group_by:
        groups: dict[str, int] = {}
        for row in passing:
            label = _label(row.get(group_by))
            groups[label] = groups.get(label, 0) + 1
        result["groups"] = groups
    return result
