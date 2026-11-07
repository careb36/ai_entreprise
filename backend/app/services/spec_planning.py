"""From an approved specification to tasks, and back: what each task covers.

A sprint planned from a specification gets tasks that each name the
criteria they cover (``BacklogItem.covers``, ids from ``spec_check``). The
PO proposes the split; a deterministic check lists the criteria no task
covers, so nothing is forgotten silently. Each task's prompt then carries
only the requirements it covers (``requirements_context``), and QA judges
the deliverable against the same criteria. ``coverage`` reads the boards
of the sprints planned from a specification and says, per criterion,
whether a task got done, is blocked, is planned or does not exist.
"""

from __future__ import annotations

import json
from typing import Any

from logging_config import logger
from models import BacklogItem, Sprint
from models.specification import Specification
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services import spec_author, spec_check
from services.sprint_planner import (
    _build_team_roster,
    _ensure_execution_team,
    _find_po_agent,
)

PLAN_MAX_TOKENS = 4096
TASK_TITLE_LIMIT = 80
TASK_DESCRIPTION_LIMIT = 500
PRIORITIES = ("critical", "high", "medium", "low")


def spec_content(spec: Specification) -> dict[str, Any]:
    return {
        "title": spec.title,
        "summary": spec.summary,
        "requirements": spec.requirements or [],
        "quality": spec.quality or [],
        "constraints": spec.constraints or [],
        "questions": spec.questions or [],
    }


class PlanRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def approved_specification(
    session: Session, company_id: int, specification_id: int
) -> Specification:
    """The company's approved specification *specification_id*, or why not."""
    spec = session.get(Specification, specification_id)
    if spec is None or spec.company_id != company_id:
        raise PlanRefused(404, "Specification not found")
    if spec.status != "approved":
        raise PlanRefused(
            409,
            f"The specification is {spec.status}; only an approved one is planned from.",
        )
    return spec


def checked_covers(item: dict[str, Any], known: set[str] | None) -> list[str] | None:
    """A task's ``covers`` checked against the specification's criteria ids."""
    covers = item.get("covers")
    if not covers:
        return None
    if known is None:
        raise PlanRefused(
            422, "A task covers criteria, but the sprint names no specification."
        )
    if not isinstance(covers, list):
        raise PlanRefused(422, "covers must be a list of criteria ids")
    unknown = [str(c) for c in covers if str(c) not in known]
    if unknown:
        raise PlanRefused(
            422, f"Unknown criteria for this specification: {', '.join(unknown)}"
        )
    return list(dict.fromkeys(str(c) for c in covers))


# ---------------------------------------------------------------------------
# Planning from the specification
# ---------------------------------------------------------------------------


def plan_prompt(
    po_name: str,
    spec: dict[str, Any],
    scope_size: str,
    item_limit: int,
    roster: str,
    roles: list[str],
    note: str = "",
) -> str:
    note_section = (
        f"## Note from the person planning\n{note.strip()[:2000]}\n\n"
        if note.strip()
        else ""
    )
    return (
        f"You are {po_name}, the PO. Plan a sprint from the specification below: "
        "split the work into tasks a single agent can deliver, and say for each task "
        "which acceptance criteria it covers, by id. Every criterion must be covered "
        "by at least one task of this sprint or a later one; cover the most valuable "
        "ones first.\n\n"
        f"## Specification: {spec.get('title')}\n{spec.get('summary')}\n\n"
        f"{spec_check.as_prompt(spec)}\n\n"
        f"{note_section}"
        f"## Team\n{roster}\n\n"
        f"Scope {scope_size}: at most {item_limit} tasks. Assignee is one of: {', '.join(roles)}.\n\n"
        "Answer with one JSON object and nothing else:\n"
        '{"sprint_name": "...", "goal": "...", "tasks": [{"title": "...", '
        '"description": "what to build and how it will be checked", '
        '"priority": "critical|high|medium|low", "assignee": "Developer", '
        '"covers": ["REQ-1.1", "REQ-1.2"]}]}'
    )


def _validate_tasks(
    tasks: Any, known: set[str], roles: set[str], item_limit: int
) -> list[dict[str, Any]]:
    validated = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict) or not task.get("title"):
            continue
        covers = task.get("covers") if isinstance(task.get("covers"), list) else []
        assignee = str(task.get("assignee") or "Developer")
        validated.append(
            {
                "title": str(task["title"])[:TASK_TITLE_LIMIT],
                "description": str(task.get("description") or "")[
                    :TASK_DESCRIPTION_LIMIT
                ],
                "priority": task.get("priority")
                if task.get("priority") in PRIORITIES
                else "medium",
                "assignee": assignee if assignee in roles else "Developer",
                "covers": list(
                    dict.fromkeys(str(c) for c in covers if str(c) in known)
                ),
            }
        )
    return validated[:item_limit]


def uncovered(spec: dict[str, Any], tasks: list[dict[str, Any]]) -> list[str]:
    """The criteria of *spec* no task in *tasks* covers, in the specification's order."""
    covered = {c for task in tasks for c in task.get("covers") or []}
    return [cid for cid in spec_check.criterion_ids(spec) if cid not in covered]


def plan_fallback(spec: dict[str, Any], item_limit: int) -> dict[str, Any]:
    """One task per requirement, in order, when no model answers."""
    tasks = [
        {
            "title": f"{r['id']}: {r.get('title', '')}"[:TASK_TITLE_LIMIT],
            "description": (
                f"{r.get('description', '')}\n\nCovers: "
                + ", ".join(c["id"] for c in r.get("criteria") or [])
            )[:TASK_DESCRIPTION_LIMIT],
            "priority": "high" if index < 3 else "medium",
            "assignee": "Developer",
            "covers": [c["id"] for c in r.get("criteria") or []],
        }
        for index, r in enumerate(spec.get("requirements") or [])
    ][:item_limit]
    return {
        "sprint_name": f"Sprint - {spec.get('title', '')[:40]}".strip(),
        "goal": (spec.get("summary") or spec.get("title") or "")[:140],
        "tasks": tasks,
        "uncovered": uncovered(spec, tasks),
    }


def plan(
    session: Session,
    spec: Specification,
    scope_size: str,
    item_limit: int,
    agents: list[dict[str, Any]],
    note: str = "",
) -> dict[str, Any]:
    """The PO's split of *spec* into tasks with ``covers``, and what stays uncovered.

    *note* is what the person planning added for the PO. A failed or
    unusable answer falls back to one task per requirement.
    """
    content = spec_content(spec)
    team = _ensure_execution_team(agents)
    roles = sorted({a.get("role", "Developer") for a in team})
    model, system_prompt, po_name = spec_author.po_profile(session, spec.company_id)
    po_name = _find_po_agent(team).get("name") or po_name
    from services import arch_author

    prompt = plan_prompt(
        po_name, content, scope_size, item_limit, _build_team_roster(team), roles, note
    ) + arch_author.planning_context(session, spec.id)
    try:
        answer = spec_author.ask(
            model,
            system_prompt,
            prompt,
            max_tokens=PLAN_MAX_TOKENS,
            bu_id=spec_author.business_unit_id(session, spec.company_id),
        )
        parsed = json.loads(spec_author._strip_fences(answer))
        if not isinstance(parsed, dict):
            raise TypeError("not an object")
    except (spec_author.SpecAuthorError, ValueError, TypeError) as exc:
        logger.warning("PO could not plan from specification %s: %s", spec.id, exc)
        return plan_fallback(content, item_limit)
    tasks = _validate_tasks(
        parsed.get("tasks"),
        set(spec_check.criterion_ids(content)),
        set(roles),
        item_limit,
    )
    if not tasks:
        return plan_fallback(content, item_limit)
    return {
        "sprint_name": str(parsed.get("sprint_name") or f"Sprint - {spec.title[:40]}")[
            :70
        ],
        "goal": str(parsed.get("goal") or spec.summary or spec.title)[:140],
        "tasks": tasks,
        "uncovered": uncovered(content, tasks),
    }


# ---------------------------------------------------------------------------
# What a task covers, in its prompts and on the board
# ---------------------------------------------------------------------------


def covers_of(item: Any) -> list[str]:
    """The criteria ids a backlog item (row or dict) covers."""
    covers = (
        item.get("covers") if isinstance(item, dict) else getattr(item, "covers", None)
    )
    return [str(c) for c in covers] if isinstance(covers, list) else []


def requirements_section(spec: dict[str, Any], covers: list[str]) -> str:
    """The prompt section for a task covering *covers*.

    A task that covers no criterion still gets the quality attributes and
    the constraints, which hold for every task of the specification.
    """
    body = spec_check.as_prompt(spec, set(covers))
    if not body:
        return ""
    if not covers:
        return (
            "\n\n## Specification rules for every task\n"
            "From the approved specification: the quality attributes and constraints "
            "every task follows, this one included.\n\n" + body
        )
    return (
        "\n\n## Requirements this task covers\n"
        "From the approved specification. Each criterion below must hold when the "
        "task is done, as a test can observe it; the review judges the deliverable "
        "against them.\n\n" + body
    )


def requirements_for(session: Session, sprint: Sprint | None, item: Any) -> str:
    """The requirements and architecture sections for *item* of *sprint*.

    Empty without a specification; the architecture part only once one is
    approved for it (``services.arch_author``).
    """
    from services import arch_author

    if sprint is None or sprint.specification_id is None:
        return ""
    spec = session.get(Specification, sprint.specification_id)
    if spec is None:
        return ""
    return requirements_section(
        spec_content(spec), covers_of(item)
    ) + arch_author.context_for(session, sprint, item)


def requirements_context(engine: Engine, item_id: int) -> str:
    """The requirements section for the task *item_id*, read from the database."""
    try:
        with Session(engine) as session:
            item = session.get(BacklogItem, item_id)
            if item is None:
                return ""
            return requirements_for(session, session.get(Sprint, item.sprint_id), item)
    except Exception as exc:  # noqa: BLE001 - Requirements are context; the task goes on without them.
        logger.debug("Requirements fetch failed for item=%s: %s", item_id, exc)
        return ""


def coverage(session: Session, spec: Specification) -> dict[str, Any]:
    """Per criterion, the tasks that cover it on the boards and where they stand.

    A criterion is ``done`` when one of its tasks got done; otherwise its
    latest task decides (a retry carried into a later sprint supersedes the
    blocked attempt): ``blocked``, or ``planned``; with no task, ``uncovered``.
    """
    rows = session.exec(
        select(BacklogItem, Sprint.id)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.specification_id == spec.id)
        .order_by(BacklogItem.id)
    ).all()
    by_criterion: dict[str, list[dict[str, Any]]] = {}
    for item, sprint_id in rows:
        for cid in covers_of(item):
            by_criterion.setdefault(cid, []).append(
                {
                    "id": item.id,
                    "sprint_id": sprint_id,
                    "title": item.title,
                    "status": item.status,
                }
            )
    criteria = []
    for r in spec.requirements or []:
        for c in r.get("criteria") or []:
            tasks = by_criterion.get(c["id"], [])
            if any(t["status"] == "done" for t in tasks):
                status = "done"
            elif tasks:
                status = "blocked" if tasks[-1]["status"] == "blocked" else "planned"
            else:
                status = "uncovered"
            criteria.append(
                {
                    "id": c["id"],
                    "requirement_id": r["id"],
                    "tasks": tasks,
                    "status": status,
                }
            )
    return {
        "criteria": criteria,
        "total": len(criteria),
        "covered": sum(1 for c in criteria if c["status"] != "uncovered"),
        "done": sum(1 for c in criteria if c["status"] == "done"),
    }
