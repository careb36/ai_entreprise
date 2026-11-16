#!/usr/bin/env python3
"""
A ratchet on file size: a file over the limit may not grow.

Two groups of files are held to the same LIMIT, each with its own baseline:

- source files (backend app code, the daemon, the proxy, scripts, the
  frontend's src, tests excluded), listed in scripts/file_size_baseline.json;
- test files (backend/tests, llm-proxy/tests, vscode-daemon/tests and the
  frontend's *.test.ts(x)), listed in scripts/test_file_size_baseline.json.

The files already over the limit are listed in their group's baseline with
the size they had; each may shrink but never grow, and a new file may not
cross the limit. When a listed file shrinks, ``--update`` lowers its entry
(it never raises one) and drops the entries of files back under the limit or
gone.

Exit status 0 when every file keeps to the rule, 1 otherwise. Run from
anywhere: paths are resolved from the repository root.
"""

import json
import subprocess
import sys
from pathlib import Path

LIMIT = 600
ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "file_size_baseline.json"
TEST_BASELINE = ROOT / "scripts" / "test_file_size_baseline.json"
PATTERNS = (
    "backend/app/*.py",
    "vscode-daemon/*.py",
    "llm-proxy/*.py",
    "scripts/*.py",
    "frontend/src/*.ts",
    "frontend/src/*.tsx",
)
TEST_PATTERNS = (
    "backend/tests/*.py",
    "llm-proxy/tests/*.py",
    "vscode-daemon/tests/*.py",
    "frontend/src/*.test.ts",
    "frontend/src/*.test.tsx",
)


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or ".test." in name or name.startswith("test_")


def _listed(patterns: tuple[str, ...]) -> list[str]:
    # git pathspecs: a single * matches across directories.
    return subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


def source_files() -> list[str]:
    return sorted(path for path in _listed(PATTERNS) if not _is_test(path))


def listed_test_files() -> list[str]:
    return sorted(_listed(TEST_PATTERNS))


def line_count(path: str) -> int:
    with open(ROOT / path, encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def check(files: list[str], baseline_path: Path, update: bool) -> list[str]:
    """Hold ``files`` to the limit against ``baseline_path``; return problems."""
    baseline: dict[str, int] = (
        json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    )
    sizes = {path: line_count(path) for path in files}
    name = baseline_path.relative_to(ROOT)

    problems = []
    for path, lines in sizes.items():
        if lines <= LIMIT:
            continue
        allowed = baseline.get(path)
        if allowed is None:
            problems.append(
                f"{path}: {lines} lines, over the {LIMIT}-line limit; split it"
            )
        elif lines > allowed:
            problems.append(
                f"{path}: grew from {allowed} to {lines} lines; it is over the "
                f"{LIMIT}-line limit, so it may only shrink"
            )

    tightened = {
        path: min(allowed, sizes[path])
        for path, allowed in baseline.items()
        if sizes.get(path, 0) > LIMIT
    }
    if update:
        if problems:
            print(f"Not updating {name} while files break the rule:", file=sys.stderr)
        else:
            baseline_path.write_text(
                json.dumps(tightened, indent=2, sort_keys=True) + "\n"
            )
            print(f"{name} written: {len(tightened)} file(s) over {LIMIT} lines.")
    elif tightened != baseline:
        print(
            f"{name} can be tightened (files shrank or went under the "
            "limit): run scripts/check_file_sizes.py --update and commit it."
        )
    return problems


def main(argv: list[str]) -> int:
    update = "--update" in argv
    problems = check(source_files(), BASELINE, update)
    problems += check(listed_test_files(), TEST_BASELINE, update)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
