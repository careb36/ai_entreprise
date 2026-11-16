"""The path filter ``changes.yml`` runs (``scripts/changed_paths.py``).

``changes.yml`` holds the one mapping from a path filter to a CI job and hands
it to this script, which diffs the pull request against its merge base and
prints one ``<filter>=true|false`` line per filter for the step to append to
``$GITHUB_OUTPUT``. These tests hold the three parts of that:

- the glob: ``**`` crosses folders and ``*`` does not;
- the filter text: only the shape the workflow writes is read, and anything
  else stops the script rather than reading a filter as empty;
- the diff: what the pull request changes, both sides of a rename, and a
  failure that prints nothing so the callers fall back to running everything.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "changed_paths", REPO / "scripts" / "changed_paths.py"
)
assert _spec and _spec.loader
changed_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changed_paths)

FILTERS = {
    "backend": ["backend/**"],
    "docs": ["docs/**"],
    "changelog": ["CHANGELOG.md"],
}


# --- the glob ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "matched"),
    [
        # `**` crosses folders, `*` does not.
        ("backend/**", "backend/app/main.py", True),
        ("backend/**", "backend/x", True),
        ("backend/**", "backend", False),
        ("backend/**", "backend-service/app.py", False),
        ("backend/*", "backend/app/main.py", False),
        ("scripts/*", "scripts/check.sh", True),
        ("scripts/*", "scripts/e2e/run.sh", False),
        # `**/` also matches no folder at all.
        ("**/*.md", "README.md", True),
        ("**/*.md", "docs/x/y.md", True),
        ("docs/adr/**", "docs/adr/0001-x.md", True),
        # The shapes the workflow actually lists.
        ("docker-compose*.yml", "docker-compose.yml", True),
        ("docker-compose*.yml", "deploy/docker-compose.yml", False),
        (".github/workflows/**", ".github/workflows/changes.yml", True),
        # A leading dot matches, so a filter is never narrower than it reads.
        ("*", ".env.example", True),
        ("**/*.md", ".hidden/x.md", True),
        # Only `*` and `**` are special; everything else is literal.
        ("a?b", "a?b", True),
        ("a?b", "axb", False),
        ("a[b", "a[b", True),
        ("a.b", "axb", False),
    ],
)
def test_the_glob_crosses_folders_only_with_double_star(pattern, path, matched):
    assert bool(changed_paths.glob_regex(pattern).match(path)) is matched


# --- the filter text ---------------------------------------------------------------


def test_the_filters_are_read_without_a_yaml_parser():
    text = (
        "workflows:\n"
        "  - '.github/workflows/**'\n"
        "backend:\n"
        "  - 'backend/**'\n"
        '  - "CHANGELOG.md"\n'
    )
    assert changed_paths.parse_filters(text) == {
        "workflows": [".github/workflows/**"],
        "backend": ["backend/**", "CHANGELOG.md"],
    }


def test_comments_and_blank_lines_are_skipped():
    text = "\n# a comment\nbackend:\n\n  - 'backend/**'\n  # another\n"
    assert changed_paths.parse_filters(text) == {"backend": ["backend/**"]}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("# only a comment\n", "no filter found"),
        ("backend:\n", "filter without a glob: backend"),
        ("backend:\n  - 'a'\nbackend:\n  - 'b'\n", "declared twice"),
        ("- 'backend/**'\n", "expected 'name:'"),
        # The mapping form of the action this replaces is not this shape.
        ("backend:\n  paths:\n    - 'backend/**'\n", "expected 'name:'"),
        ("backend: 'backend/**'\n", "expected 'name:'"),
        ("backend:\n  paths: ['backend/**']\n", "expected 'name:'"),
    ],
)
def test_any_other_shape_stops_the_script(text, reason):
    with pytest.raises(changed_paths.FilterError) as error:
        changed_paths.parse_filters(text)
    assert reason in str(error.value)


def test_a_file_matches_every_filter_it_touches():
    assert changed_paths.matching(FILTERS, "backend/app/main.py") == ["backend"]
    assert changed_paths.matching(FILTERS, "README.md") == []


def test_each_filter_says_whether_any_file_matched():
    assert changed_paths.outputs(FILTERS, ["docs/TESTING.md", "backend/x.py"]) == {
        "backend": True,
        "docs": True,
        "changelog": False,
    }
    assert changed_paths.outputs(FILTERS, ["CHANGELOG.md"]) == {
        "backend": False,
        "docs": False,
        "changelog": True,
    }


# --- the diff ----------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    return tmp_path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(root, "rev-parse", "HEAD")


def test_the_changed_files_are_the_ones_the_branch_changes(repo: Path):
    _write(repo, "backend/app/main.py")
    base = _commit(repo, "base")
    _write(repo, "backend/app/main.py", "y\n")
    _write(repo, "docs/TESTING.md")
    head = _commit(repo, "branch")
    assert changed_paths.changed_files(base, head, repo) == [
        "backend/app/main.py",
        "docs/TESTING.md",
    ]


def test_a_commit_on_the_base_branch_is_not_a_change_of_the_pull_request(repo: Path):
    _write(repo, "README.md")
    base = _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "frontend/src/App.tsx")
    head = _commit(repo, "feature")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "docs/TESTING.md")
    _commit(repo, "main moves on")
    assert changed_paths.changed_files(base, head, repo) == ["frontend/src/App.tsx"]


def test_a_rename_counts_as_both_its_old_and_its_new_path(repo: Path):
    # A file moved out of a filter's folder still runs that job once, for the
    # move itself, rather than dropping the job before the move lands.
    _write(repo, "scripts/check.sh", "#!/bin/sh\necho check\necho done\n")
    base = _commit(repo, "base")
    (repo / "scripts/e2e").mkdir()
    _git(repo, "mv", "scripts/check.sh", "scripts/e2e/check.sh")
    head = _commit(repo, "move")
    assert changed_paths.changed_files(base, head, repo) == [
        "scripts/check.sh",
        "scripts/e2e/check.sh",
    ]


# --- the command -------------------------------------------------------------------


def test_the_command_prints_one_line_per_filter(repo: Path, monkeypatch, capsys):
    # This is what the workflow appends to $GITHUB_OUTPUT.
    _write(repo, "backend/app/main.py")
    base = _commit(repo, "base")
    _write(repo, "frontend/src/App.tsx")
    head = _commit(repo, "frontend")
    monkeypatch.setenv(
        "FILTERS",
        "backend:\n  - 'backend/**'\nfrontend:\n  - 'frontend/**'\n",
    )
    assert (
        changed_paths.main(["--base", base, "--head", head, "--repo", str(repo)]) == 0
    )
    assert capsys.readouterr().out == "backend=false\nfrontend=true\n"


def test_the_filters_may_come_from_a_file(repo: Path, tmp_path, monkeypatch, capsys):
    _write(repo, "docs/TESTING.md")
    head = _commit(repo, "docs")
    monkeypatch.delenv("FILTERS", raising=False)
    filters_file = tmp_path / "filters.yml"
    filters_file.write_text("docs:\n  - 'docs/**'\n")
    argv = [
        "--base",
        head,
        "--head",
        head,
        "--repo",
        str(repo),
        "--filters-file",
        str(filters_file),
    ]
    assert changed_paths.main(argv) == 0
    assert capsys.readouterr().out == "docs=false\n"


def test_without_filters_nothing_is_printed(repo: Path, monkeypatch, capsys):
    # An unset variable is a missing filter, not an empty one: the step fails
    # and the callers run their jobs, rather than reading every filter false.
    monkeypatch.delenv("FILTERS", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        changed_paths.main(["--base", "a", "--head", "b"])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FILTERS" in captured.err


def test_unreadable_filters_exit_without_printing(repo: Path, monkeypatch, capsys):
    monkeypatch.setenv("FILTERS", "backend: 'backend/**'\n")
    code = changed_paths.main(["--base", "a", "--head", "b", "--repo", str(repo)])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read the filters" in captured.err


def test_a_failed_diff_exits_without_printing(repo: Path, monkeypatch, capsys):
    # A filter that could not run must not report every job as unaffected.
    _write(repo, "backend/app/main.py")
    head = _commit(repo, "base")
    monkeypatch.setenv("FILTERS", "backend:\n  - 'backend/**'\n")
    code = changed_paths.main(["--base", "0" * 40, "--head", head, "--repo", str(repo)])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "git diff" in captured.err
