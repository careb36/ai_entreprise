"""The paths the factory's own sprints may not change without a person.

The graders, the evidence, CI, the positioning and this list itself
(``docs/design/rsi-loop.md``, section 3). The list is data: repository-relative
glob patterns in which ``**`` crosses folders and ``*`` and ``?`` do not.
``matches`` is a pure function over paths; it reads no file and decides
nothing: what a match means (a hold, a refused merge, a ``revert``) is up to
its caller.

Callers pass the paths the platform observed (the daemon's writes, a git
diff); a path a producer declares may add to a match, never remove one. The
list in force is the running backend's copy, never the one in a company's
project. ``.github/CODEOWNERS`` covers every pattern, and
``tests/test_protected_paths.py`` keeps the two in step (REQ-9).

The tests that hold the evidence to its behaviour are derived from the test
map in ``docs/architecture/contexts.json`` (``tests.files``): the caller reads
that file and passes its entries to ``evidence_test_patterns``.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

TESTS_FOLDER = "backend/tests/"
EVIDENCE_CONTEXT = "evidence"

# Each entry: the pattern, and why it is protected.
PROTECTED: tuple[tuple[str, str], ...] = (
    (
        "bench/**",
        "the exam and its answers: specifications, acceptance tests, the Verifier bench's cases, scorecards",
    ),
    ("scripts/bench/**", "the grader, the schema, the thresholds, the budget"),
    (
        "scripts/self_host/**",
        "the self-hosting harness and the target measures it holds",
    ),
    ("scripts/e2e/**", "the end-to-end checks"),
    (
        "vscode-daemon/sandbox.py",
        "the sandbox the target measure and the bench's grade run in (rsi-loop PR 12)",
    ),
    ("vscode-daemon/sandbox_image.py", "which image a grade ran, observed"),
    (
        "vscode-daemon/sandbox_cache.py",
        "imported by the grader on the host with the sandbox",
    ),
    (
        "vscode-daemon/run_logs.py",
        "imported by the grader on the host with the sandbox",
    ),
    ("scripts/rsi/**", "the script that decides keep or revert"),
    (
        "rsi/cycles/**",
        "the cycle records the watch after a merge judges against: B's medians and spread",
    ),
    (
        "backend/app/services/evidence/**",
        "the Verifier, the risk tier, the standards check",
    ),
    ("backend/tests/test_verificador_benchmark.py", "the test of the Verifier bench"),
    ("backend/tests/test_bench_*.py", "the tests of the bench"),
    (".github/workflows/**", "CI is the evidence of a run"),
    (".github/CODEOWNERS", "who must review a protected change"),
    (
        "scripts/protected_paths_check.py",
        "the CI check that requires a code owner's approval of a protected change",
    ),
    ("backend/tests/test_protected_paths_check.py", "the test of that check"),
    ("docs/POSITIONING.md", "the rules"),
    ("backend/app/services/contracts/protected_paths.py", "the list itself"),
    (
        "backend/tests/test_protected_paths.py",
        "the test that keeps the list and CODEOWNERS in step",
    ),
    ("scripts/check_architecture.py", "a ratchet CI enforces"),
    ("scripts/check_frontend_architecture.py", "a ratchet CI enforces"),
    ("scripts/check_file_sizes.py", "a ratchet CI enforces"),
    ("scripts/file_size_baseline.json", "a ratchet CI enforces"),
    (
        "docs/architecture/contexts.json",
        "the dependency rule and the test map this list reads",
    ),
    ("docs/architecture/frontend.json", "a ratchet CI enforces"),
    ("backend/app/history_check.py", "the legality of a task's history"),
    ("backend/app/services/tasks/board.py", "the rework count the bench reads"),
    (
        "backend/app/services/learning/procedures.py",
        "the keep and revert tolerances of procedures",
    ),
    (
        "backend/app/services/management/manager_policy.py",
        "what the Manager agent must always escalate",
    ),
    ("backend/app/services/platform/sod.py", "segregation of duties"),
)

PATTERNS: tuple[str, ...] = tuple(pattern for pattern, _ in PROTECTED)


class Match(NamedTuple):
    """A protected path and the first pattern of the list that matched it."""

    path: str
    pattern: str


def evidence_test_patterns(test_files: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """The test files whose contexts include evidence, from ``tests.files``."""
    return tuple(
        sorted(
            TESTS_FOLDER + name
            for name, contexts in test_files.items()
            if EVIDENCE_CONTEXT in contexts
        )
    )


def protected_patterns(
    test_files: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """The list, plus the evidence's tests when the test map is given."""
    if not test_files:
        return PATTERNS
    extra = [p for p in evidence_test_patterns(test_files) if p not in PATTERNS]
    return PATTERNS + tuple(extra)


def normalize(path: str, backslash_separates: bool = True) -> str | None:
    """A repository-relative path in POSIX form, or None outside the repository.

    With *backslash_separates*, a backslash is a folder separator (Windows);
    without it, a backslash is part of a name (POSIX).
    """
    text = (path or "").strip()
    if backslash_separates:
        text = text.replace("\\", "/")
    if not text:
        return None
    text = posixpath.normpath(text.lstrip("/"))
    if text in (".", "..") or text.startswith("../"):
        return None
    return text


def interpretations(path: str) -> tuple[str, ...]:
    """The path read as POSIX (a backslash is part of a name) and with
    backslashes as separators, each once, those inside the repository only.

    A caller cannot always know which the disk meant: ``bench/..\\frontend/x.py``
    is a folder named ``..\\frontend`` inside bench on POSIX and
    ``frontend/x.py`` on Windows. A path is protected if either reading is.
    """
    found: list[str] = []
    for backslash_separates in (False, True):
        text = normalize(path, backslash_separates)
        if text is not None and text not in found:
            found.append(text)
    return tuple(found)


def _compile(pattern: str) -> re.Pattern[str]:
    """``**`` crosses folders, ``*`` and ``?`` stay within one."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    # Case-insensitive: on a case-insensitive disk ``BENCH/x`` is ``bench/x``,
    # and a match only ever adds a person's review. DOTALL: a name may hold a
    # line break, and ``**`` must cross it as ``*`` and ``?`` already do.
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def matches(paths: Iterable[str], patterns: Sequence[str] = PATTERNS) -> list[Match]:
    """The paths that match a pattern, in their order, each once, with its first pattern.

    Each path is tried under both of its ``interpretations``; the match names
    the reading that matched. Two inputs are the same path only when every
    reading is the same: ``bench/a\\b.py`` and ``bench/a/b.py`` are two files
    on a POSIX disk, so both are kept.
    """
    compiled = [(pattern, _compile(pattern)) for pattern in patterns]
    found: list[Match] = []
    seen: set[tuple[str, ...]] = set()
    for raw in paths:
        readings = interpretations(raw)
        if not readings or readings in seen:
            continue
        hit = next(
            (
                Match(reading, pattern)
                for pattern, regex in compiled
                for reading in readings
                if regex.fullmatch(reading)
            ),
            None,
        )
        if hit is not None:
            seen.add(readings)
            found.append(hit)
    return found
