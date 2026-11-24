"""Metrics a program printed, read by the platform, and an agent's stated ones.

``docs/design/project-programs-and-screenshots.md`` section 3: a number
becomes an observation only when the platform ran the program and read the
number itself. This module is the reading, pure and without imports of other
contexts:

- ``parse_lines``: stdout lines starting with the declared prefix, each one
  JSON object ``{"name", "value", "split", "step"?}``. The caller passes the
  stdout bytes it hashed itself; stderr is never read for metrics. The name
  matches ``[A-Za-z][A-Za-z0-9_./-]{0,63}``, the value must be finite, and at
  most ``MAX_METRICS`` are kept (more marks the record ``metrics_truncated``).
- ``parse_file``: ``/artifacts/<file>`` holding ``{"metrics": [...]}`` with
  the same entries, at most ``MAX_FILE_BYTES``. A file that fails the schema
  gives no metrics and says why. A ``sha256`` field in it is never read.
- ``parse_claims``: the metrics an agent states in its answer, one line
  ``claimed-metric: {"name", "value", "split"}`` each. They are claims and
  stay in the run's claims, never beside the observed ones (rule 1).
- ``provenance``: who could have printed the number (section 3's table).
  ``program_output``: a ``script`` or ``training`` program, where agent code
  ran and could print anything. ``committed_evaluator``: a committed,
  unchanged ``evaluation``, which still loads the agent's code in-process.
  ``platform_scorer`` is the two-phase evaluation, a later slice.
- ``supports_quality``: the owner's decision 2 (2026-11-24). A
  ``committed_evaluator`` metric may support the ``low`` and ``medium``
  tiers; ``high`` needs ``platform_scorer``; ``program_output`` never
  supports a quality metric.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

PROGRAM_OUTPUT = "program_output"
COMMITTED_EVALUATOR = "committed_evaluator"
PLATFORM_SCORER = "platform_scorer"
PROVENANCES = (PROGRAM_OUTPUT, COMMITTED_EVALUATOR, PLATFORM_SCORER)
SPLITS = ("train", "val", "test")
MAX_METRICS = 500
MAX_FILE_BYTES = 256 * 1024
MAX_CLAIMS = 50
CLAIM_PREFIX = "claimed-metric:"
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{0,63}")


def entry(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """One metric entry checked: ``(metric, None)`` or ``(None, why)``."""
    if not isinstance(raw, dict):
        return None, "not an object"
    name, value, split = raw.get("name"), raw.get("value"), raw.get("split", "val")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None, f"bad name {str(name)[:40]!r}"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None, f"{name}: the value is not a number"
    if not math.isfinite(value):
        return None, f"{name}: the value is not finite"
    if split not in SPLITS:
        return None, f"{name}: split must be train, val or test"
    out: dict[str, Any] = {"name": name, "value": float(value), "split": split}
    step = raw.get("step")
    if isinstance(step, int) and not isinstance(step, bool) and step >= 0:
        out["step"] = step
    return out, None


def parse_lines(stdout: str, prefix: str) -> dict[str, Any]:
    """The metrics on *stdout*'s lines that start with *prefix*: ``metrics``
    (each with ``source: line`` and its line number), ``dropped`` (why, at
    most 20) and ``metrics_truncated``."""
    metrics: list[dict[str, Any]] = []
    dropped: list[str] = []
    truncated = False
    for number, line in enumerate((stdout or "").splitlines(), start=1):
        if not prefix or not line.startswith(prefix):
            continue
        if len(metrics) >= MAX_METRICS:
            truncated = True
            break
        try:
            raw = json.loads(line[len(prefix) :])
        except ValueError:
            raw = None
        metric, why = entry(raw) if raw is not None else (None, "not JSON")
        if metric is None:
            if len(dropped) < 20:
                dropped.append(f"line {number}: {why}")
            continue
        metrics.append({**metric, "source": "line", "line": number})
    return {"metrics": metrics, "dropped": dropped, "metrics_truncated": truncated}


def parse_file(data: bytes, name: str) -> dict[str, Any]:
    """The metrics in a ``metrics.json`` the backend received: ``metrics``
    (``source: file``), ``dropped``, ``metrics_truncated``, or ``reason``
    when the file fails the schema (then no metrics at all)."""
    if len(data) > MAX_FILE_BYTES:
        return {"metrics": [], "reason": f"{name} is over {MAX_FILE_BYTES} bytes"}
    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"metrics": [], "reason": f"{name} is not JSON"}
    listed = body.get("metrics") if isinstance(body, dict) else None
    if not isinstance(listed, list):
        return {"metrics": [], "reason": f"{name} has no metrics list"}
    checked = [entry(item) for item in listed[:MAX_METRICS]]
    bad = [why for metric, why in checked if metric is None]
    if bad:
        return {"metrics": [], "reason": f"{name} fails the schema: {bad[0]}"}
    return {
        "metrics": [{**m, "source": "file"} for m, _ in checked if m],
        "dropped": [],
        "metrics_truncated": len(listed) > MAX_METRICS,
    }


def rounded(metrics: list[dict[str, Any]], precision: int) -> list[dict[str, Any]]:
    """Each value rounded to *precision* digits (decision 3: rounded scores)."""
    return [{**m, "value": round(m["value"], precision)} for m in metrics]


def parse_claims(text: str) -> list[dict[str, Any]]:
    """The metrics an agent states in its answer (``claimed-metric:`` lines)."""
    claims: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("-* ").strip("`")
        if not stripped.lower().startswith(CLAIM_PREFIX):
            continue
        try:
            raw = json.loads(stripped[len(CLAIM_PREFIX) :].strip().strip("`"))
        except ValueError:
            continue
        claim, _why = entry(raw)
        if claim is not None:
            claims.append(claim)
        if len(claims) >= MAX_CLAIMS:
            break
    return claims


def provenance(kind: Any, code: Any) -> str:
    """Who could have printed a number a program of *kind* and *code* printed."""
    if kind == "evaluation" and code == "committed":
        return COMMITTED_EVALUATOR
    return PROGRAM_OUTPUT


def supports_quality(source: Any, tier: Any) -> tuple[bool, str | None]:
    """Whether a metric of provenance *source* may support a quality claim at
    risk *tier*, and why not (decision 2)."""
    if source == PLATFORM_SCORER:
        return True, None
    if source == COMMITTED_EVALUATOR:
        if tier in ("low", "medium"):
            return True, None
        return False, (
            f"a committed_evaluator metric supports the low and medium tiers only; "
            f"the {tier or 'unknown'} tier needs platform_scorer"
        )
    return False, (
        "a program_output metric was printed by a process that ran agent code; "
        "it never supports a quality metric"
    )
