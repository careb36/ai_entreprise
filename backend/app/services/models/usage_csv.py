"""The usage report as CSV (issue #512, phase 5; ``docs/design/TEAMS_AND_USAGE.md``).

Pure: the report ``usage_report.report`` returns, written as one CSV document.
The first lines are comment rows naming the company, the range, the period,
the time zone and when the report was generated; then one header row and one
row per dimension and key (``period``, ``person``, ``team``, ``role``,
``producer``, ``model``), after the ``total`` row.

A spreadsheet runs a cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or
a carriage return as a formula (CSV injection, OWASP WSTG-INPV / "CSV
Injection"). A label is text people or agents wrote: a person's name, a
team's name, an agent's role or a model id the proxy reported. Every text
cell that starts with one of those characters is prefixed with ``'``, so the
spreadsheet shows it as text; numbers are written as numbers and never
prefixed. The ``csv`` module quotes separators, quotes and line breaks.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from services.models.usage_report import COLUMNS, DIMENSIONS

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
UNATTRIBUTED = "(unattributed)"
HEADER = ("dimension", "key", "label", *COLUMNS[:-2], "cost_usd", COLUMNS[-1])


def safe_text(value: Any) -> str:
    """*value* as a cell spreadsheets keep as text (see the module)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _usd(cents: float | None) -> str:
    # The cost of the priced turns; empty, never 0, when none was priced.
    return "" if cents is None else f"{cents / 100:.6f}"


def _cells(dimension: str, row: dict[str, Any]) -> list[Any]:
    key = row.get("key")
    label = row.get("label")
    return [
        dimension,
        safe_text(UNATTRIBUTED if key is None else key),
        safe_text(UNATTRIBUTED if label is None and key is None else label),
        *(int(row.get(c) or 0) for c in COLUMNS[:-2]),
        _usd(row.get("cost_cents")),
        int(row.get("cost_unknown_turns") or 0),
    ]


def _teams_line(history: dict[str, Any]) -> str:
    # Which part of the range keeps today's teams (issue #702).
    cover = history.get("current_teams_cover")
    if not cover:
        return "teams: each person's teams at the time of the work"
    return (
        f"teams: today's teams for work from {cover['since']} to before "
        f"{cover['before']}; the teams at the time of the work after it"
    )


def render(report: dict[str, Any], company_name: str | None = None) -> str:
    """The CSV document for *report*."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    company = f"{report['company_id']}"
    if company_name:
        company += f" ({company_name})"
    for line in (
        "La Fabrique usage report",
        f"company: {company}",
        (
            f"period: {report['since']} to {report['until']} inclusive, "
            f"{report['timezone']}, by {report['period']}"
        ),
        f"generated: {report['generated_at']}",
        "cost_usd: the priced turns only; cost_unknown_turns are not in it",
        _teams_line(report.get("team_history") or {}),
    ):
        writer.writerow([safe_text("# " + line)])
    writer.writerow(HEADER)
    writer.writerow(_cells("total", {"key": "all", "label": "all", **report["totals"]}))
    for dimension in DIMENSIONS:
        for row in report[f"by_{dimension}"]:
            writer.writerow(_cells(dimension, row))
    return out.getvalue()
