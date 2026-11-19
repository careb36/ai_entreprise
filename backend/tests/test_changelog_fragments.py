"""Changelog fragments (issue #731): ``collect``, ``unreleased`` and ``release``.

A pull request adds ``changelog.d/<issue>-<slug>.md`` instead of a line in
``CHANGELOG.md``; ``scripts/release_notes.py`` folds the fragments into
``[Unreleased]`` at release. These tests run it on small changelogs and
fragment folders of their own, and show on a temporary git repository that
two branches each adding a fragment merge in either order without a conflict,
where two lines at the top of ``[Unreleased]`` do conflict.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes_fragments", SCRIPT)
assert _spec and _spec.loader
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

### Changed
* An earlier change (#20).

### Fixed
* An earlier fix (#21).

---

## [1.0.0] - 2026-11-01

### Added
* The first release (#10).

[1.0.0]: https://example.invalid/releases/tag/v1.0.0
"""


def _changelog(tmp_path: Path, text: str = CHANGELOG) -> tuple[Path, Path]:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    folder = tmp_path / "changelog.d"
    folder.mkdir()
    (folder / "README.md").write_text("# Changelog fragments\n\nNot one.\n")
    return path, folder


def _fragment(folder: Path, name: str, section: str, body: str) -> Path:
    path = folder / name
    path.write_text(f"{section}\n\n{body}\n", encoding="utf-8")
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --- folding -----------------------------------------------------------------------


def test_collect_folds_each_entry_into_its_section_and_deletes_the_fragments(
    tmp_path,
):
    path, folder = _changelog(tmp_path)
    _fragment(folder, "30-later-fix.md", "Fixed", "* A later fix (#30).")
    _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    _fragment(folder, "26-another-change.md", "Changed", "* Another change (#26).")

    names = release_notes.collect(path, folder)

    # Section order, then file name.
    assert names == ["25-a-change.md", "26-another-change.md", "30-later-fix.md"]
    assert sorted(p.name for p in folder.iterdir()) == ["README.md"]
    assert release_notes.notes(path.read_text(), "Unreleased") == (
        "### Changed\n\n"
        "* A change (#25).\n\n"
        "* Another change (#26).\n\n"
        "* An earlier change (#20).\n\n"
        "### Fixed\n\n"
        "* A later fix (#30).\n\n"
        "* An earlier fix (#21)."
    )
    # The released sections and the closing rule are untouched.
    after = path.read_text()
    assert after.endswith(CHANGELOG[CHANGELOG.index("---\n") :])


def test_a_missing_section_goes_where_keep_a_changelog_puts_it(tmp_path):
    path, folder = _changelog(tmp_path)
    _fragment(folder, "40-new.md", "Added", "* Something new (#40).")
    _fragment(folder, "41-gone.md", "Removed", "* Something gone (#41).")
    _fragment(folder, "42-safer.md", "Security", "* Something safer (#42).")

    release_notes.collect(path, folder)

    body = release_notes.notes(path.read_text(), "Unreleased")
    headings = [line for line in body.splitlines() if line.startswith("### ")]
    assert headings == [
        "### Added",
        "### Changed",
        "### Removed",
        "### Fixed",
        "### Security",
    ]
    assert "### Security\n\n* Something safer (#42)." in body
    assert "* Something safer (#42).\n\n---\n\n## [1.0.0]" in path.read_text()


def test_an_empty_unreleased_takes_the_fragments(tmp_path):
    text = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-11-01\n\n* One.\n"
    path, folder = _changelog(tmp_path, text)
    _fragment(folder, "50-fix.md", "Fixed", "* A fix (#50).")

    release_notes.collect(path, folder)

    assert path.read_text() == (
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n* A fix (#50).\n\n"
        "## [1.0.0] - 2026-11-01\n\n* One.\n"
    )


def test_collect_is_idempotent(tmp_path):
    path, folder = _changelog(tmp_path)
    _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    release_notes.collect(path, folder)
    once = path.read_text()

    assert release_notes.collect(path, folder) == []
    assert path.read_text() == once


def test_folding_twice_changes_nothing_so_an_interrupted_collect_finishes(
    tmp_path,
):
    # The changelog was written, then the run stopped before deleting.
    path, folder = _changelog(tmp_path)
    fragment = _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    fragments = release_notes.read_fragments(folder)
    folded = release_notes.fold(CHANGELOG, fragments)
    assert release_notes.fold(folded, fragments) == folded
    path.write_text(folded)

    assert release_notes.collect(path, folder) == ["25-a-change.md"]
    assert path.read_text() == folded
    assert not fragment.exists()


def test_a_multi_line_entry_is_kept_as_written(tmp_path):
    path, folder = _changelog(tmp_path)
    body = "* **A change (#27).** Its detail,\n  over two lines.\n  - and a sub-item"
    _fragment(folder, "27-long.md", "Changed", body)
    release_notes.collect(path, folder)
    assert f"### Changed\n\n{body}\n\n* An earlier change (#20)." in path.read_text()


# --- refusals ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("60-x.md", "Improved\n\n* Faster.\n", "'Improved', an unknown section"),
        ("60-x.md", "### Added\n\n* New.\n", "'### Added', an unknown section"),
        ("60-x.md", "Added\n\n   \n", "the fragment is empty"),
        ("60-x.md", "", "'', an unknown section"),
        ("60-x.md", "Added\n\n### Fixed\n* New.\n", "the entry has a heading line"),
        ("x-60.md", "Added\n\n* New.\n", "the name is not <issue>-<slug>.md"),
        ("60_Some_Thing.md", "Added\n\n* New.\n", "the name is not"),
        ("60-x.txt", "Added\n\n* New.\n", "the name is not"),
    ],
)
def test_a_bad_fragment_is_refused_by_name_and_nothing_is_written(
    tmp_path, name, text, reason
):
    path, folder = _changelog(tmp_path)
    good = _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    (folder / name).write_text(text)

    with pytest.raises(release_notes.ChangelogError) as caught:
        release_notes.collect(path, folder)

    assert f"changelog.d/{name}: " in str(caught.value)
    assert reason in str(caught.value)
    assert path.read_text() == CHANGELOG
    assert good.exists() and (folder / name).exists()


def test_every_bad_fragment_is_named_at_once(tmp_path):
    _, folder = _changelog(tmp_path)
    (folder / "1-a.md").write_text("Nope\n\n* x\n")
    (folder / "2-b.md").write_text("Added\n")
    problems = release_notes.fragment_problems(folder)
    assert len(problems) == 2
    assert problems[0].startswith("changelog.d/1-a.md: ")
    assert problems[1].startswith("changelog.d/2-b.md: ")


def test_no_folder_and_only_the_readme_are_no_fragments(tmp_path):
    assert release_notes.read_fragments(tmp_path / "changelog.d") == []
    _, folder = _changelog(tmp_path)
    (folder / ".gitkeep").write_text("")
    assert release_notes.read_fragments(folder) == []


# --- reading [Unreleased] ------------------------------------------------------------


def test_unreleased_previews_the_fold_without_writing(tmp_path):
    path, folder = _changelog(tmp_path)
    fragment = _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")

    result = _run("--changelog", str(path), "unreleased")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("### Changed\n\n* A change (#25).\n\n* An earlier")
    assert path.read_text() == CHANGELOG and fragment.exists()


def test_a_pre_release_reads_the_fragments_too(tmp_path):
    path, folder = _changelog(tmp_path)
    _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    result = _run("--changelog", str(path), "notes", "v1.1.0-rc.1")
    assert result.returncode == 0, result.stderr
    assert "* A change (#25)." in result.stdout
    # A released version's notes are its own section only.
    result = _run("--changelog", str(path), "notes", "v1.0.0")
    assert result.stdout == "### Added\n* The first release (#10).\n"


def test_unreleased_on_the_command_line_names_a_bad_fragment(tmp_path):
    path, folder = _changelog(tmp_path)
    (folder / "7-x.md").write_text("Misc\n\n* x\n")
    result = _run("--changelog", str(path), "unreleased")
    assert result.returncode == 1
    assert "changelog.d/7-x.md: the first line is 'Misc'" in result.stderr


# --- cutting a release ---------------------------------------------------------------


@pytest.mark.parametrize("command", ["release", "cut"])
def test_cut_produces_the_release_a_hand_fold_would(tmp_path, command):
    path, folder = _changelog(tmp_path)
    _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    _fragment(folder, "40-new.md", "Added", "* Something new (#40).")
    by_hand = release_notes.release(
        release_notes.fold(CHANGELOG, release_notes.read_fragments(folder)),
        "1.1.0",
        date="2026-11-20",
    )

    result = _run("--changelog", str(path), command, "v1.1.0", "--date", "2026-11-20")

    assert result.returncode == 0, result.stderr
    assert "2 fragment(s) collected" in result.stdout
    assert path.read_text() == by_hand
    section = release_notes.notes(by_hand, "1.1.0")
    assert section.startswith("### Added\n\n* Something new (#40).")
    assert "### Changed\n\n* A change (#25).\n\n* An earlier change (#20)." in section
    assert sorted(p.name for p in folder.iterdir()) == ["README.md"]


def test_cut_without_fragments_is_the_release_it_always_was(tmp_path):
    path, _ = _changelog(tmp_path)
    result = _run("--changelog", str(path), "cut", "1.1.0", "--date", "2026-11-20")
    assert result.returncode == 0, result.stderr
    assert path.read_text() == release_notes.release(
        CHANGELOG, "1.1.0", date="2026-11-20"
    )


def test_a_refused_cut_changes_no_file(tmp_path):
    path, folder = _changelog(tmp_path)
    fragment = _fragment(folder, "25-a-change.md", "Changed", "* A change (#25).")
    result = _run("--changelog", str(path), "cut", "1.0.0")
    assert result.returncode == 1
    assert "already has a [1.0.0] section" in result.stderr
    assert path.read_text() == CHANGELOG and fragment.exists()


def test_fragments_alone_make_a_release_of_an_empty_unreleased(tmp_path):
    text = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-11-01\n\n* One.\n"
    path, folder = _changelog(tmp_path, text)
    _fragment(folder, "50-fix.md", "Fixed", "* A fix (#50).")
    result = _run("--changelog", str(path), "cut", "1.0.1", "--date", "2026-11-20")
    assert result.returncode == 0, result.stderr
    assert (
        release_notes.notes(path.read_text(), "1.0.1") == "### Fixed\n\n* A fix (#50)."
    )


# --- two branches, no conflict ---------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def _branch(root: Path, name: str, write) -> None:
    _ok(root, "checkout", "-q", "-b", name, "main")
    write(root)
    _ok(root, "add", "-A")
    _ok(root, "commit", "-q", "-m", name)
    _ok(root, "checkout", "-q", "main")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("needs git")
    root = tmp_path / "repo"
    root.mkdir()
    _ok(root, "init", "-q")
    _changelog(root)
    _ok(root, "add", "-A")
    _ok(root, "commit", "-q", "-m", "base")
    return root


def _add_fragment(name: str, body: str):
    return lambda root: _fragment(root / "changelog.d", name, "Changed", body)


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_two_branches_adding_fragments_merge_in_either_order(git_repo, order):
    _branch(git_repo, "a", _add_fragment("101-first.md", "* First (#101)."))
    _branch(git_repo, "b", _add_fragment("102-second.md", "* Second (#102)."))

    for name in order:
        result = _git(git_repo, "merge", "--no-ff", "-q", "-m", f"merge {name}", name)
        assert result.returncode == 0, result.stdout + result.stderr

    folder = git_repo / "changelog.d"
    release_notes.collect(git_repo / "CHANGELOG.md", folder)
    body = release_notes.notes((git_repo / "CHANGELOG.md").read_text(), "Unreleased")
    # Whichever merged first, the collected order is the file-name order.
    assert body.startswith("### Changed\n\n* First (#101).\n\n* Second (#102).")


def test_two_branches_editing_the_changelog_top_do_conflict(git_repo):
    # The waiting the fragments remove: the old rule, shown on the same base.
    def top_line(entry: str):
        def write(root: Path) -> None:
            path = root / "CHANGELOG.md"
            text = path.read_text()
            path.write_text(text.replace("### Changed\n", f"### Changed\n{entry}\n", 1))

        return write

    _branch(git_repo, "a", top_line("* First (#101)."))
    _branch(git_repo, "b", top_line("* Second (#102)."))
    _ok(git_repo, "merge", "--no-ff", "-q", "-m", "merge a", "a")
    result = _git(git_repo, "merge", "--no-ff", "-q", "-m", "merge b", "b")
    assert result.returncode != 0
    assert "CONFLICT" in result.stdout + result.stderr
    _git(git_repo, "merge", "--abort")
