"""The programs a project declares, and a call's typed parameters (slice 1).

``docs/design/project-programs-and-screenshots.md``: a Manager declares the
project's entry points in the company's stack profile (``programs``, set
through the Manager-only, audited ``PUT /v1/companies/{id}/stack_profile``).
A model never writes command text: ``workspace__run_program`` names a
declared program and gives typed parameters, and the backend renders each
parameter as one word, ``--<name>=<value>``. Declarations come only from the
stack profile (the owner's decision 1, 2026-11-24): a file committed in the
repository is never read as one.

This module is the shape and the checks, pure and without imports of other
contexts. ``validate_programs`` runs when the profile is saved
(``workspace/stack_roots.validate_overrides``) and again at each call
(``tools/program_runs``); ``render`` turns a call's arguments into words.

The rules:

- ``argv`` is fixed words a person wrote. Each word is letters, digits and
  ``_ . = : / + -`` (the set ``tools/check_scripts.parse`` uses), with no
  ``..``, no absolute path and nothing a shell reads as an operator, quote,
  variable, glob or redirection.
- ``kind`` is ``script``, ``training`` or ``evaluation``; ``code`` is
  ``committed`` only (``notebook`` and ``code: task`` are later slices).
  ``entry`` is the file the program runs, which must be committed on the
  default branch and unchanged on disk at each call.
- A parameter is ``int``, ``float`` (finite), ``bool``, ``enum`` (its
  values) or ``string`` (a regex of at most 64 characters, a maximum length
  of at most 64), each with bounds. An unknown parameter, a value out of
  bounds or a missing one without a default is refused with the allowed
  values.
- ``metrics`` names where the platform reads metrics (a stdout line prefix
  and/or a file under ``/artifacts``), the tolerance a person set for
  ``metric_matches``, the precision scores are rounded to (decision 3) and
  optional thresholds.
- ``limits`` are bounded here; the daemon caps them again with its own
  maxima (``FABRIQUE_PROGRAM_MAX_*``), which the backend cannot raise.
"""

from __future__ import annotations

import math
import re
from typing import Any

KINDS = ("script", "training", "evaluation")
EVALUATION = "evaluation"
CODES = ("committed",)
TYPES = ("int", "float", "bool", "enum", "string")
MAX_PROGRAMS = 20
MAX_ARGV = 20
MAX_PARAMS = 20
MAX_ENUM_VALUES = 50
MAX_STRING = 64
DEFAULT_PREFIX = "fabrique-metric: "
DEFAULT_PRECISION = 4
# Limits a declaration may name, each with its bounds and default.
LIMITS: dict[str, tuple[float, float, float]] = {
    "seconds": (1, 3600, 600),
    "cpus": (0.1, 64, 2),
    "memory_mb": (64, 262144, 2048),
    "pids": (16, 4096, 512),
    "artifacts_mb": (1, 102400, 512),
}
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,40}")
_PARAM = re.compile(r"[a-z][a-z0-9_]{0,30}")
WORD = re.compile(r"[A-Za-z0-9_.=:/+-]{1,200}")
_BANNED = ("..", "=/", ":/", "//")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}")
_PREFIX = re.compile(r"[A-Za-z0-9_.:=/-][A-Za-z0-9_.:=/ -]{0,39}")
_METRIC = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{0,63}")
_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.json")


def plain_word(word: Any) -> bool:
    """A word the shell reads as itself: the plain set, no ``..``, not absolute."""
    return (
        isinstance(word, str)
        and bool(WORD.fullmatch(word))
        and not word.startswith("/")
        and not any(b in word for b in _BANNED)
    )


def _path(raw: Any, what: str) -> str:
    """A relative POSIX path of plain segments; raises ``ValueError``."""
    text = raw.strip().strip("/") if isinstance(raw, str) else ""
    parts = text.split("/") if text else []
    if not parts or not all(_SEGMENT.fullmatch(p) and p != ".." for p in parts):
        raise ValueError(f"{what} must be a relative path of plain names: {raw!r}")
    return "/".join(parts)


def _number(value: Any, what: str, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{what} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{what} must be finite")
    if integer and not isinstance(value, int):
        raise TypeError(f"{what} must be an integer")
    return value


def _param(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError(f"params.{name} must be an object")
    kind = raw.get("type")
    if kind not in TYPES:
        raise ValueError(f"params.{name}.type must be one of {', '.join(TYPES)}")
    allowed = {"type", "default", "min", "max", "values", "pattern", "max_length"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown field for params.{name}: {unknown[0]}")
    spec: dict[str, Any] = {"type": kind}
    if kind in ("int", "float"):
        integer = kind == "int"
        low = _number(raw.get("min"), f"params.{name}.min", integer=integer)
        high = _number(raw.get("max"), f"params.{name}.max", integer=integer)
        if low > high:
            raise ValueError(f"params.{name}: min is above max")
        spec.update(min=low, max=high)
    elif kind == "enum":
        values = raw.get("values")
        if not isinstance(values, list) or not 0 < len(values) <= MAX_ENUM_VALUES:
            raise ValueError(
                f"params.{name}.values must list 1 to {MAX_ENUM_VALUES} words"
            )
        for value in values:
            if not plain_word(value) or len(value) > MAX_STRING:
                raise ValueError(f"params.{name}.values: {value!r} is not a plain word")
        spec["values"] = list(dict.fromkeys(values))
    elif kind == "string":
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not 0 < len(pattern) <= MAX_STRING:
            raise ValueError(
                f"params.{name}.pattern must be a regex of 1 to {MAX_STRING} characters"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"params.{name}.pattern does not compile: {exc}") from exc
        length = raw.get("max_length", MAX_STRING)
        _number(length, f"params.{name}.max_length", integer=True)
        if not 1 <= length <= MAX_STRING:
            raise ValueError(f"params.{name}.max_length must be 1 to {MAX_STRING}")
        spec.update(pattern=pattern, max_length=length)
    if "default" in raw and raw["default"] is not None:
        spec["default"] = check_value(name, spec, raw["default"])
    return spec


def check_value(name: str, spec: dict[str, Any], value: Any) -> Any:
    """*value* checked against *spec*; raises ``ValueError`` naming what is allowed."""
    kind = spec["type"]
    if kind in ("int", "float"):
        bounds = f"from {spec['min']:g} to {spec['max']:g}"
        try:
            number = _number(value, name, integer=kind == "int")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a finite {kind} {bounds}") from exc
        if not spec["min"] <= number <= spec["max"]:
            raise ValueError(f"{name} is out of bounds: {value!r}; allowed {bounds}")
        return float(number) if kind == "float" else number
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false")
        return value
    if kind == "enum":
        if value not in spec["values"]:
            raise ValueError(f"{name} must be one of {', '.join(spec['values'])}")
        return value
    if (
        not isinstance(value, str)
        or len(value) > spec["max_length"]
        or not plain_word(value)
        or not re.fullmatch(spec["pattern"], value)
    ):
        raise ValueError(
            f"{name} must match {spec['pattern']} in letters, digits and `_ . = : / + -`, "
            f"at most {spec['max_length']} characters"
        )
    return value


def _metrics(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {
            "line_prefix": DEFAULT_PREFIX,
            "precision": DEFAULT_PRECISION,
            "tolerance": 0.0,
        }
    if not isinstance(raw, dict):
        raise TypeError("metrics must be an object")
    allowed = {
        "line_prefix",
        "file",
        "tolerance",
        "tolerances",
        "precision",
        "thresholds",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown field for metrics: {unknown[0]}")
    prefix = raw.get("line_prefix", DEFAULT_PREFIX)
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ValueError("metrics.line_prefix must be 1 to 40 plain characters")
    out: dict[str, Any] = {"line_prefix": prefix}
    if raw.get("file") is not None:
        if not isinstance(raw["file"], str) or not _FILE.fullmatch(raw["file"]):
            raise ValueError(
                "metrics.file must be one .json file name under /artifacts"
            )
        out["file"] = raw["file"]
    precision = raw.get("precision", DEFAULT_PRECISION)
    _number(precision, "metrics.precision", integer=True)
    if not 0 <= precision <= 8:
        raise ValueError("metrics.precision must be 0 to 8 digits")
    out["precision"] = precision
    tolerance = _number(raw.get("tolerance", 0.0), "metrics.tolerance")
    if tolerance < 0:
        raise ValueError("metrics.tolerance must not be negative")
    out["tolerance"] = float(tolerance)
    named = raw.get("tolerances") or {}
    thresholds = raw.get("thresholds") or {}
    if not isinstance(named, dict) or not isinstance(thresholds, dict):
        raise TypeError("metrics.tolerances and metrics.thresholds must be objects")
    for key, value in named.items():
        if not _METRIC.fullmatch(str(key)) or _number(value, f"tolerance {key}") < 0:
            raise ValueError(
                f"metrics.tolerances.{key} must be a metric name and a non-negative number"
            )
    if named:
        out["tolerances"] = {str(k): float(v) for k, v in named.items()}
    checked: dict[str, Any] = {}
    for key, rule in thresholds.items():
        if not _METRIC.fullmatch(str(key)) or not isinstance(rule, dict):
            raise ValueError(
                f"metrics.thresholds.{key} must be a metric name and an object"
            )
        if set(rule) - {"min", "max", "split"} or not ({"min", "max"} & set(rule)):
            raise ValueError(
                f"metrics.thresholds.{key} holds min and/or max, and split"
            )
        item = {
            b: float(_number(rule[b], f"threshold {key}.{b}"))
            for b in ("min", "max")
            if b in rule
        }
        if "split" in rule:
            if rule["split"] not in ("train", "val", "test"):
                raise ValueError(
                    f"metrics.thresholds.{key}.split must be train, val or test"
                )
            item["split"] = rule["split"]
        checked[str(key)] = item
    if checked:
        out["thresholds"] = checked
    return out


def _limits(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TypeError("limits must be an object")
    unknown = sorted(set(raw) - set(LIMITS))
    if unknown:
        raise ValueError(f"unknown field for limits: {unknown[0]}")
    out: dict[str, Any] = {}
    for key, (low, high, default) in LIMITS.items():
        value = raw.get(key, default)
        _number(value, f"limits.{key}", integer=key != "cpus")
        if not low <= value <= high:
            raise ValueError(f"limits.{key} must be from {low:g} to {high:g}")
        out[key] = value
    return out


def validate_program(raw: Any) -> dict[str, Any]:
    """One declaration, checked; raises ``ValueError`` or ``TypeError``."""
    if not isinstance(raw, dict):
        raise TypeError("each program must be an object")
    allowed = {
        "name",
        "kind",
        "folder",
        "argv",
        "code",
        "entry",
        "params",
        "metrics",
        "limits",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown program field: {unknown[0]}")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"a program's name must match {_NAME.pattern}: {name!r}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"program {name}: kind must be one of {', '.join(KINDS)}")
    code = raw.get("code", "committed")
    if code not in CODES:
        raise ValueError(
            f"program {name}: code must be committed (code: task is a later slice)"
        )
    argv = raw.get("argv")
    if not isinstance(argv, list) or not 0 < len(argv) <= MAX_ARGV:
        raise ValueError(f"program {name}: argv must list 1 to {MAX_ARGV} words")
    for word in argv:
        if not plain_word(word):
            raise ValueError(
                f"program {name}: argv word {word!r} is not plain (letters, digits and "
                "`_ . = : / + -`; no `..`, no absolute path)"
            )
    params = raw.get("params") or {}
    if not isinstance(params, dict) or len(params) > MAX_PARAMS:
        raise ValueError(
            f"program {name}: params must be an object of at most {MAX_PARAMS}"
        )
    checked: dict[str, Any] = {}
    for key, spec in params.items():
        if not isinstance(key, str) or not _PARAM.fullmatch(key):
            raise ValueError(
                f"program {name}: parameter name {key!r} must match {_PARAM.pattern}"
            )
        checked[key] = _param(key, spec)
    out: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "code": code,
        "argv": list(argv),
        "entry": _path(raw.get("entry"), f"program {name}: entry"),
        "params": checked,
        "metrics": _metrics(raw.get("metrics")),
        "limits": _limits(raw.get("limits")),
    }
    if raw.get("folder"):
        out["folder"] = _path(raw["folder"], f"program {name}: folder")
    return out


def validate_programs(raw: Any) -> list[dict[str, Any]]:
    """The profile's ``programs``, checked; names unique."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_PROGRAMS:
        raise ValueError(f"programs must be a list of at most {MAX_PROGRAMS} programs")
    out = [validate_program(entry) for entry in raw]
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise ValueError("program names must be unique")
    return out


def valid_or_empty(raw: Any) -> list[dict[str, Any]]:
    """``validate_programs`` for a stored row: a row that no longer passes
    declares nothing rather than stopping a run."""
    try:
        return validate_programs(raw)
    except (TypeError, ValueError):
        return []


def _rendered(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def render(program: dict[str, Any], arguments: Any) -> tuple[list[str], dict[str, Any]]:
    """The words to run and the values used: ``argv`` then ``--<name>=<value>``
    per parameter in declaration order. Raises ``ValueError`` for an unknown
    parameter, a value out of bounds or a missing one without a default."""
    given = arguments if isinstance(arguments, dict) else {}
    params = program.get("params") or {}
    unknown = sorted(set(given) - set(params))
    if unknown:
        allowed = ", ".join(params) or "none"
        raise ValueError(
            f"unknown parameter {unknown[0]!r}; the parameters are {allowed}"
        )
    words, values = list(program["argv"]), {}
    for name, spec in params.items():
        if name in given and given[name] is not None:
            value = check_value(name, spec, given[name])
        elif "default" in spec:
            value = spec["default"]
        else:
            raise ValueError(f"{name} is required (no default); {describe(spec)}")
        word = f"--{name}={_rendered(value)}"
        if not plain_word(word):  # Defence in depth: never reached by a checked value.
            raise ValueError(f"{name} does not render as a plain word")
        words.append(word)
        values[name] = value
    return words, values


def describe(spec: dict[str, Any]) -> str:
    """What a parameter accepts, for the model."""
    kind = spec["type"]
    if kind in ("int", "float"):
        text = f"{kind} from {spec['min']:g} to {spec['max']:g}"
    elif kind == "enum":
        text = "one of " + ", ".join(spec["values"])
    elif kind == "string":
        text = f"text matching {spec['pattern']}"
    else:
        text = "true or false"
    return text + (
        f" (default {_rendered(spec['default'])})" if "default" in spec else ""
    )
