"""The daemon writes only inside the project, never into its VCS metadata (#357).

Every check goes through the real ``write_files`` against ``tmp_path``: an
absolute path, ``..``, a symlinked file or folder that leads out of the
project, and the version-control metadata (a hook under ``.git/hooks`` would
run on the person's next commit), reached directly or through a symlink.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


@pytest.fixture(scope="module")
def daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_iso", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def layout(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    (project / "src").mkdir(parents=True)
    (project / ".git" / "hooks").mkdir(parents=True)
    outside.mkdir()
    return project, outside


def _write(daemon, project: Path, path: str) -> dict:
    [report] = daemon.write_files(str(project), [{"path": path, "content": "x = 1\n"}])
    return report


@pytest.mark.parametrize(
    "path",
    ["../outside/escape.py", "src/../../outside/escape.py", "/etc/escape.py"],
)
def test_a_path_out_of_the_project_is_refused(daemon, layout, path):
    project, outside = layout
    assert _write(daemon, project, path)["status"] == "rejected"
    assert not (outside / "escape.py").exists()


def test_a_symlinked_file_that_leads_out_is_not_followed(daemon, layout):
    project, outside = layout
    target = outside / "victim.py"
    target.write_text("original\n")
    (project / "src" / "link.py").symlink_to(target)
    assert _write(daemon, project, "src/link.py")["status"] == "rejected"
    assert target.read_text() == "original\n"


def test_a_symlinked_folder_that_leads_out_is_not_followed(daemon, layout):
    project, outside = layout
    (project / "vendor").symlink_to(outside, target_is_directory=True)
    assert _write(daemon, project, "vendor/new.py")["status"] == "rejected"
    assert not (outside / "new.py").exists()


@pytest.mark.parametrize(
    "path",
    [
        ".git/hooks/pre-commit",
        ".git/config",
        "sub/.git/hooks/post-checkout",
        ".hg/hgrc",
        ".svn/entries",
    ],
)
def test_version_control_metadata_is_never_written(daemon, layout, path):
    project, _ = layout
    report = _write(daemon, project, path)
    assert report["status"] == "rejected", report
    assert not (project / path).exists()
    assert not (project / ".git" / "hooks" / "pre-commit").exists()


def test_a_symlink_into_the_metadata_is_refused(daemon, layout):
    project, _ = layout
    (project / "hooks").symlink_to(project / ".git" / "hooks", target_is_directory=True)
    assert _write(daemon, project, "hooks/pre-commit")["status"] == "rejected"
    assert not (project / ".git" / "hooks" / "pre-commit").exists()


def test_a_symlink_inside_the_project_still_works(daemon, layout):
    project, _ = layout
    real = project / "src" / "real.py"
    real.write_text("old\n")
    (project / "src" / "alias.py").symlink_to(real)
    assert _write(daemon, project, "src/alias.py")["status"] == "written"
    assert real.read_text() == "x = 1\n"


def test_a_name_that_only_looks_like_metadata_is_written(daemon, layout):
    project, _ = layout
    for path in ("src/.github/workflow.py", "docs/git/notes.py", ".gitignore"):
        assert _write(daemon, project, path)["status"] == "written", path
