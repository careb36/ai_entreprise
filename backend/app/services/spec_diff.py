"""Diff between two versions of a specification.

NOTE: requirement/criterion ids are positional (assigned by spec_check.normalize),
so reordering changes ids and shows as add/remove. Identities are not preserved
across edits; this is a known limitation.

Deterministic: requirements, criteria, quality attributes and constraints are
matched by their ids (or by their text for constraints, which carry no id).
A requirement or criterion is ``added`` when its id is only in the newer
version, ``removed`` when it is only in the older one, and ``changed`` when
the id is in both but some field differs. Nothing here is asked of a model.
"""

from __future__ import annotations

from typing import Any


def _reqs(spec: Any) -> list[dict[str, Any]]:
    return list(
        (
            spec.requirements
            if hasattr(spec, "requirements")
            else spec.get("requirements")
        )
        or []
    )


def _quality(spec: Any) -> list[dict[str, Any]]:
    return list(
        (spec.quality if hasattr(spec, "quality") else spec.get("quality")) or []
    )


def _constraints(spec: Any) -> list[str]:
    return list(
        (spec.constraints if hasattr(spec, "constraints") else spec.get("constraints"))
        or []
    )


def _req_map(spec: Any) -> dict[str, dict[str, Any]]:
    return {str(r.get("id")): r for r in _reqs(spec) if r.get("id") is not None}


def _crit_map(req: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(c.get("id")): c
        for c in (req.get("criteria") or [])
        if c.get("id") is not None
    }


def diff_specs(old: Any, new: Any) -> dict[str, Any]:
    old_reqs = _req_map(old)
    new_reqs = _req_map(new)

    added_reqs: list[str] = []
    removed_reqs: list[str] = []
    changed_reqs: list[dict[str, Any]] = []

    added_reqs = sorted(set(new_reqs) - set(old_reqs))
    removed_reqs = sorted(set(old_reqs) - set(new_reqs))
    for rid in sorted(set(old_reqs) & set(new_reqs)):
        o, n = old_reqs[rid], new_reqs[rid]
        changes: list[dict[str, Any]] = []
        for field in ("title", "description"):
            if (o.get(field) or "") != (n.get(field) or ""):
                changes.append(
                    {"field": field, "before": o.get(field), "after": n.get(field)}
                )
        # criteria diff within the requirement
        oc = _crit_map(o)
        nc = _crit_map(n)
        added_c = sorted(set(nc) - set(oc))
        removed_c = sorted(set(oc) - set(nc))
        changed_c = []
        for cid in sorted(set(oc) & set(nc)):
            if (oc[cid].get("given"), oc[cid].get("when"), oc[cid].get("then")) != (
                nc[cid].get("given"),
                nc[cid].get("when"),
                nc[cid].get("then"),
            ):
                changed_c.append(
                    {
                        "id": cid,
                        "before": {
                            "given": oc[cid].get("given"),
                            "when": oc[cid].get("when"),
                            "then": oc[cid].get("then"),
                        },
                        "after": {
                            "given": nc[cid].get("given"),
                            "when": nc[cid].get("when"),
                            "then": nc[cid].get("then"),
                        },
                    }
                )
        if added_c or removed_c or changed_c or changes:
            changed_reqs.append(
                {
                    "id": rid,
                    "fields": changes,
                    "criteria": {
                        "added": added_c,
                        "removed": removed_c,
                        "changed": changed_c,
                    },
                }
            )

    # Quality attributes: matched by id.
    old_q = {str(q.get("id")): q for q in _quality(old) if q.get("id") is not None}
    new_q = {str(q.get("id")): q for q in _quality(new) if q.get("id") is not None}
    added_q = sorted(set(new_q) - set(old_q))
    removed_q = sorted(set(old_q) - set(new_q))
    changed_q = []
    for qid in sorted(set(old_q) & set(new_q)):
        o, n = old_q[qid], new_q[qid]
        if (o.get("attribute"), o.get("target")) != (
            n.get("attribute"),
            n.get("target"),
        ):
            changed_q.append(
                {
                    "id": qid,
                    "before": o.get("attribute"),
                    "after": n.get("attribute"),
                    "target_before": o.get("target"),
                    "target_after": n.get("target"),
                }
            )

    # Constraints: no ids; compare as sets of text.
    old_c = set(_constraints(old))
    new_c = set(_constraints(new))
    added_constraints = sorted(new_c - old_c)
    removed_constraints = sorted(old_c - new_c)

    changed_criteria_ids = set()
    for r in changed_reqs:
        for cid in r["criteria"]["changed"]:
            changed_criteria_ids.add(cid["id"])
        for cid in r["criteria"]["added"]:
            changed_criteria_ids.add(cid)
        for cid in r["criteria"]["removed"]:
            changed_criteria_ids.add(cid)
    # criteria whose requirement was added/removed count as changed too
    for rid in added_reqs:
        for c in new_reqs[rid].get("criteria") or []:
            if c.get("id") is not None:
                changed_criteria_ids.add(str(c["id"]))
    for rid in removed_reqs:
        for c in old_reqs[rid].get("criteria") or []:
            if c.get("id") is not None:
                changed_criteria_ids.add(str(c["id"]))

    return {
        "from_version": getattr(old, "version", None)
        if not isinstance(old, dict)
        else old.get("version"),
        "to_version": getattr(new, "version", None)
        if not isinstance(new, dict)
        else new.get("version"),
        "requirements": {
            "added": added_reqs,
            "removed": removed_reqs,
            "changed": changed_reqs,
        },
        "quality": {
            "added": added_q,
            "removed": removed_q,
            "changed": changed_q,
        },
        "constraints": {
            "added": added_constraints,
            "removed": removed_constraints,
        },
        "changed_criterion_ids": sorted(changed_criteria_ids),
        "changed_requirement_ids": sorted(
            set(added_reqs) | set(removed_reqs) | {r["id"] for r in changed_reqs}
        ),
    }
