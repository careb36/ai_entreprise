"""The changelog fragments' check, ``scripts/check_changelog.py`` (issue #731).

The check holds the repository's ``changelog.d/`` fragments to the format
``scripts/release_notes.py`` folds, and fails while CONTRIBUTING.md or the
pull request template (PULL_REQUEST_TEMPLATE.md) still tell a contributor to
add a line under ``[Unreleased]`` in CHANGELOG.md. Running it here is how CI
runs it without a workflow of its own; ``scripts/check.sh`` runs it in both
modes (``test_check_script.py``).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_changelog.py"
_spec = importlib.util.spec_from_file_location("check_changelog", SCRIPT)
assert _spec and _spec.loader
check_changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_changelog)

CONTRIBUTING = (
    "# Contributing\n\nEach pull request records its change as a fragment in\n"
    "`changelog.d/<issue>-<slug>.md`; leave CHANGELOG.md alone.\n"
)
TEMPLATE = (
    "## Checklist\n- [ ] I recorded the change as a fragment, "
    "`changelog.d/<issue>-<slug>.md`\n"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / ".github").mkdir()
    (tmp_path / "CONTRIBUTING.md").write_text(CONTRIBUTING)
    (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text(TEMPLATE)
    folder = tmp_path / "changelog.d"
    folder.mkdir()
    (folder / "README.md").write_text("# Changelog fragments\n\nAdded\n")
    (folder / "731-good.md").write_text("Changed\n\n* A change (#731).\n")
    return tmp_path


def test_the_repository_passes():
    result = _run()
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("check_changelog: ok")


def test_a_good_tree_passes_and_counts_its_fragments(tree):
    result = _run("--root", str(tree))
    assert result.returncode == 0, result.stderr
    assert "(1 fragment(s)" in result.stdout


@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("732-bad.md", "Misc\n\n* x\n", "'Misc', an unknown section"),
        ("732-bad.md", "Fixed\n", "the fragment is empty"),
        ("732-bad.md", "Fixed\n\n## Fixed\n* x\n", "the entry has a heading line"),
        ("Bad Name.md", "Fixed\n\n* x\n", "the name is not <issue>-<slug>.md"),
        ("732-bad.markdown", "Fixed\n\n* x\n", "the name is not"),
    ],
)
def test_a_bad_fragment_fails_with_its_name_and_reason(tree, name, text, reason):
    (tree / "changelog.d" / name).write_text(text)
    result = _run("--root", str(tree))
    assert result.returncode == 1
    assert f"changelog.d/{name}: " in result.stderr
    assert reason in result.stderr


@pytest.mark.parametrize(
    "line",
    [
        "- [ ] I added a line under [Unreleased] in CHANGELOG.md",
        "Each pull request adds its line under `[Unreleased]` in `CHANGELOG.md`.",
        "Update the [Unreleased] section of CHANGELOG.md with your change.",
        "Add to CHANGELOG.md, under [Unreleased], one line.",
    ],
)
@pytest.mark.parametrize("rel", ["CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md"])
def test_an_instruction_still_asking_for_a_changelog_edit_fails(tree, rel, line):
    path = tree / rel
    path.write_text(path.read_text() + line + "\n")
    result = _run("--root", str(tree))
    assert result.returncode == 1
    assert f"{rel}:" in result.stderr
    assert "still asks for a line under [Unreleased]" in result.stderr


@pytest.mark.parametrize("rel", ["CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md"])
def test_an_instruction_that_does_not_name_the_fragments_fails(tree, rel):
    (tree / rel).write_text("# Nothing about the changelog\n")
    problems = check_changelog.problems(tree)
    assert problems == [
        f"{rel}: does not name changelog.d/, where a change is recorded"
    ]


def test_a_missing_instruction_fails(tree):
    (tree / "CONTRIBUTING.md").unlink()
    assert check_changelog.problems(tree) == ["CONTRIBUTING.md: missing"]


def test_mentioning_the_changelog_otherwise_is_fine(tree):
    path = tree / "CONTRIBUTING.md"
    path.write_text(
        path.read_text()
        + "Releases fold the fragments into CHANGELOG.md.\n"
        + "Read [Unreleased] with `release_notes.py unreleased`.\n"
    )
    assert check_changelog.problems(tree) == []
