"""Versioned releases (issue #512): the changelog reader and the tag workflows.

``scripts/release_notes.py`` (a port of AixleHQ/flow's ``bin/changelog``)
gives a GitHub Release its notes from ``CHANGELOG.md``. These tests run it on
small changelogs of their own and on the repository's, and hold
``.github/workflows/release.yml`` and ``images.yml`` to what
``docs/OPERATIONS.md`` promises: they run on a ``v*`` tag only, refuse a tag
whose commit is not on ``main``, and publish the images ``docker-build.yml``
builds.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release_notes.py"
WORKFLOWS = REPO / ".github" / "workflows"
_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert _spec and _spec.loader
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

All notable changes are documented here.

## [Unreleased]

### Added
- Something not released yet (#12).

## [1.1.0] - 2026-11-10

### Fixed
- A fix (#11).

## [1.0.0] - 2026-11-01

### Added
- The first release (#10).

[Unreleased]: https://example.invalid/compare/v1.1.0...main
[1.1.0]: https://example.invalid/compare/v1.0.0...v1.1.0
[1.0.0]: https://example.invalid/releases/tag/v1.0.0
"""


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


# --- extracting a section ----------------------------------------------------


def test_extracts_a_version_section_without_its_heading_or_the_next_one():
    assert release_notes.notes(CHANGELOG, "1.1.0") == "### Fixed\n- A fix (#11)."


def test_the_last_section_leaves_the_link_references_out():
    assert (
        release_notes.notes(CHANGELOG, "1.0.0")
        == "### Added\n- The first release (#10)."
    )


def test_a_section_ends_at_any_level_two_heading_and_drops_a_closing_rule():
    text = "## [Unreleased]\n\n- One (#1).\n\n---\n\n## Versioning\n\nNo tag yet.\n"
    assert release_notes.notes(text, "Unreleased") == "- One (#1)."


def test_a_missing_version_fails_clearly():
    with pytest.raises(
        release_notes.ChangelogError, match=r"no ## \[2\.0\.0\] section"
    ):
        release_notes.notes(CHANGELOG, "2.0.0")


def test_an_empty_section_fails_clearly():
    text = "## [Unreleased]\n\n## [1.0.0] - 2026-11-01\n\n- One (#1).\n"
    with pytest.raises(release_notes.ChangelogError, match="is empty"):
        release_notes.notes(text, "Unreleased")


def test_the_command_line_names_the_missing_version_and_exits_one(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    result = _run("--changelog", str(path), "notes", "v9.9.9")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "CHANGELOG.md has no ## [9.9.9] section" in result.stderr


# --- which section a tag reads -------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "section"),
    [
        ("v1.1.0", "1.1.0"),
        ("1.1.0", "1.1.0"),
        ("v1.2.0-rc.1", "Unreleased"),
        ("v2.0.0-beta.2", "Unreleased"),
        ("Unreleased", "Unreleased"),
        ("unreleased", "Unreleased"),
        ("", "Unreleased"),
    ],
)
def test_section_for_a_tag(ref, section):
    assert release_notes.section_for(ref) == section


@pytest.mark.parametrize("ref", ["v1.2", "release-1.0.0", "v1.2.0.4", "vX.Y.Z"])
def test_section_for_refuses_what_is_no_release_tag(ref):
    with pytest.raises(release_notes.ChangelogError, match="is not a vX.Y.Z"):
        release_notes.section_for(ref)


def test_a_pre_release_reads_unreleased_on_the_command_line(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    result = _run("--changelog", str(path), "notes", "v1.2.0-rc.1")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "### Added\n- Something not released yet (#12).\n"


# --- cutting a release ---------------------------------------------------------


def test_release_moves_unreleased_into_a_dated_section():
    cut = release_notes.release(CHANGELOG, "1.2.0", date="2026-11-14")
    assert (
        "## [Unreleased]\n\n## [1.2.0] - 2026-11-14\n\n### Added\n- Something not released yet"
        in cut
    )
    assert (
        release_notes.notes(cut, "1.2.0")
        == "### Added\n- Something not released yet (#12)."
    )
    assert release_notes.released_versions(cut) == ["1.2.0", "1.1.0", "1.0.0"]
    with pytest.raises(release_notes.ChangelogError, match="is empty"):
        release_notes.notes(cut, "Unreleased")


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("1.1.0", "already has a"),
        ("1.2.0-rc.1", "pre-release gets no section"),
        ("1.2", "is not an X.Y.Z version"),
    ],
)
def test_release_refuses(version, message):
    with pytest.raises(release_notes.ChangelogError, match=message):
        release_notes.release(CHANGELOG, version, date="2026-11-14")


def test_release_refuses_an_empty_unreleased():
    cut = release_notes.release(CHANGELOG, "1.2.0", date="2026-11-14")
    with pytest.raises(release_notes.ChangelogError, match="is empty"):
        release_notes.release(cut, "1.3.0", date="2026-11-15")


def test_release_on_the_command_line_writes_the_file(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    result = _run("--changelog", str(path), "release", "v1.2.0", "--date", "2026-11-14")
    assert result.returncode == 0, result.stderr
    assert "## [1.2.0] - 2026-11-14" in path.read_text()


# --- the repository's own changelog -----------------------------------------------


def test_every_section_of_the_repository_changelog_reads_cleanly():
    text = (REPO / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n" in text
    sections = release_notes.released_versions(text)
    try:
        release_notes.notes(text, "Unreleased")
        sections.append("Unreleased")
    except release_notes.ChangelogError as exc:
        # Right after a release is cut, [Unreleased] is empty.
        assert "is empty" in str(exc)
    for section in sections:
        body = release_notes.notes(text, section)
        assert body.startswith("### "), section
        assert not any(line.startswith("## ") for line in body.splitlines()), section
        assert not body.endswith("---"), section


# --- the workflows -----------------------------------------------------------------


def _workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 reads the bare key `on` as the boolean true.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _steps_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


@pytest.mark.parametrize("name", ["release.yml", "images.yml"])
def test_the_workflow_runs_on_v_tags_only(name):
    data = _workflow(name)
    assert data["on"] == {"push": {"tags": ["v*"]}}


@pytest.mark.parametrize(
    ("name", "job"), [("release.yml", "release"), ("images.yml", "version")]
)
def test_the_workflow_refuses_a_tag_off_main(name, job):
    text = _steps_text(_workflow(name)["jobs"][job])
    assert "+refs/heads/main:refs/remotes/origin/main" in text
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in text
    assert "develop" not in text


# The actions a tag workflow may use beside checkout: the cosign installer
# and the artifact upload for the images' signatures and SBOMs (issue #522,
# part C; backend/tests/test_supply_chain.py).
_TAG_ACTIONS = (
    "actions/checkout@",
    "actions/upload-artifact@",
    "sigstore/cosign-installer@",
)


@pytest.mark.parametrize("name", ["release.yml", "images.yml"])
def test_the_workflow_runs_on_the_self_hosted_runner_and_pins_checkout(name):
    data = _workflow(name)
    for job in data["jobs"].values():
        assert job["runs-on"] == ["self-hosted", "linux", "x64", "lafabrique"]
        uses = [step["uses"] for step in job["steps"] if "uses" in step]
        assert uses and uses[0].startswith("actions/checkout@")
        assert all(
            u.startswith(_TAG_ACTIONS) and len(u.split("@")[1]) == 40 for u in uses
        )


def test_release_writes_the_notes_with_the_script_and_creates_the_release():
    data = _workflow("release.yml")
    assert data["permissions"] == {"contents": "write"}
    job = data["jobs"]["release"]
    text = _steps_text(job)
    assert 'scripts/release_notes.py notes "$GITHUB_REF_NAME"' in text
    assert 'gh release create "$TAG"' in text
    assert "--prerelease" in text
    create = next(step for step in job["steps"] if step.get("name") == "Create Release")
    assert create["env"]["GH_TOKEN"] == "${{ github.token }}"


def _matrix(name: str, job: str) -> list[str]:
    return [
        entry["name"]
        for entry in _workflow(name)["jobs"][job]["strategy"]["matrix"]["include"]
    ]


def test_images_publish_what_docker_build_builds_and_release_lists_them():
    published = _matrix("images.yml", "publish")
    assert published == _matrix("docker-build.yml", "build")
    release_text = _steps_text(_workflow("release.yml")["jobs"]["release"])
    for name in published:
        # Inside the shell's double quotes, each backtick is escaped.
        assert f"\\`{name}\\`" in release_text


def test_images_move_latest_only_for_a_stable_release():
    data = _workflow("images.yml")
    assert data["concurrency"]["cancel-in-progress"] is False
    publish = data["jobs"]["publish"]
    assert publish["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
    }
    text = _steps_text(publish)
    assert "ghcr.io/${GITHUB_REPOSITORY,,}/${NAME}" in text
    assert 'if [ "$STABLE" = "true" ]; then docker push "${image}:latest"; fi' in text
    version_text = _steps_text(data["jobs"]["version"])
    assert 'if [[ "$version" != *-* ]]; then stable=true; fi' in version_text
