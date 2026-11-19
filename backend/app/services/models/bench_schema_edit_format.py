"""The scorecard's edit-format fields (schema v7), as ``bench_schema`` checks them.

A copy of ``scripts/bench/edit_format_card.problems`` (issue #689): a pure
function with the same messages, in the same order;
``tests/test_scorecard_schema_parity.py`` holds the two in step. The fields are
``edit_format`` (the format a run asked every model for), per spec
``edit_format`` (its deliveries' observations summed) and ``edit_formats`` (one
row per model and format, with its outcome: ``pass``, ``fail``, ``unusable`` or
``unmeasured`` with its reason; an unusable format is never a pass).

The formats are this context's own (``edit_format.FORMATS``), the module the
script reads without importing it, so the two cannot name different formats.
"""

from __future__ import annotations

from typing import Any

from services.models.bench_schema_blocks import count
from services.models.edit_format import FORMATS

EDIT_FORMAT_VERSION = 7
EDIT_FORMAT_OUTCOMES = ("pass", "fail", "unusable", "unmeasured")
SUMMARY_COUNTS = (
    "deliveries",
    "observed",
    "missing",
    "well_formed",
    "whole_files",
    "edit_blocks",
    "malformed_edit_blocks",
    "off_format",
    "rejected_edits",
    "tool_edits",
    "tool_edit_errors",
)
ROW_COUNTS = (
    "criteria_passed",
    "criteria_total",
    "rework_requests",
    "tokens_reported",
    *SUMMARY_COUNTS,
)


def _summary_problems(where: str, obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where} must be an object"]
    found = [
        f"{where}.{k} must be a count" for k in SUMMARY_COUNTS if not count(obj.get(k))
    ]
    requested = obj.get("requested")
    if not isinstance(requested, dict) or not all(count(v) for v in requested.values()):
        found.append(f"{where}.requested must map formats to counts")
    if not found and not (
        obj["observed"] + obj["missing"] == obj["deliveries"]
        and obj["well_formed"] <= obj["observed"]
    ):
        found.append(
            f"{where}: observed + missing = deliveries, well_formed <= observed"
        )
    return found


def _row_problems(at: str, row: Any) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    if row.get("format") not in FORMATS:
        found.append(f"{at}.format must be one of {', '.join(FORMATS)}")
    if not isinstance(row.get("model"), str) or not row.get("model"):
        found.append(f"{at}.model must be a non-empty string")
    if row.get("outcome") not in EDIT_FORMAT_OUTCOMES:
        found.append(f"{at}.outcome must be one of {', '.join(EDIT_FORMAT_OUTCOMES)}")
    found += [f"{at}.{k} must be a count" for k in ROW_COUNTS if not count(row.get(k))]
    if row.get("outcome") in ("unusable", "unmeasured") and not row.get("reason"):
        found.append(f"{at}: an {row.get('outcome')} row needs its reason")
    if (
        row.get("outcome") == "pass"
        and count(row.get("well_formed"))
        and row["well_formed"] == 0
    ):
        found.append(f"{at}: a format no delivery used well cannot pass")
    rate = row.get("well_formed_rate")
    if rate is not None and not (
        isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= rate <= 1
    ):
        found.append(f"{at}.well_formed_rate must be from 0 to 1, or null")
    return found


def edit_format_problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the card's edit-format fields; none before v7."""
    version = card.get("schema_version")
    specs = card.get("specs") or []
    if not (isinstance(version, int) and version >= EDIT_FORMAT_VERSION):
        return [
            f"{k} needs schema v{EDIT_FORMAT_VERSION}"
            for k in ("edit_format", "edit_formats")
            if k in card
        ] + [
            f"specs[{i}].edit_format needs schema v{EDIT_FORMAT_VERSION}"
            for i, s in enumerate(specs)
            if isinstance(s, dict) and "edit_format" in s
        ]
    found = []
    if "edit_format" in card and card["edit_format"] not in FORMATS:
        found.append(f"edit_format must be one of {', '.join(FORMATS)}")
    for index, spec in enumerate(specs):
        if isinstance(spec, dict) and "edit_format" in spec:
            found += _summary_problems(
                f"specs[{index}].edit_format", spec["edit_format"]
            )
    data = card.get("edit_formats")
    if data is None:
        return found
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return [*found, "edit_formats.rows must be a list"]
    for index, row in enumerate(data["rows"]):
        found += _row_problems(f"edit_formats.rows[{index}]", row)
    return found
