"""Target measure of the finding "the lint after an edit reads the root's profile".

The self-hosting retrospective's F5 follow-up left one item open: the stack
profile resolves per folder for a delivery's suites, but the lint after an
edit (``services/tools/edit_lint.py``) still reads the root's profile, so in
a repository with no project at its root (La Fabrique: ``backend/``,
``frontend/``, ``llm-proxy/``) an edit of ``backend/app/x.py`` is told "the
project names no lint command" and is never linted. The specification is
``scripts/self_host/specs/folder-lint-specification.json``; each test names
the criterion it measures.

A grader, written by a person (docs/design/rsi-loop.md, §1.3 and REQ-12):
it fails on the baseline (main on 2026-11-16) and should pass once the
sprint's change is in. It reads only the backend's public seams named in the
specification and asks no daemon, database or model. Run it from the backend
folder of the build under test, this file taken from the baseline:

    cd <build>/backend && PYTHONPATH=.:app python -m pytest -q -p no:cacheprovider \\
        <baseline>/scripts/self_host/measures/test_folder_lint.py

or through the harness: ``self_host.py --steps measure --spec <spec file>``.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from services.tools import edit_lint
from services.workspace import project_profile, stack_roots

# La Fabrique's shape: no project at the root, a lint command per folder.
PROFILE = {
    "test": "",
    "lint": "",
    "folders": {
        "backend": {"stack": "python", "lint": "python -m ruff check ."},
        "frontend": {"stack": "node", "lint": "npx eslint ."},
    },
}


class _Box:
    """The workspace toolbox as ``edit_lint.after_edit`` reads it; records
    what the daemon was asked to run."""

    def __init__(self, profile: dict):
        self.workspace_root = "/work/la-fabrique"
        self.sandbox = None
        self.sandbox_error = None
        self.lint_profile = lambda: profile
        self.asked: list[dict] = []

    def requester(self, subject: str, request: dict, timeout: float) -> dict:
        self.asked.append({"subject": subject, **request})
        return {"exit_code": 0, "stdout": "All checks passed!\n", "stderr": ""}


def _resolved_profile() -> dict:
    """The root's profile with the resolved folders, as the fix's loader
    returns it (REQ-2.3 measures the loader itself)."""
    return {
        "stack": None,
        "lint": None,
        "folders": stack_roots.resolve_folders(None, {}, PROFILE),
    }


def _edit(profile: dict, path: str) -> tuple[_Box, str, dict]:
    box = _Box(profile)
    extra: dict = {}
    text = edit_lint.after_edit(box, path, {"checked": "python syntax"}, extra)
    return box, text, extra


# --- REQ-1: a folder's profile carries a lint command ---------------------------


def test_req_1_1_a_company_names_a_folders_lint_command():
    checked = stack_roots.validate_overrides(PROFILE)
    assert checked["folders"]["backend"]["lint"] == "python -m ruff check ."
    resolved = {f["folder"]: f for f in stack_roots.resolve_folders(None, {}, PROFILE)}
    assert resolved["backend"]["lint"] == "python -m ruff check ."
    assert resolved["backend"]["source"]["lint"] == "company"
    assert resolved["frontend"]["lint"] == "npx eslint ."


def test_req_1_2_a_detected_folder_keeps_its_stacks_lint_and_an_empty_one_turns_it_off():
    detected = {
        "backend": {
            "stack": "python",
            "install": "poetry install",
            "lint": "poetry run python -m ruff check .",
        },
        "frontend": {
            "stack": "node",
            "install": "npm ci",
            "lint": "npm run lint --if-present",
        },
    }
    folders = stack_roots.resolve_folders(
        None, detected, {"folders": {"frontend": {"lint": ""}}}
    )
    resolved = {f["folder"]: f for f in folders}
    assert resolved["backend"]["lint"] == "poetry run python -m ruff check ."
    assert resolved["backend"]["source"]["lint"] == "detected"
    assert resolved["frontend"]["lint"] is None


# --- REQ-2: the lint after an edit runs the edited file's folder's linter ------


def test_req_2_1_an_edit_in_a_folder_runs_that_folders_lint_there():
    box, text, extra = _edit(_resolved_profile(), "backend/app/services/x.py")
    assert [a["command"] for a in box.asked] == [
        "cd backend && python -m ruff check app/services/x.py"
    ]
    assert box.asked[0]["subject"] == "workspace.execute"
    assert extra["lint"]["folder"] == "backend"
    assert extra["lint"]["narrowed"] is True and extra["lint"]["exit_code"] == 0
    assert "no problems" in text


def test_req_2_2_a_file_in_no_folder_with_no_root_lint_is_told_so():
    box, text, extra = _edit(_resolved_profile(), "docs/notes.py")
    assert box.asked == []
    assert extra["lint"] == {"ran": False, "reason": "no lint command"}
    assert "names no lint command" in text


def test_req_2_2_the_folders_stack_decides_which_files_its_linter_reads():
    box, _text, extra = _edit(_resolved_profile(), "backend/app/x.ts")
    assert box.asked == []
    assert extra["lint"]["ran"] is False and "python linter" in extra["lint"]["reason"]
    box, _text, extra = _edit(_resolved_profile(), "frontend/src/a.ts")
    assert [a["command"] for a in box.asked] == ["cd frontend && npx eslint src/a.ts"]


def test_req_2_3_the_lints_profile_carries_the_companys_folders(monkeypatch):
    async def detect(_nats, _root):
        return None, {}

    monkeypatch.setattr(project_profile, "detect_project", detect)
    monkeypatch.setattr(
        project_profile,
        "read_company",
        lambda sprint, engine: {"id": 1, "overrides": PROFILE, "installs": {}},
    )
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        profile = edit_lint.load_profile(
            {"nats": None, "loop": loop}, 1, "/work/la-fabrique", None
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    folders = {f["folder"]: f.get("lint") for f in profile.get("folders") or []}
    assert folders == {"backend": "python -m ruff check .", "frontend": "npx eslint ."}
    box = _Box(profile)
    edit_lint.after_edit(box, "backend/app/x.py", {}, {})
    assert [a["command"] for a in box.asked] == [
        "cd backend && python -m ruff check app/x.py"
    ]


def test_the_measure_reads_the_seams_it_names():
    # A rename of a seam makes the measure fail loudly, never pass vacuously.
    for name in ("after_edit", "load_profile"):
        assert callable(getattr(edit_lint, name, None)), name
    assert callable(getattr(stack_roots, "resolve_folders", None))
    with pytest.raises((TypeError, ValueError)):
        stack_roots.validate_overrides({"folders": {"backend": {"lint": 3}}})
