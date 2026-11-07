"""Impact of a specification revision on the work already planned or done.

Given a revision (a specification that supersedes another), the criteria and
requirements that changed or disappeared, and the tasks and modules that
cover them, are listed as *affected*. Nothing is mutated here: turning the
list into the next sprint is a separate, explicit step a Manager takes.
"""

from __future__ import annotations

from typing import Any

from models import Architecture, BacklogItem, Specification, Sprint
from sqlmodel import Session, select

from services import spec_diff, spec_planning


def _covers(item: BacklogItem) -> list[str]:
    return spec_planning.covers_of(item)


def _changed_criteria_and_reqs(old: Specification, new: Specification) -> tuple[set[str], set[str]]:
    d = spec_diff.diff_specs(old, new)
    criteria = set(d["changed_criterion_ids"])
    reqs = set(d["changed_requirement_ids"])
    return criteria, reqs


def impact(session: Session, revision: Specification) -> dict[str, Any]:
    """The affected tasks and modules of *revision*'s approval."""
    if revision.supersedes_id is None:
        return {"specification_id": revision.id, "previous_id": None,
                "changed_criterion_ids": [], "changed_requirement_ids": [],
                "affected_tasks": [], "flagged_modules": []}

    previous = session.get(Specification, revision.supersedes_id)
    if previous is None:
        return {"specification_id": revision.id, "previous_id": revision.supersedes_id,
                "changed_criterion_ids": [], "changed_requirement_ids": [],
                "affected_tasks": [], "flagged_modules": []}

    criteria, reqs = _changed_criteria_and_reqs(previous, revision)

    # Tasks covering a changed criterion, on sprints of the previous version.
    affected_tasks: list[dict[str, Any]] = []
    rows = session.exec(
        select(BacklogItem, Sprint)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.specification_id == previous.id)
        .order_by(BacklogItem.id)
    ).all()
    for item, sprint in rows:
        covered = set(_covers(item))
        hit = sorted(covered & criteria)
        if not hit:
            continue
        affected_tasks.append(
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "sprint_id": sprint.id,
                "sprint_name": sprint.sprint_name,
                "sprint_status": sprint.status,
                "changed_criteria": hit,
            }
        )

    # Architectures of the previous version: modules covering a changed req.
    flagged_modules: list[dict[str, Any]] = []
    archs = session.exec(
        select(Architecture).where(
            Architecture.company_id == revision.company_id,
            Architecture.specification_id == previous.id,
        )
    ).all()
    for arch in archs:
        for mod in (arch.modules or []):
            mod_cov = {str(c) for c in (mod.get("covers") or [])}
            hit = sorted(mod_cov & reqs)
            if not hit:
                continue
            flagged_modules.append(
                {
                    "architecture_id": arch.id,
                    "architecture_status": arch.status,
                    "module_id": mod.get("id"),
                    "module_name": mod.get("name"),
                    "changed_requirements": hit,
                }
            )

    return {
        "specification_id": revision.id,
        "previous_id": previous.id,
        "changed_criterion_ids": sorted(criteria),
        "changed_requirement_ids": sorted(reqs),
        "affected_tasks": affected_tasks,
        "flagged_modules": flagged_modules,
    }
