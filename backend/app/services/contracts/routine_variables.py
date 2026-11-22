"""A routine's variables: named inputs its task text uses (issue #774).

A routine declares its variables, each ``{"name", "label", "default",
"required", "options"}``, and writes ``{{name}}`` in its description. When it
runs, ``resolve`` takes the values given ("run now") over the defaults,
refuses a missing required value, a value outside ``options`` or a name the
routine does not declare, and ``render`` puts them in the text. A
placeholder the routine does not declare is refused when the routine is
written (``check_placeholders``), so a run never leaves ``{{…}}`` unfilled.

Values are text, at most ``VALUE_MAX`` characters each; a value is inserted
as written, never evaluated. The shape follows Paperclip's routine variables
(MIT: ``packages/shared/src/types/routine.ts``, ``RoutineVariable``); no code
taken. Pure: no database.
"""

from __future__ import annotations

import re
from typing import Any

MAX_VARIABLES = 20
VALUE_MAX = 500
LABEL_MAX = 100
MAX_OPTIONS = 50
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class InvalidVariables(ValueError):
    """Declarations or values the backend refuses; the message says why."""


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise InvalidVariables(f"{what} must be text")
    if len(value) > VALUE_MAX:
        raise InvalidVariables(f"{what} is over {VALUE_MAX} characters")
    return value


def validate(raw: Any) -> list[dict[str, Any]]:
    """*raw* as stored declarations (``[]`` for none), or ``InvalidVariables``."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidVariables("variables must be a list")
    if len(raw) > MAX_VARIABLES:
        raise InvalidVariables(f"at most {MAX_VARIABLES} variables")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidVariables("each variable must be an object")
        unknown = set(item) - {"name", "label", "default", "required", "options"}
        if unknown:
            raise InvalidVariables(
                f"unknown variable field(s): {', '.join(sorted(unknown))}"
            )
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise InvalidVariables(
                "a variable name is 1 to 40 lowercase letters, digits or _, starting with a letter"
            )
        if name in seen:
            raise InvalidVariables(f"variable {name!r} is declared twice")
        seen.add(name)
        label = item.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > LABEL_MAX):
            raise InvalidVariables(
                f"label of {name!r} must be text of at most {LABEL_MAX}"
            )
        options = item.get("options") or []
        if not isinstance(options, list) or len(options) > MAX_OPTIONS:
            raise InvalidVariables(
                f"options of {name!r} must be a list of at most {MAX_OPTIONS}"
            )
        options = [_text(o, f"an option of {name!r}") for o in options]
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise InvalidVariables(f"required of {name!r} must be true or false")
        default = item.get("default")
        if default is not None:
            default = _text(default, f"the default of {name!r}")
            if options and default not in options:
                raise InvalidVariables(
                    f"the default of {name!r} is not one of its options"
                )
        out.append(
            {
                "name": name,
                "label": label,
                "default": default,
                "required": required,
                "options": options,
            }
        )
    return out


def placeholders(text: str) -> list[str]:
    return sorted(set(_PLACEHOLDER.findall(text or "")))


def check_placeholders(text: str, declared: list[dict[str, Any]]) -> None:
    """Refuse a ``{{name}}`` in *text* that *declared* does not declare."""
    names = {v["name"] for v in declared}
    unknown = [p for p in placeholders(text) if p not in names]
    if unknown:
        raise InvalidVariables(
            f"the description uses undeclared variable(s): {', '.join(unknown)}"
        )


def resolve(declared: list[dict[str, Any]], given: Any) -> dict[str, str]:
    """The value of every declared variable: *given* over the default."""
    given = given or {}
    if not isinstance(given, dict):
        raise InvalidVariables("values must be an object")
    names = {v["name"] for v in declared}
    unknown = sorted(set(given) - names)
    if unknown:
        raise InvalidVariables(f"unknown variable(s): {', '.join(unknown)}")
    out: dict[str, str] = {}
    for var in declared:
        name = var["name"]
        value = given.get(name)
        if value is None:
            value = var.get("default")
        if value is None or value == "":
            if var.get("required"):
                raise InvalidVariables(f"variable {name!r} is required")
            value = ""
        value = _text(value, f"the value of {name!r}")
        if var.get("options") and value and value not in var["options"]:
            raise InvalidVariables(
                f"{name!r} must be one of: {', '.join(var['options'])}"
            )
        out[name] = value
    return out


def render(text: str, values: dict[str, str]) -> str:
    """*text* with each ``{{name}}`` replaced by its value, in one pass (a value
    that itself holds ``{{…}}`` is not expanded again)."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text or "")
