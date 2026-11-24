"""The Verifier's ``tests_removed`` check: test definitions a delivery lost.

Design: ``docs/design/move-and-delete-files.md`` (Residual risk). In sprint 6
(2026-11-24) a Developer delivered a whole 642-line test file with empty
content and rewrote two other test files from memory: the old tests were gone
and nothing counted them.

What is counted comes from the daemon's report of each file it wrote or
deleted (``workspace_writes``): the unified diff of the file's bytes before
and after, which the daemon computed from the disk (``vscode-daemon/file_diff.py``).
Never the producer's account. For every test file (``test_*.py``,
``*_test.py``, ``*.test.*`` and ``*.spec.*`` scripts), a removed line that
defines a test (Python ``def test_…``/``async def test_…`` at any indent, a
JavaScript ``it("…")``/``test("…")`` call) takes that name away and an added
line gives it back. The names are summed over the whole delivery, so a test
moved to another test file of the same delivery is not lost, nor one whose
signature line changed. A name removed more often than it is added is lost.

A lost name is a reason for a person, not a fault, as a protected path is
(``protected_check``): a test may be removed on purpose (a feature taken out,
a test renamed), and a count of names cannot tell that from a test thrown
away. The check passes with the names in ``removed`` and the observation, and
the ``risk_tier`` check rises to ``high`` (``risk.held_for_a_person``), so the
task waits for a person at every autonomy level.

A test file the delivery modified or deleted without a diff in the report
(too large, cut for the reply's size, binary, an older daemon) cannot be
counted: the check is ``unverifiable`` and names it.

Not caught: a test whose body was hollowed out under the same name, a test
added under a removed test's name, tests written by a console producer (its
report has no diffs) and test files outside these name patterns.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

CHECK_NAME = "tests_removed"
HOLD_AREA = "tests"
_QUOTED = 20

# The sha256 of no bytes: a file that held nothing had no test to lose.
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PY_FILE = re.compile(r"^(?:test_.*|.*_test)\.py$")
_JS_FILE = re.compile(r"^.+\.(?:test|spec)\.(?:[cm]?[jt]sx?)$")
_PY_TEST = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_TEST = re.compile(
    r"^\s*(?:it|test)(?:\.(?:only|skip|todo|concurrent|fails))*\s*\(\s*"
    r"(?P<q>[\"'`])(?P<name>.+?)(?P=q)"
)


def is_test_file(path: str) -> bool:
    """A file a test runner collects by its name."""
    name = PurePosixPath(path.replace("\\", "/")).name
    return bool(_PY_FILE.match(name) or _JS_FILE.match(name))


def _test_name(path: str, line: str) -> str | None:
    if path.endswith(".py"):
        match = _PY_TEST.match(line)
        return match.group(1) if match else None
    match = _JS_TEST.match(line)
    return match.group("name") if match else None


def diff_tests(path: str, diff: str) -> tuple[list[str], list[str]]:
    """The test names on the removed and on the added lines of one file's diff."""
    removed: list[str] = []
    added: list[str] = []
    in_body = False
    for line in diff.split("\n"):
        if line.startswith("@@"):
            in_body = True  # Before the first hunk, ---/+++ are the header.
            continue
        if not in_body or not line or line[0] not in "+-":
            continue
        name = _test_name(path, line[1:])
        if name:
            (removed if line[0] == "-" else added).append(name)
    return removed, added


def _uncounted_reason(write: dict[str, Any]) -> str | None:
    """Why a test file's change has no diff to count, or ``None`` when it has one
    or could not have lost a test (a new file, an unchanged one)."""
    if write.get("change") in ("added", "unchanged"):
        return None
    if write.get("previous_sha256") == _EMPTY_SHA256:
        return None
    if isinstance(write.get("diff"), str) and write["diff"]:
        return None
    omitted = write.get("diff_omitted")
    return f"no diff ({omitted})" if omitted else "no diff in the daemon's report"


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The test names the delivery removed and did not add back anywhere in it.

    ``None`` when the daemon reported no write or deletion of a test file.
    """
    writes = (evidence or {}).get("workspace_writes")
    if not isinstance(writes, list):
        return None
    files = [
        w
        for w in writes
        if isinstance(w, dict)
        and w.get("status") in ("written", "deleted")
        and isinstance(w.get("path"), str)
        and is_test_file(w["path"])
    ]
    if not files:
        return None
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    removed_from: dict[str, str] = {}
    uncounted: list[dict[str, str]] = []
    for write in files:
        path = write["path"]
        reason = _uncounted_reason(write)
        if reason:
            uncounted.append({"path": path, "reason": reason})
            continue
        gone, new = diff_tests(path, write.get("diff") or "")
        removed.update(gone)
        added.update(new)
        for name in gone:
            removed_from.setdefault(name, path)
    lost = removed - added
    listed = [
        {"name": name, "path": removed_from[name], "count": count}
        for name, count in sorted(
            lost.items(), key=lambda kv: (removed_from[kv[0]], kv[0])
        )
    ]
    parts = []
    if listed:
        shown = ", ".join(f"{t['name']} ({t['path']})" for t in listed[:_QUOTED])
        more = f" (and {len(listed) - _QUOTED} more)" if len(listed) > _QUOTED else ""
        parts.append(
            f"{sum(lost.values())} test definition(s) removed and not added back "
            f"anywhere in the delivery, a person decides: {shown}{more}"
        )
    else:
        parts.append(
            f"no test lost in {len(files) - len(uncounted)} test file(s): "
            f"{sum(removed.values())} definition(s) removed, "
            f"{sum(added.values())} added"
        )
    if uncounted:
        parts.append(
            "not counted: "
            + ", ".join(f"{u['path']} ({u['reason']})" for u in uncounted[:_QUOTED])
        )
    return {
        "name": CHECK_NAME,
        "outcome": "unverifiable" if uncounted else "pass",
        "observation": "; ".join(parts),
        "removed": listed,
        "uncounted": uncounted,
        "held": bool(listed),
    }


def hold_reason(result: dict[str, Any] | None) -> str | None:
    """The reason the ``risk_tier`` check rises to ``high``, or ``None``."""
    if not result or not result.get("held"):
        return None
    names = [t["name"] for t in result.get("removed") or []]
    return (
        "tests removed by the delivery: "
        + ", ".join(names[:5])
        + (f" (and {len(names) - 5} more)" if len(names) > 5 else "")
    )
