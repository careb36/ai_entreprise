"""A task's difficulty, estimated from its plan before it runs (issue #319).

The estimate is a pure function of what is known before a task runs, never a
model's opinion of it:

- ``criteria``: the acceptance criteria the task covers (``covers``);
- ``modules`` / ``files``: the modules of the approved architecture that cover
  one of those requirements, and the paths they map to;
- ``dependencies``: the tasks it waits for (``depends_on``);
- ``attempt``: which attempt this is (deliveries so far, plus one);
- ``rework_requests`` / ``send_backs``: the ``NEEDS WORK:`` requests on its
  board and a person's send-backs, its rework history.

Each input adds a fixed weight, recorded beside the score, so the estimate is
explainable (``weights``) and the same plan always gives the same number. The
score is 1..5; the band is ``low`` (1-2), ``medium`` (3) or ``high`` (4-5).

With only the criteria and whether it is a rework (no architecture, no
dependencies), the score is the one ``model_routing.estimate_difficulty``
always gave, so the Architect/editor threshold reads the same numbers as
before.

The estimate is an observation of the plan: the task worker stores it on the
task as an ``estimated`` event (``services/tasks/task_events.py``) with the
routing it led to, and on the run's evidence beside the served model.
"""

from __future__ import annotations

from typing import Any

VERSION = 1
MIN_SCORE, MAX_SCORE = 1, 5
LOW, MEDIUM, HIGH = "low", "medium", "high"
BANDS: tuple[str, ...] = (LOW, MEDIUM, HIGH)
# The scores each band holds, inclusive.
BAND_RANGES: dict[str, tuple[int, int]] = {LOW: (1, 2), MEDIUM: (3, 3), HIGH: (4, 5)}
# The caps and thresholds of each input's weight.
MAX_CRITERIA_WEIGHT = 3
MAX_MODULE_WEIGHT = 2
MANY_FILES = 3
MANY_DEPENDENCIES = 2


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _requirement(criterion: Any) -> str:
    """``REQ-1.2`` → ``REQ-1``: the requirement a criterion belongs to."""
    return str(criterion).rsplit(".", 1)[0]


def mapped_modules(
    arch: dict[str, Any] | None, covers: list[Any]
) -> tuple[list[str], list[str]]:
    """The ids of the architecture's modules covering any of *covers*, and their paths."""
    if not isinstance(arch, dict):
        return [], []
    wanted = {str(c) for c in covers} | {_requirement(c) for c in covers}
    modules: list[str] = []
    files: list[str] = []
    for number, module in enumerate(_list(arch.get("modules")), 1):
        if not isinstance(module, dict):
            continue
        if not wanted & {str(c) for c in _list(module.get("covers"))}:
            continue
        modules.append(str(module.get("id") or f"MOD-{number}"))
        path = module.get("path")
        if isinstance(path, str) and path.strip() and path.strip() not in files:
            files.append(path.strip())
    return modules, files


def plan_inputs(
    item: Any,
    arch: dict[str, Any] | None = None,
    *,
    attempt: int = 1,
    rework: bool = False,
    rework_requests: int = 0,
    send_backs: int = 0,
    dependencies: int | None = None,
) -> dict[str, Any]:
    """What the estimate reads, from the task (a dict or a ``BacklogItem``).

    *dependencies* defaults to the length of the task's ``depends_on``.
    *rework* says the attempt carries rework feedback even when the counts
    do not show it (the worker's own reading of the thread).
    """
    covers = _list(_field(item, "covers"))
    modules, files = mapped_modules(arch, covers)
    if dependencies is None:
        dependencies = len(_list(_field(item, "depends_on")))
    return {
        "criteria": len(covers),
        "requirements": len({_requirement(c) for c in covers}),
        "modules": modules,
        "files": files,
        "dependencies": max(0, int(dependencies)),
        "attempt": max(1, int(attempt)),
        "rework": bool(rework),
        "rework_requests": max(0, int(rework_requests)),
        "send_backs": max(0, int(send_backs)),
    }


def weights(inputs: dict[str, Any]) -> dict[str, int]:
    """Each input's share of the score."""
    reworked = (
        inputs.get("rework")
        or inputs.get("attempt", 1) > 1
        or inputs.get("rework_requests", 0) > 0
        or inputs.get("send_backs", 0) > 0
    )
    return {
        "base": MIN_SCORE,
        "criteria": min(int(inputs.get("criteria", 0)), MAX_CRITERIA_WEIGHT),
        "modules": min(len(inputs.get("modules") or []), MAX_MODULE_WEIGHT),
        "files": int(len(inputs.get("files") or []) >= MANY_FILES),
        "dependencies": int(int(inputs.get("dependencies", 0)) >= MANY_DEPENDENCIES),
        "rework": int(bool(reworked)),
        "send_back": int(int(inputs.get("send_backs", 0)) > 0),
    }


def band_of_score(score: int) -> str:
    """``low``, ``medium`` or ``high`` for a 1..5 score."""
    for band, (lo, hi) in BAND_RANGES.items():
        if lo <= score <= hi:
            return band
    return HIGH if score > MAX_SCORE else LOW


def estimate(inputs: dict[str, Any]) -> dict[str, Any]:
    """``{score, band, inputs, weights, version}`` for *inputs* (``plan_inputs``)."""
    parts = weights(inputs)
    score = max(MIN_SCORE, min(MAX_SCORE, sum(parts.values())))
    return {
        "score": score,
        "band": band_of_score(score),
        "inputs": dict(inputs),
        "weights": parts,
        "version": VERSION,
    }


def estimate_item(
    item: Any, arch: dict[str, Any] | None = None, **counts: Any
) -> dict[str, Any]:
    """``estimate(plan_inputs(item, arch, **counts))``."""
    return estimate(plan_inputs(item, arch, **counts))


def stored_score(value: Any) -> int | None:
    """The score of a stored estimate (an ``estimated`` event's ``difficulty``)."""
    score = value.get("score") if isinstance(value, dict) else None
    if isinstance(score, bool) or not isinstance(score, int):
        return None
    return score if MIN_SCORE <= score <= MAX_SCORE else None
