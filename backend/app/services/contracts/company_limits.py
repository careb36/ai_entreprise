"""A company's limits on its agents' work, as data (issue #766).

``companies.limits`` is ``null`` (the deployment's values, as before
migration 0061) or an object with some of ``LIMITS``:

- ``tool_rounds``: the Developer's tool rounds per call;
- ``result_max_chars``: the most of one tool's result the model sees;
- ``max_output_tokens``: the cap on the Developer's answer (``0``: none);
- ``max_reworks``: a task's one rework budget, every cause together.

Each stays within ``[FLOORS, CEILINGS]``, fixed here: a company chooses
inside a range the code holds (AixleHQ/flow's ``MAX_RETRIES = 10``), and a
value outside it is refused when set. A stored value that no longer
validates is dropped when read, never applied (Orca's rule: a malformed
setting cannot lift a limit). ``services/models/model_profile.py`` reads the
ceilings of the Developer's limits from here.
"""

from __future__ import annotations

from typing import Any, Final

FLOORS: Final[dict[str, int]] = {
    "tool_rounds": 1,
    "result_max_chars": 500,
    "max_output_tokens": 0,
    "max_reworks": 0,
}
CEILINGS: Final[dict[str, int]] = {
    "tool_rounds": 50,
    "result_max_chars": 100_000,
    "max_output_tokens": 1_048_576,
    "max_reworks": 10,
}
LIMITS: Final = tuple(FLOORS)
# A task's rework budget when neither the deployment nor the company sets one.
DEFAULT_MAX_REWORKS: Final = 3


class InvalidLimits(ValueError):
    """Limits the backend refuses; the message says which and why."""


def _problem(key: str, value: Any) -> str | None:
    if key not in FLOORS:
        return f"unknown limit {key!r}; known: {', '.join(LIMITS)}"
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{key} must be a whole number"
    if not FLOORS[key] <= value <= CEILINGS[key]:
        return f"{key} must be between {FLOORS[key]} and {CEILINGS[key]}"
    return None


def validate(raw: Any) -> dict[str, int] | None:
    """*raw* as stored limits; ``None`` or ``{}`` clears them. ``InvalidLimits``
    names every problem."""
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise InvalidLimits("limits must be an object or null")
    problems = [p for key, value in raw.items() if (p := _problem(key, value))]
    if problems:
        raise InvalidLimits("; ".join(problems))
    return {key: raw[key] for key in LIMITS if key in raw}


def parse(stored: Any) -> dict[str, int]:
    """What a stored value applies: the valid entries only (``{}`` for none)."""
    if not isinstance(stored, dict):
        return {}
    return {
        key: stored[key]
        for key in LIMITS
        if key in stored and _problem(key, stored[key]) is None
    }


def deployment_rework_limit(max_reworks: int | None, legacy: int | None = None) -> int:
    """The deployment's rework budget: ``MAX_REWORKS``, else its old name
    ``AUTOPILOT_MAX_REWORK_CYCLES``, else ``DEFAULT_MAX_REWORKS``; never below 0."""
    value = max_reworks if max_reworks is not None else legacy
    return DEFAULT_MAX_REWORKS if value is None else max(0, int(value))
