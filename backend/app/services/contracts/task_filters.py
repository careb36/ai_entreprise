"""A filter over a company's tasks, and the shape of a saved view (issue #535).

A filter is a JSON tree: a group ``{"and": [...]}`` or ``{"or": [...]}``
whose members are conditions ``{"field", "op", "value"}`` or further groups.
The fields come from ``FILTER_FIELDS``, an allowlist; a condition on any other
field is refused with ``FilterRefused`` naming it, never ignored. Each field
says which operators it takes and what its value must be, so a value is
checked before anything reads it. The tree is bounded in depth, in members
per group and in conditions overall.

``parse_filter`` returns the normalised tree; ``matches`` evaluates it against
a task's facts, a plain dict keyed by the same field names. Neither performs
I/O or builds a query: the tasks context reads the facts from the task
projections (``services/tasks/task_filter.py``), and ``sprint_ids`` names the
sprints a filter is limited to so the reader can narrow its query with bound
parameters.

The view settings (``group_by``, ``order_by``, ``layout``,
``visible_columns``, ``access``) are checked here too, against their own
allowlists. The design follows Plane's rich filters and IssueView
(AGPL-3.0-only, design only: no code taken).
"""

from __future__ import annotations

import json
import re
from typing import Any

# The status names of the board's columns; any other stored status is shown,
# and filtered, as ``todo`` (``routers/sprint.py``).
STATUSES = ("todo", "in_progress", "in_review", "done", "blocked")
# The verdict names a verification records (``contracts/verdicts``); ``None``
# stands for a task with no verified delivery.
VERDICTS = ("verified", "refuted", "unverifiable")
# The risk tiers the Verifier observes, lowest first (``evidence/risk.py``).
RISK_TIERS = ("low", "medium", "high")

EQUALITY = ("eq", "ne", "in")
ORDERED = ("eq", "ne", "in", "gte", "lte")
OPERATORS = ("eq", "ne", "in", "gte", "lte")

# field -> (kind of value, operators). ``covers`` names a requirement or a
# criterion (``REQ-3``, ``REQ-3.1``); a requirement matches its criteria.
FILTER_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "status": ("status", EQUALITY),
    "verdict": ("verdict", EQUALITY),
    "risk_tier": ("risk_tier", ORDERED),
    "producer": ("text", EQUALITY),
    "assignee_role": ("text", EQUALITY),
    "rework_count": ("count", ORDERED),
    "waits_for_person": ("bool", ("eq", "ne")),
    "covers": ("requirement", EQUALITY),
    "sprint_id": ("id", EQUALITY),
}

MAX_DEPTH = 4
MAX_MEMBERS = 20
MAX_CONDITIONS = 50
MAX_IN_VALUES = 50
MAX_TEXT = 200
MAX_RAW_BYTES = 8192
_REQUIREMENT = re.compile(r"^REQ-\d{1,4}(\.\d{1,4})*$")

LAYOUTS = ("board", "list")
ACCESS = ("private", "company")
GROUP_FIELDS = (
    "status",
    "verdict",
    "risk_tier",
    "producer",
    "assignee_role",
    "waits_for_person",
    "sprint_id",
)
ORDER_FIELDS = (
    "id",
    "status",
    "verdict",
    "risk_tier",
    "producer",
    "assignee_role",
    "rework_count",
    "sprint_id",
    "updated_at",
)
COLUMNS = (
    "title",
    "status",
    "verdict",
    "risk_tier",
    "producer",
    "assignee_role",
    "rework_count",
    "waits_for_person",
    "covers",
    "sprint_id",
    "updated_at",
)
DEFAULT_COLUMNS = ("title", "status", "verdict", "risk_tier", "producer")
MAX_NAME = 120


class FilterRefused(ValueError):
    """A filter or a view setting that cannot be evaluated; ``field`` names the
    field at fault when there is one."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field

    def as_detail(self) -> dict[str, Any]:
        return {"message": str(self), "field": self.field}


# --- Parsing -----------------------------------------------------------------


def load(raw: str | None) -> dict[str, Any] | None:
    """The filter in a query string: JSON text, or ``None`` when empty."""
    if raw is None or not raw.strip():
        return None
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raise FilterRefused(f"the filter is over {MAX_RAW_BYTES} bytes")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise FilterRefused("the filter is not JSON") from exc
    return parse_filter(value)


def parse_filter(raw: Any) -> dict[str, Any] | None:
    """The normalised filter tree, or ``None`` for no filter (``{}`` or null).

    Raises ``FilterRefused`` for a field outside ``FILTER_FIELDS``, an
    operator the field does not take, a value of the wrong kind, or a tree
    over its bounds.
    """
    if raw is None or raw == {}:
        return None
    counter = [0]
    return _group(raw, 1, counter)


def _group(raw: Any, depth: int, counter: list[int]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FilterRefused("a group must be an object")
    if "field" in raw:
        # A bare condition at the top stands for a group of one.
        if depth == 1:
            return {"and": [_condition(raw, counter)]}
        raise FilterRefused("a condition stands only inside a group")
    if depth > MAX_DEPTH:
        raise FilterRefused(f"the filter is nested deeper than {MAX_DEPTH} groups")
    keys = set(raw)
    if keys not in ({"and"}, {"or"}):
        raise FilterRefused('a group has exactly one key, "and" or "or"')
    (key,) = keys
    members = raw[key]
    if not isinstance(members, list) or not members:
        raise FilterRefused(f'"{key}" takes a non-empty list')
    if len(members) > MAX_MEMBERS:
        raise FilterRefused(f"a group has more than {MAX_MEMBERS} members")
    parsed = []
    for member in members:
        if isinstance(member, dict) and "field" in member:
            parsed.append(_condition(member, counter))
        else:
            parsed.append(_group(member, depth + 1, counter))
    return {key: parsed}


def _condition(raw: dict[str, Any], counter: list[int]) -> dict[str, Any]:
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise FilterRefused(f"the filter has more than {MAX_CONDITIONS} conditions")
    extra = set(raw) - {"field", "op", "value"}
    if extra:
        raise FilterRefused(f"unknown keys in a condition: {sorted(extra)}")
    field = raw.get("field")
    if not isinstance(field, str) or field not in FILTER_FIELDS:
        name = field if isinstance(field, str) else repr(field)
        raise FilterRefused(f"the field {name[:60]!r} cannot be filtered", name[:60])
    kind, operators = FILTER_FIELDS[field]
    op = raw.get("op", "eq")
    if op not in operators:
        raise FilterRefused(
            f"the field {field!r} does not take {str(op)[:20]!r}", field
        )
    if "value" not in raw:
        raise FilterRefused(f"the condition on {field!r} has no value", field)
    value = raw["value"]
    if op == "in":
        if not isinstance(value, list) or not value or len(value) > MAX_IN_VALUES:
            raise FilterRefused(
                f'"in" on {field!r} takes a list of 1 to {MAX_IN_VALUES} values', field
            )
        value = [_value(kind, field, v) for v in value]
    else:
        value = _value(kind, field, value)
        if op in ("gte", "lte") and value is None:
            raise FilterRefused(f"{op!r} on {field!r} needs a value", field)
    return {"field": field, "op": op, "value": value}


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# kind -> whether a value is one of it.
_VALUES = {
    "status": lambda v: v in STATUSES,
    "verdict": lambda v: v is None or v in VERDICTS,
    "risk_tier": lambda v: v is None or v in RISK_TIERS,
    "text": lambda v: v is None or (isinstance(v, str) and 0 < len(v) <= MAX_TEXT),
    "count": lambda v: _integer(v) and v >= 0,
    "bool": lambda v: isinstance(v, bool),
    "requirement": lambda v: isinstance(v, str) and bool(_REQUIREMENT.match(v)),
    "id": lambda v: _integer(v) and v > 0,
}


def _value(kind: str, field: str, value: Any) -> Any:
    if isinstance(value, (list, dict)) or not _VALUES[kind](value):
        raise FilterRefused(f"{str(value)[:60]!r} is not a value of {field!r}", field)
    return value


# --- Reading a parsed tree ---------------------------------------------------


def fields_of(tree: dict[str, Any] | None) -> set[str]:
    """Every field a parsed filter reads."""
    if tree is None:
        return set()
    if "field" in tree:
        return {tree["field"]}
    (members,) = tree.values()
    return set().union(*(fields_of(m) for m in members))


def sprint_ids(tree: dict[str, Any] | None) -> set[int] | None:
    """The sprints a parsed filter is limited to, or ``None`` when it is not.

    Only a top-level ``and`` limits: an ``eq`` or ``in`` on ``sprint_id``
    among its members. Several such members intersect.
    """
    if tree is None or "and" not in tree:
        return None
    limit: set[int] | None = None
    for member in tree["and"]:
        if member.get("field") != "sprint_id" or member["op"] not in ("eq", "in"):
            continue
        ids = set(member["value"]) if member["op"] == "in" else {member["value"]}
        limit = ids if limit is None else limit & ids
    return limit


def _covers(covered: Any, wanted: str) -> bool:
    for criterion in covered or []:
        text = str(criterion)
        if text == wanted or text.startswith(wanted + "."):
            return True
    return False


def _tier_rank(value: Any) -> int | None:
    return RISK_TIERS.index(value) if value in RISK_TIERS else None


def _compare(field: str, op: str, actual: Any, wanted: Any) -> bool:
    if field == "covers":
        if op == "in":
            return any(_covers(actual, w) for w in wanted)
        hit = _covers(actual, wanted)
        return hit if op == "eq" else not hit
    if op == "eq":
        return actual == wanted
    if op == "ne":
        return actual != wanted
    if op == "in":
        return actual in wanted
    if field == "risk_tier":
        actual, wanted = _tier_rank(actual), _tier_rank(wanted)
    if actual is None:
        return False
    return actual >= wanted if op == "gte" else actual <= wanted


def matches(tree: dict[str, Any] | None, facts: dict[str, Any]) -> bool:
    """Whether a task whose facts are *facts* passes the parsed filter."""
    if tree is None:
        return True
    if "field" in tree:
        return _compare(
            tree["field"], tree["op"], facts.get(tree["field"]), tree["value"]
        )
    if "and" in tree:
        return all(matches(m, facts) for m in tree["and"])
    return any(matches(m, facts) for m in tree["or"])


# --- A saved view's settings -------------------------------------------------


def parse_order(raw: Any) -> str | None:
    """``order_by``: a field of ``ORDER_FIELDS``, ``-`` in front for descending."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and raw.lstrip("-") in ORDER_FIELDS:
        return raw
    raise FilterRefused(f"{str(raw)[:60]!r} cannot order tasks", "order_by")


def parse_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """A view's settings, checked; raises ``FilterRefused`` naming the setting."""
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise FilterRefused(
            f"a view needs a name of 1 to {MAX_NAME} characters", "name"
        )
    group_by = raw.get("group_by")
    if group_by not in (None, *GROUP_FIELDS):
        raise FilterRefused(f"{str(group_by)[:60]!r} cannot group tasks", "group_by")
    layout = raw.get("layout") or "list"
    if layout not in LAYOUTS:
        raise FilterRefused(f"the layout is one of {LAYOUTS}", "layout")
    access = raw.get("access") or "private"
    if access not in ACCESS:
        raise FilterRefused(f"the access is one of {ACCESS}", "access")
    columns = raw.get("visible_columns")
    if columns is None:
        columns = list(DEFAULT_COLUMNS)
    if not isinstance(columns, list) or any(c not in COLUMNS for c in columns):
        raise FilterRefused(
            f"the visible columns come from {COLUMNS}", "visible_columns"
        )
    return {
        "name": name.strip(),
        "filters": parse_filter(raw.get("filters")) or {},
        "group_by": group_by,
        "order_by": parse_order(raw.get("order_by")),
        "layout": layout,
        "visible_columns": list(dict.fromkeys(columns)),
        "access": access,
        "locked": bool(raw.get("locked", False)),
    }
