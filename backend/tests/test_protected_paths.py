"""The protected paths (``services.contracts.protected_paths``, docs/design/rsi-loop.md §3).

The matcher: ``**`` crosses folders, ``*`` does not, a path outside the list
does not match, the list protects itself, the evidence's tests come from the
test map in ``docs/architecture/contexts.json``. And REQ-9: every pattern is
covered by a line of ``.github/CODEOWNERS`` with an owner, and removing one
of those lines fails this suite.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from services.contracts import protected_paths as pp

REPO = Path(__file__).resolve().parents[2]
CODEOWNERS = REPO / ".github" / "CODEOWNERS"
CONTEXTS = REPO / "docs" / "architecture" / "contexts.json"


def _test_map() -> dict[str, list[str]]:
    return json.loads(CONTEXTS.read_text())["tests"]["files"]


def _patterns() -> tuple[str, ...]:
    return pp.protected_patterns(_test_map())


# --- the matcher ----------------------------------------------------------------


def test_a_double_star_crosses_folders():
    found = pp.matches(
        [
            "bench/README.md",
            "bench/specs/01-cli-wordcount/acceptance/test_x.py",
            "scripts/bench/compare.py",
            "backend/app/services/evidence/verification.py",
            ".github/workflows/sub/dir/x.yml",
        ]
    )
    assert [m.pattern for m in found] == [
        "bench/**",
        "bench/**",
        "scripts/bench/**",
        "backend/app/services/evidence/**",
        ".github/workflows/**",
    ]


def test_a_single_star_stays_in_its_folder():
    assert pp.matches(["backend/tests/test_bench_compare.py"]) == [
        pp.Match("backend/tests/test_bench_compare.py", "backend/tests/test_bench_*.py")
    ]
    assert pp.matches(["backend/tests/test_bench_x/deep.py"]) == []
    assert pp.matches(["a/b/c.py"], ["a/*.py"]) == []
    assert pp.matches(["a/c.py"], ["a/*.py"]) == [pp.Match("a/c.py", "a/*.py")]
    assert pp.matches(["a/b/c.py"], ["a/**/c.py"]) == [
        pp.Match("a/b/c.py", "a/**/c.py")
    ]
    assert pp.matches(["a/c.py"], ["a/**/c.py"]) == [pp.Match("a/c.py", "a/**/c.py")]
    assert pp.matches(["a/bc.py"], ["a/?c.py"]) == [pp.Match("a/bc.py", "a/?c.py")]
    assert pp.matches(["a/b/c.py"], ["a?b/c.py"]) == []


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/services/planning/adr_docs.py",
        "backend/app/routers/architectures.py",
        "backend/tests/test_adr_docs.py",
        "docs/POSITIONING.md.bak",
        "benchmarks/x.py",
        "bench",
        "frontend/src/App.tsx",
        "scripts/check_context_docs.py",
        "docs/architecture/MIGRATION.md",
        "",
        "../bench/x.py",
    ],
)
def test_a_path_outside_the_list_does_not_match(path):
    assert pp.matches([path], _patterns()) == []


def test_the_list_protects_itself_and_its_guards():
    for path in (
        "backend/app/services/contracts/protected_paths.py",
        "backend/tests/test_protected_paths.py",
        ".github/CODEOWNERS",
        "docs/POSITIONING.md",
        "docs/architecture/contexts.json",
    ):
        assert pp.matches([path]) == [pp.Match(path, path)], path


def test_paths_are_normalised_before_matching():
    found = pp.matches(
        [
            "./bench/x.py",
            "/scripts/bench/run.sh",
            "scripts\\self_host\\self_host.py",
            "backend/app/../app/history_check.py",
            "BENCH/specs/x.py",
            "bench/x.py",
        ]
    )
    assert [m.path for m in found] == [
        "bench/x.py",
        "scripts/bench/run.sh",
        "scripts/self_host/self_host.py",
        "backend/app/history_check.py",
        "BENCH/specs/x.py",
    ]


def test_the_owner_s_list_is_in_force():
    for path in (
        "scripts/e2e/run.sh",
        "scripts/check_architecture.py",
        "scripts/check_frontend_architecture.py",
        "scripts/check_file_sizes.py",
        "scripts/file_size_baseline.json",
        "docs/architecture/frontend.json",
        "backend/app/history_check.py",
        "backend/app/services/tasks/board.py",
        "backend/app/services/learning/procedures.py",
        "backend/app/services/management/manager_policy.py",
        "backend/app/services/platform/sod.py",
        "backend/tests/test_verificador_benchmark.py",
    ):
        assert pp.matches([path]), path
    assert len(pp.PATTERNS) == len(set(pp.PATTERNS))
    assert all(why for _, why in pp.PROTECTED)


def test_the_evidence_tests_come_from_the_test_map():
    derived = pp.evidence_test_patterns(
        {
            "test_a.py": ["evidence"],
            "test_b.py": ["loop"],
            "test_c.py": ["repo", "evidence"],
        }
    )
    assert derived == ("backend/tests/test_a.py", "backend/tests/test_c.py")
    assert pp.protected_patterns() == pp.PATTERNS
    mapped = pp.evidence_test_patterns(_test_map())
    assert "backend/tests/test_secops_checks.py" in mapped
    assert set(mapped) <= set(_patterns())
    assert pp.matches(["backend/tests/test_secops_checks.py"], _patterns())
    assert not pp.matches(["backend/tests/test_secops_checks.py"])


def test_every_pattern_names_something_that_exists():
    for pattern in _patterns():
        literal = re.split(r"[*?]", pattern, maxsplit=1)[0]
        if "*" in pattern or "?" in pattern:
            assert any(REPO.glob(pattern)) or (REPO / literal).exists(), pattern
        else:
            assert (REPO / pattern).is_file(), pattern


# --- REQ-9: CODEOWNERS covers the list ------------------------------------------


def _rules(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rules.append((pattern, owners))
    return rules


def _owners_rule_matches(rule: str, path: str) -> bool:
    """The subset of CODEOWNERS syntax the file uses: anchored paths, folders, ``*``."""
    if rule == "*":
        return True
    body = rule.lstrip("/")
    if body.endswith("/"):
        return path.startswith(body)
    regex = re.escape(body).replace(r"\*", "[^/]*")
    return bool(re.fullmatch(regex, path) or re.match(regex + "/", path))


def _covers(rule: str, pattern: str) -> bool:
    """A CODEOWNERS line other than ``*`` that matches every path the pattern can."""
    if rule == "*" or not rule.startswith("/"):
        return False
    body = rule[1:]
    if body.endswith("/"):
        return pattern.startswith(body)
    if "*" in body:
        return body == pattern
    return pattern == body or pattern.startswith(body + "/")


def _sample(pattern: str) -> str:
    return (
        pattern.replace("**", "sample/deep/file.py")
        .replace("*", "sample")
        .replace("?", "s")
    )


def uncovered(patterns: tuple[str, ...], codeowners: str) -> list[str]:
    """The patterns no owned CODEOWNERS line covers, the last matching line winning."""
    rules = _rules(codeowners)
    missing = []
    for pattern in patterns:
        sample = _sample(pattern)
        last = None
        for rule, owners in rules:
            if _owners_rule_matches(rule, sample):
                last = (rule, owners)
        if last is None or not last[1] or not _covers(last[0], pattern):
            missing.append(pattern)
    return missing


def test_codeowners_covers_every_protected_pattern():
    assert uncovered(_patterns(), CODEOWNERS.read_text()) == []


def test_removing_a_codeowners_line_fails_the_check():
    text = CODEOWNERS.read_text()
    without_bench = "\n".join(
        line for line in text.splitlines() if not line.startswith("/bench/ ")
    )
    assert uncovered(_patterns(), without_bench) == ["bench/**"]
    without_sod = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("/backend/app/services/platform/sod.py")
    )
    assert uncovered(_patterns(), without_sod) == [
        "backend/app/services/platform/sod.py"
    ]


def test_the_catch_all_and_an_ownerless_override_do_not_cover():
    assert uncovered(("bench/**",), "*  @owner\n") == ["bench/**"]
    assert uncovered(("bench/**",), "/bench/  @owner\n/bench/  \n") == ["bench/**"]
    assert uncovered(("bench/**",), "/bench/specs/  @owner\n") == ["bench/**"]
    assert uncovered(("bench/**",), "/bench/  @owner\n") == []
    assert (
        uncovered(("backend/tests/test_bench_*.py",), "/backend/tests/  @owner\n") == []
    )
    assert uncovered(
        ("backend/tests/test_bench_*.py",), "/backend/tests/test_bench_x.py  @owner\n"
    ) == ["backend/tests/test_bench_*.py"]


# --- review findings: the evidence's tests, backslashes, line breaks ------------


def _imports_the_evidence(path: Path) -> bool:
    """Whether a test file imports ``services.evidence`` (or ``app.services.evidence``)."""
    roots = (["services", "evidence"], ["app", "services", "evidence"])
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if any(parts[: len(r)] == r for r in roots):
                return True
            if parts in (["services"], ["app", "services"]) and any(
                alias.name == "evidence" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if any(parts[: len(r)] == r for r in roots):
                    return True
    return False


def test_every_test_that_imports_the_evidence_is_protected():
    tests = REPO / "backend" / "tests"
    importing = sorted(
        f"backend/tests/{p.name}"
        for p in tests.glob("test_*.py")
        if _imports_the_evidence(p)
    )
    assert "backend/tests/test_verification_checks.py" in importing
    assert "backend/tests/test_grounding.py" in importing
    patterns = _patterns()
    unprotected = [p for p in importing if not pp.matches([p], patterns)]
    assert unprotected == []


def test_a_backslash_is_read_both_ways():
    # On POSIX, ``..\frontend`` is a folder inside bench; on Windows, a step up.
    assert pp.matches(["bench/..\\frontend/x.py"]) == [
        pp.Match("bench/..\\frontend/x.py", "bench/**")
    ]
    # Either reading protects: a Windows path still matches.
    assert pp.matches(["scripts\\bench\\run.sh"]) == [
        pp.Match("scripts/bench/run.sh", "scripts/bench/**")
    ]
    # Outside the repository under one reading, inside and unprotected under
    # the other: no match.
    assert pp.matches(["..\\bench/x.py"]) == []
    assert pp.interpretations("bench/..\\frontend/x.py") == (
        "bench/..\\frontend/x.py",
        "frontend/x.py",
    )
    assert pp.interpretations("bench/x.py") == ("bench/x.py",)
    assert pp.interpretations("../x") == ()


def test_a_line_break_in_a_path_still_matches():
    assert pp.matches(["bench/line\nbreak.py"]) == [
        pp.Match("bench/line\nbreak.py", "bench/**")
    ]
    assert pp.matches(["a/line\nbreak.py"], ["a/**"]) == [
        pp.Match("a/line\nbreak.py", "a/**")
    ]
    assert pp.matches(["a/x\ny/c.py"], ["a/**/c.py"]) == [
        pp.Match("a/x\ny/c.py", "a/**/c.py")
    ]
    # ``*`` and ``?`` still stop at a folder, line break or not.
    assert pp.matches(["a/b\n/c.py"], ["a/*.py"]) == []
    assert pp.matches(["a/b\nc.py"], ["a/*.py"]) == [pp.Match("a/b\nc.py", "a/*.py")]
    assert pp.matches(["a/\n.py"], ["a/?.py"]) == [pp.Match("a/\n.py", "a/?.py")]
    assert pp.matches(["a/\n/.py"], ["a/?/.py"]) == [pp.Match("a/\n/.py", "a/?/.py")]
    assert pp.matches(["a//.py"], ["a/?.py"]) == []


def test_two_files_that_share_a_reading_are_both_kept():
    backslashed, nested = "bench/a\\b.py", "bench/a/b.py"
    for order in ([backslashed, nested], [nested, backslashed]):
        found = pp.matches(order)
        assert len(found) == 2, found
    assert len(pp.matches([nested, "./bench/a/b.py"])) == 1
