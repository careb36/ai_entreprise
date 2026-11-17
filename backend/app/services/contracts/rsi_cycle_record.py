"""The self-improvement cycle record's rules, as pure functions
(``docs/design/rsi-loop.md``, PR 10 of section 8; REQ-26, REQ-27).

The cycle runner (``scripts/rsi/cycle.py``) writes one JSON record per cycle;
``scripts/rsi/cycle_record.py`` holds its schema, version 1, and the watch
after a merge refuses a record that does not validate. The backend imports
the same records (``services/management/rsi_cycle.py``) and must refuse
exactly what the script refuses, so the rules are here, in contracts, with
the metric names passed in: contracts imports no other context, and the
names are ``services/evidence/fitness.GUARDED``'s. ``backend/tests/
test_rsi_cycle_record_parity.py`` runs both validators over the same records
and fails when they disagree.

Beside schema version 1 the backend reads one optional field the runner adds
for REQ-26, ``kept_files``: the files the cycle kept, each ``{"path":
"<repository-relative path>", "sha256": "<64 hex>"}``. The hash is the
runner's claim; ``kept_files_problems`` checks only its shape, and the
backend recomputes every hash it can read (rule 6 of ``docs/POSITIONING.md``).
"""

from __future__ import annotations

import math
import posixpath
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1
OPTIONAL_METRICS = ("prompt_tokens_mean",)
_SUMMARY_FIELDS = ("median", "spread", "runs")
_SHA = re.compile(r"[0-9a-f]{40}")  # used with fullmatch: no trailing newline
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CYCLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_OPTIONAL_TEXT = ("baseline_sha", "finding_id", "specification")
MAX_KEPT_FILES = 500


def number(value: Any) -> bool:
    """A finite int or float, never a bool, NaN or infinity."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def sha(value: Any) -> bool:
    """A full 40-character lowercase commit sha, matched whole."""
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def cycle_id_ok(value: Any) -> bool:
    """Whether *value* is a cycle id that makes a valid branch name
    (``rsi/revert-<cycle id>`` passes ``git check-ref-format``)."""
    return (
        isinstance(value, str)
        and _CYCLE_ID.fullmatch(value) is not None
        and ".." not in value
        and not value.endswith(".")
        and not value.endswith(".lock")
    )


def count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def parse_when(text: Any) -> datetime | None:
    """*text* as a datetime, or None when it is not ISO 8601."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))  # noqa: FURB162 - as the script parses it
    except ValueError:
        return None


def _summary(where: str, obj: Any, problems: list[str]) -> None:
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object with a median and a spread")
        return
    if extra := sorted(set(obj) - set(_SUMMARY_FIELDS)):
        problems.append(f"{where}: unknown field(s) {', '.join(extra)}")
    if not number(obj.get("median")):
        problems.append(f"{where}.median must be a finite number")
    spread = obj.get("spread")
    if not number(spread) or spread < 0:
        problems.append(f"{where}.spread must be a finite number >= 0")
    if obj.get("runs") is not None and not count(obj["runs"]):
        problems.append(f"{where}.runs must be a count >= 1")


def _scope(
    where: str,
    obj: Any,
    required: Sequence[str],
    optional: Sequence[str],
    problems: list[str],
) -> None:
    """One baseline scope: every guarded metric, the optional ones allowed."""
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object of metric summaries")
        return
    if unknown := sorted(set(obj) - set(required) - set(optional)):
        problems.append(f"{where}: unknown metric(s) {', '.join(unknown)}")
    if missing := sorted(set(required) - set(obj)):
        problems.append(f"{where}: no baseline for {', '.join(missing)}")
    for metric in sorted(set(obj) - set(unknown)):
        _summary(f"{where}.{metric}", obj[metric], problems)


def _models(value: Any, problems: list[str]) -> None:
    if (
        not isinstance(value, dict)
        or not value
        or not all(
            isinstance(role, str)
            and isinstance(names, list)
            and names
            and all(isinstance(name, str) and name for name in names)
            for role, names in value.items()
        )
    ):
        problems.append(
            "models must map each role to the non-empty list of models B was served"
        )


def validate(
    record: Any,
    required: Sequence[str],
    optional: Sequence[str] = OPTIONAL_METRICS,
) -> list[str]:
    """What is wrong with *record* against schema version 1; empty when sound.

    *required* are the metrics every baseline scope must carry (fitness's
    guarded six), *optional* the ones it may carry.
    """
    if not isinstance(record, dict):
        return ["the record is not an object"]
    problems: list[str] = []
    version = record.get("schema_version")
    if version != SCHEMA_VERSION:
        problems.append(f"schema_version is {version!r}, expected {SCHEMA_VERSION}")
    if not cycle_id_ok(record.get("cycle_id")):
        problems.append(
            "cycle_id must be 1-100 branch-safe characters "
            "(a letter or digit, then letters, digits, '.', '_' or '-'; "
            "no '..', no trailing '.' and no '.lock' suffix)"
        )
    for key in ("merged_sha", "graders_sha"):
        if not sha(record.get(key)):
            problems.append(f"{key} must be a full 40-character commit sha")
    when = parse_when(record.get("merged_at"))
    if when is None:
        problems.append("merged_at must be an ISO 8601 timestamp")
    elif when.tzinfo is None:
        problems.append("merged_at must name its timezone (a UTC 'Z' or an offset)")
    _models(record.get("models"), problems)
    for key in _OPTIONAL_TEXT:
        if key in record and (
            not isinstance(record[key], str)
            or (key == "baseline_sha" and not sha(record[key]))
        ):
            problems.append(
                f"{key} must be "
                + ("a full commit sha" if key == "baseline_sha" else "a string")
            )
    for key in ("pull_request", "k"):
        if key in record and not count(record[key]):
            problems.append(f"{key} must be an integer >= 1")
    if "prompt_targeted" in record and not isinstance(record["prompt_targeted"], bool):
        problems.append("prompt_targeted must be a boolean")
    baseline = record.get("baseline")
    if not isinstance(baseline, dict):
        problems.append("baseline must be an object")
        return problems
    if unknown := sorted(set(baseline) - {"totals", "specs"}):
        problems.append(f"baseline: unknown field(s) {', '.join(unknown)}")
    if "totals" not in baseline:
        problems.append("baseline.totals is missing")
    else:
        _scope("baseline.totals", baseline["totals"], required, optional, problems)
    specs = baseline.get("specs")
    if specs is not None:
        if not isinstance(specs, dict):
            problems.append("baseline.specs must be an object of scopes")
        else:
            for name in sorted(specs):
                _scope(
                    f"baseline.specs.{name}", specs[name], required, optional, problems
                )
    return problems


def relative_path(value: Any) -> str | None:
    """*value* as a clean repository-relative POSIX path, or None.

    Absolute paths, backslashes, ``..`` segments, empty segments and control
    characters are refused: a kept file is named inside the repository.
    """
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    if value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return posixpath.normpath(value)


def kept_files_problems(value: Any) -> list[str]:
    """What is wrong with a record's ``kept_files``; empty when sound or absent.

    Shape only: a list of ``{"path", "sha256"}`` with distinct clean paths.
    Whether a hash is right is for the reader to observe, never to accept.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return ["kept_files must be a list of {path, sha256}"]
    if len(value) > MAX_KEPT_FILES:
        return [f"kept_files lists more than {MAX_KEPT_FILES} files"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        where = f"kept_files[{index}]"
        if not isinstance(entry, dict) or set(entry) - {"path", "sha256"}:
            problems.append(f"{where} must be an object with only path and sha256")
            continue
        path = relative_path(entry.get("path"))
        if path is None:
            problems.append(f"{where}.path must be a repository-relative path")
        elif path in seen:
            problems.append(f"{where}.path {path} is listed twice")
        else:
            seen.add(path)
        if not sha256(entry.get("sha256")):
            problems.append(f"{where}.sha256 must be 64 lowercase hex characters")
    return problems
