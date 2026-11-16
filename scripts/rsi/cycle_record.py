"""The self-improvement cycle's record: its schema and validator.

The cycle runner (``scripts/rsi/cycle.py``, PR 8 of ``docs/design/rsi-loop.md``)
writes one JSON record per cycle once its change is merged into ``main``, and a
person commits it under ``rsi/cycles/<cycle id>.json``. The watch after the
merge (``scripts/rsi/watch.py``, REQ-19) reads the record and compares each
nightly real-model scorecard made after the merge with the baseline B's
medians and spread that the record carries. A record that does not validate is
refused: the watch answers ``unmeasured`` (exit 2), never ``ok`` — unknown is
not absent (rule 7 of ``docs/POSITIONING.md``).

Schema, version 1 (every field required unless said optional; the metric names
are ``services/evidence/fitness.py``'s, the guarded six plus the prompt's mean,
which counts only for a cycle whose specification targets the prompt):

.. code-block:: json

    {
      "schema_version": 1,
      "cycle_id": "cycle-1-folder-lint",
      "merged_sha": "<the 40-hex sha merged into main>",
      "merged_at": "2026-11-16T14:39:43Z",
      "graders_sha": "<the 40-hex sha the cycle's graders were taken from>",
      "models": {"Developer": ["<model>"], "QA": ["<model>"]},
      "baseline": {
        "totals": {
          "criteria_rate":      {"median": 0.9,  "spread": 0.02, "runs": 3},
          "first_attempt_rate": {"median": 0.8,  "spread": 0.04},
          "blocked_share":      {"median": 0.1,  "spread": 0.02},
          "rework_per_task":    {"median": 0.5,  "spread": 0.1},
          "tokens_reported":    {"median": 100000, "spread": 20000},
          "wall_clock_seconds": {"median": 1000.0, "spread": 100.0},
          "prompt_tokens_mean": {"median": 2000.0, "spread": 100.0}
        },
        "specs": {"<spec name>": {"<metric>": {"median": 0, "spread": 0}}}
      },
      "baseline_sha": "<B's 40-hex sha>",
      "finding_id": "F5",
      "specification": "folder-lint",
      "pull_request": 630,
      "prompt_targeted": false,
      "k": 3
    }

- ``cycle_id`` names the cycle in the watch's output and in the revert pull
  request's branch, so it is restricted to branch-safe characters.
- ``merged_at`` is ISO 8601 with its timezone (a UTC ``Z`` or an offset); a
  card counts only when its stamp is strictly after it.
- ``models`` is B's served model per role (the scorecard's ``models`` over
  every specification, as ``scripts/bench/repeated.py`` reads them): a nightly
  card served by different models is a void pair and makes its night
  unmeasured (REQ-7).
- ``baseline.totals`` holds one summary per guarded metric of
  ``fitness.METRICS`` — the medians of B's K complete runs and their spread
  (max − min), as ``repeated.summary`` computes them; ``runs`` is optional.
  ``prompt_tokens_mean`` is optional and judged only with ``prompt_targeted``.
- ``baseline.specs`` is optional and holds the same summaries for the
  specifications B measured in full; a specification the record does not carry
  is not judged.

``validate(record)`` returns what is wrong with *record* (empty when it is
sound); ``merged_at_dt(record)`` is the merge's moment as an aware datetime.
"""

from __future__ import annotations

import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "app"))
from services.evidence import fitness  # noqa: E402

SCHEMA_VERSION = 1
CYCLES_DIR = "rsi/cycles"  # where a person commits the records, by convention
REQUIRED_METRICS = tuple(fitness.GUARDED)  # the six the decision guards
OPTIONAL_METRICS = ("prompt_tokens_mean",)
_SUMMARY_FIELDS = ("median", "spread", "runs")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_CYCLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_OPTIONAL_TEXT = ("baseline_sha", "finding_id", "specification")


def _number(value: Any) -> bool:
    """A finite int or float, never a bool, NaN or infinity."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def parse_when(text: Any) -> datetime | None:
    """*text* as an aware datetime, or None when it is not ISO 8601."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def merged_at_dt(record: dict[str, Any]) -> datetime | None:
    """The merge's moment; None when the record does not carry a sound one."""
    return parse_when(record.get("merged_at")) if isinstance(record, dict) else None


def _summary(where: str, obj: Any, problems: list[str]) -> None:
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object with a median and a spread")
        return
    if extra := sorted(set(obj) - set(_SUMMARY_FIELDS)):
        problems.append(f"{where}: unknown field(s) {', '.join(extra)}")
    if not _number(obj.get("median")):
        problems.append(f"{where}.median must be a finite number")
    spread = obj.get("spread")
    if not _number(spread) or spread < 0:
        problems.append(f"{where}.spread must be a finite number >= 0")
    if obj.get("runs") is not None and not _count(obj["runs"]):
        problems.append(f"{where}.runs must be a count >= 1")


def _scope(where: str, obj: Any, problems: list[str]) -> None:
    """One baseline scope: every guarded metric, the prompt's mean optional."""
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object of metric summaries")
        return
    if unknown := sorted(set(obj) - set(REQUIRED_METRICS) - set(OPTIONAL_METRICS)):
        problems.append(f"{where}: unknown metric(s) {', '.join(unknown)}")
    if missing := sorted(set(REQUIRED_METRICS) - set(obj)):
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


def validate(record: Any) -> list[str]:
    """What is wrong with *record* against the schema; empty when it is sound."""
    if not isinstance(record, dict):
        return ["the record is not an object"]
    problems = []
    version = record.get("schema_version")
    if version != SCHEMA_VERSION:
        problems.append(f"schema_version is {version!r}, expected {SCHEMA_VERSION}")
    if not isinstance(record.get("cycle_id"), str) or not _CYCLE_ID.match(
        record["cycle_id"]
    ):
        problems.append(
            "cycle_id must be 1-100 branch-safe characters "
            "(a letter or digit, then letters, digits, '.', '_' or '-')"
        )
    for key in ("merged_sha", "graders_sha"):
        if not isinstance(record.get(key), str) or not _SHA.match(record[key]):
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
            or (key == "baseline_sha" and not _SHA.match(record[key]))
        ):
            problems.append(
                f"{key} must be "
                + ("a full commit sha" if key == "baseline_sha" else "a string")
            )
    for key in ("pull_request", "k"):
        if key in record and not _count(record[key]):
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
        _scope("baseline.totals", baseline["totals"], problems)
    specs = baseline.get("specs")
    if specs is not None:
        if not isinstance(specs, dict):
            problems.append("baseline.specs must be an object of scopes")
        else:
            for name in sorted(specs):
                _scope(f"baseline.specs.{name}", specs[name], problems)
    return problems
