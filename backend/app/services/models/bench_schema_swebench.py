"""The scorecard's ``swebench`` block (schema v6), as ``bench_schema`` checks it.

A copy of ``scripts/bench/swebench_card.problems`` (issue #687): a pure
function with the same messages, in the same order;
``tests/test_scorecard_schema_parity.py`` holds the two in step. The block is
SWE-bench Lite run through the factory: each instance's outcome from its own
FAIL_TO_PASS and PASS_TO_PASS tests run by the scorer (``pass``, ``fail``, or
``unmeasured`` with its reason), never the factory's report.
"""

from __future__ import annotations

import re
from typing import Any

from services.models.bench_schema_blocks import count

SWEBENCH_VERSION = 6
SWEBENCH_PASS, SWEBENCH_FAIL, SWEBENCH_UNMEASURED = "pass", "fail", "unmeasured"
SWEBENCH_OUTCOMES = (SWEBENCH_PASS, SWEBENCH_FAIL, SWEBENCH_UNMEASURED)
_ID = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d{1,6}$")
_TOKENS = ("reported", "turns_reported", "turns_unreported")
_TEST_GROUPS = ("fail_to_pass", "pass_to_pass")


def resolved_rate(resolved: int, measured: int) -> float | None:
    """The resolve rate as the card writes it (``swebench_card._rate``)."""
    return round(resolved / measured, 4) if measured else None


def _group_problem(at: str, key: str, group: Any) -> list[str]:
    if group is None or (
        isinstance(group, dict)
        and count(group.get("passed"))
        and count(group.get("total"))
        and group["passed"] <= group["total"]
    ):
        return []
    return [f"{at}.{key} must be null or passed <= total counts"]


def _row_problems(at: str, row: Any, statuses: tuple[str, ...]) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    if not isinstance(row.get("instance_id"), str) or not _ID.match(row["instance_id"]):
        found.append(f"{at}.instance_id must be a SWE-bench instance id")
    for key in ("spec", "repo"):
        if not isinstance(row.get(key), str) or not row.get(key):
            found.append(f"{at}.{key} must be a non-empty string")
    if row.get("outcome") not in SWEBENCH_OUTCOMES:
        found.append(f"{at}.outcome must be one of {', '.join(SWEBENCH_OUTCOMES)}")
    if row.get("status") not in statuses:
        found.append(f"{at}.status must be one of {', '.join(statuses)}")
    if row.get("outcome") == SWEBENCH_UNMEASURED and not row.get("reason"):
        found.append(f"{at}: an unmeasured instance says why (reason)")
    for key in _TEST_GROUPS:
        found += _group_problem(at, key, row.get(key))
    if row.get("outcome") == SWEBENCH_PASS and not all(
        isinstance(row.get(k), dict) and row[k].get("passed") == row[k].get("total")
        for k in _TEST_GROUPS
    ):
        found.append(
            f"{at}: a resolved instance passed every FAIL_TO_PASS and PASS_TO_PASS test"
        )
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
    found += [
        f"{at}.tokens.{k} must be a count" for k in _TOKENS if not count(tokens.get(k))
    ]
    return found


def _dataset_problems(data: dict[str, Any]) -> list[str]:
    dataset = data.get("dataset") if isinstance(data.get("dataset"), dict) else {}
    found = []
    if not re.fullmatch(r"[0-9a-f]{40}", str(dataset.get("revision", ""))):
        found.append("swebench.dataset.revision must be a commit sha")
    for key in ("name", "source", "license", "split"):
        if not isinstance(dataset.get(key), str) or not dataset.get(key):
            found.append(f"swebench.dataset.{key} must be a non-empty string")
    pins = dataset.get("instances")
    if not isinstance(pins, dict) or not all(
        re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in pins.values()
    ):
        found.append("swebench.dataset.instances must map each id to its pinned sha256")
    return found


def swebench_problems(card: dict[str, Any], statuses: tuple[str, ...]) -> list[str]:
    """What is wrong with the optional ``swebench`` block of *card* (v6)."""
    if "swebench" not in card:
        return []
    version = card.get("schema_version")
    found = []
    if not (isinstance(version, int) and version >= SWEBENCH_VERSION):
        found.append(f"swebench needs schema v{SWEBENCH_VERSION}")
    data = card["swebench"]
    if not isinstance(data, dict):
        return [*found, "swebench must be an object"]
    found += _dataset_problems(data)
    rows = data.get("instances")
    if not isinstance(rows, list):
        return [*found, "swebench.instances must be a list"]
    for index, row in enumerate(rows):
        found += _row_problems(f"swebench.instances[{index}]", row, statuses)
    if found:
        return found
    for key in ("subset", "skipped"):
        if not isinstance(data.get(key), list):
            found.append(f"swebench.{key} must be a list")
    ids = [r["instance_id"] for r in rows]
    if len(set(ids)) != len(ids):
        found.append("swebench.instances names an instance twice")
    ran = {s.get("name") for s in card.get("specs") or [] if isinstance(s, dict)}
    found += [
        f"swebench: {r['spec']} is also a reference specification's entry"
        for r in rows
        if r["spec"] in ran
    ]
    resolved = sum(r["outcome"] == SWEBENCH_PASS for r in rows)
    failed = sum(r["outcome"] == SWEBENCH_FAIL for r in rows)
    expected = {
        "graded": len(rows),
        "measured": resolved + failed,
        "resolved": resolved,
        "failed": failed,
        "unmeasured": sum(r["outcome"] == SWEBENCH_UNMEASURED for r in rows),
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }
    found += [
        f"swebench.{k} does not match its instances"
        for k, v in expected.items()
        if data.get(k) != v
    ]
    if data.get("resolved_rate") != resolved_rate(resolved, resolved + failed):
        found.append(
            "swebench.resolved_rate must be resolved / measured, or null when none was measured"
        )
    return found
