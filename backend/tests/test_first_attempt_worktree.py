"""A task's first attempt runs its tools in the task's own worktree (Git workflow).

Found 2026-11-24: on a company that delivers on branches, the daemon made the
task's worktree only at its first delivery, so a first attempt ran its tools
in the person's checkout, where nothing may be written, and was never offered
edit, move or delete. Now the worker asks the daemon for the worktree before
the first turn (``workspace.console.prepare`` with ``private_git: false``,
``services/tools/task_worktree.py``) when every daemon announces it, and the
delivery commits in that same worktree. Real git in ``tmp_path`` and the real
daemon modules; design in ``docs/design/first-attempt-in-task-worktree.md``.
"""

from __future__ import annotations

from pathlib import Path

import agent_tools_helpers
import console_producer_helpers
import console_run  # the daemon's modules, on sys.path via conftest
import git_workspace
import pytest
from console_producer_helpers import git
from developer_tools_helpers import _answer, _Model
from services.loop.worker import execute as worker_execute
from services.tools import developer_tools, mcp_tools, task_worktree, workspace_edits
from services.workspace import daemon_liveness, git_workflow

repo = console_producer_helpers.repo
daemon = console_producer_helpers.daemon
SPEC = {"branch": "fabrique/task-1", "base": "", "forge_policy": "none"}
ENGINE = object()  # Never opened: the spec and the sandbox are stubbed.
MOVE = {("workspace.edit", "move"), ("workspace.edit", "delete")}
MOVE |= {("workspace.generate", "delete")}


class _Requester(agent_tools_helpers._Requester):
    """The daemon's functions, plus its ``workspace.console.prepare``."""

    def __init__(self):
        super().__init__(daemon)
        self.payloads = []

    def __call__(self, subject, payload, timeout):
        if subject == "workspace.console.prepare":
            self.subjects.append(subject)
            self.payloads.append(payload)
            return console_run.prepare(payload["workspace_root"], payload)
        return super().__call__(subject, payload, timeout)


def _checkout_state(root: Path) -> tuple:
    """What the person sees: their branch, their HEAD, their files and status."""
    return (
        git(str(root), "rev-parse", "--abbrev-ref", "HEAD"),
        git(str(root), "rev-parse", "HEAD"),
        git(str(root), "status", "--porcelain", "--untracked-files=all"),
        (root / "calc.py").read_text(),
    )


@pytest.fixture
def wired(repo, monkeypatch):
    """The worker's bridge on *repo*, a Git-workflow company, the daemons heard."""
    requester = _Requester()
    announced = set(MOVE) | {(task_worktree.SUBJECT, task_worktree.FEATURE)}
    monkeypatch.setattr(
        daemon_liveness, "announces", lambda s, f=None: (s, f) in announced
    )
    monkeypatch.setattr(git_workflow, "task_spec", lambda e, c, i: dict(SPEC))
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(
        developer_tools.sandbox, "request_for_company_id", lambda c, e: None
    )
    monkeypatch.setattr(
        developer_tools.research_tools, "toolbox_for", lambda *a, **k: None
    )
    monkeypatch.setattr(developer_tools, "_detect_tests", lambda *a, **k: (None, {}))
    developer_tools.bind(object(), object(), lambda company_id: str(repo))
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    yield requester, announced
    task_worktree.leave()
    developer_tools.unbind()


def test_the_daemon_prepares_the_tools_worktree_with_git_s_own_pointer(repo):
    before = _checkout_state(repo)
    prepared = console_run.prepare(str(repo), {"git": SPEC, "private_git": False})
    assert prepared["ok"], prepared
    assert prepared["private_git"] is False
    worktree = Path(prepared["worktree"])
    assert worktree.parent.name == "fabrique-worktrees"
    assert (worktree / ".git").is_file()  # git's pointer, no private repository
    assert git(str(worktree), "rev-parse", "--abbrev-ref", "HEAD") == SPEC["branch"]
    assert _checkout_state(repo) == before

    # A worktree a run left dirty is put back at the branch's head before reuse.
    (worktree / "calc.py").write_text("broken\n")
    (worktree / "stray.py").write_text("x = 1\n")
    again = console_run.prepare(str(repo), {"git": SPEC, "private_git": False})
    assert again["worktree"] == str(worktree) and again["head"] == prepared["head"]
    assert (worktree / "calc.py").read_text() == (repo / "calc.py").read_text()
    assert not (worktree / "stray.py").exists()
    assert _checkout_state(repo) == before


def test_a_first_attempt_offers_edit_and_move_in_the_task_s_worktree(wired, repo):
    requester, _ = wired
    before = _checkout_state(repo)
    prepared = developer_tools.enter_task_worktree(1, 1, engine=ENGINE)
    assert prepared["ok"] is True and prepared["private_git"] is False
    assert requester.payloads[0]["private_git"] is False
    worktree = prepared["worktree"]
    assert developer_tools.tool_root(1, 1, engine=ENGINE) == worktree

    box = developer_tools.toolbox_for(1, "Developer", engine=ENGINE, item_id=1)
    names = [s["function"]["name"] for s in box.schemas()]
    assert {"workspace__edit", "workspace__move", "workspace__delete"} <= set(names)
    assert box.workspace.workspace_root == worktree
    assert workspace_edits.offered(box)["withheld"] == {}

    observation, _ = box.call(
        agent_tools_helpers._call(
            "workspace__edit",
            path="calc.py",
            start_line=2,
            end_line=2,
            new_text="    return a + b\n",
        ),
        1,
    )
    assert observation["is_error"] is False, observation
    box.close()
    assert _checkout_state(repo) == before  # the person's checkout never written

    # The delivery names the same branch: it commits in the same worktree.
    files = workspace_edits.with_tool_edits([], box.workspace.edited_files)
    assert [f["path"] for f in files] == ["calc.py"]
    reply = git_workspace.deliver(
        str(repo), {**SPEC, "message": "Task 1\n"}, files, daemon.write_files
    )
    assert reply["ok"], reply
    assert reply["workspace_root"] == worktree == reply["git"]["worktree"]
    assert reply["git"]["parent"] == prepared["head"]
    assert git(str(repo), "rev-parse", SPEC["branch"]) == reply["git"]["commit"]
    shown = git(str(repo), "show", f"{SPEC['branch']}:calc.py")
    assert "return a + b" in shown
    assert _checkout_state(repo) == before

    task_worktree.leave()
    assert task_worktree.prepared_for(1, 1) is None


def test_an_older_daemon_keeps_today_s_behaviour(wired, repo):
    requester, announced = wired
    announced.discard((task_worktree.SUBJECT, task_worktree.FEATURE))
    assert developer_tools.enter_task_worktree(1, 1, engine=ENGINE) is None
    assert "workspace.console.prepare" not in requester.subjects
    assert developer_tools.tool_root(1, 1) == str(repo)
    box = developer_tools.toolbox_for(1, "Developer", item_id=1)
    assert box.workspace.workspace_root == str(repo)
    assert "workspace__edit" not in [s["function"]["name"] for s in box.schemas()]
    assert not (repo / ".git" / "fabrique-worktrees").exists()


def test_a_reply_that_is_not_the_task_s_worktree_is_never_used(repo, monkeypatch):
    monkeypatch.setattr(daemon_liveness, "announces", lambda s, f=None: True)
    monkeypatch.setattr(git_workflow, "task_spec", lambda e, c, i: dict(SPEC))

    def enter(reply):
        answer = lambda subject, payload, timeout: reply
        return task_worktree.enter(answer, lambda c: str(repo), 1, 1, ENGINE)

    base = {"ok": True, "private_git": False, "branch": SPEC["branch"]}
    # The person's checkout itself, another task's worktree, an older daemon.
    for reply in (
        {**base, "worktree": str(repo)},
        {**base, "worktree": f"{repo}/.git/fabrique-worktrees/fabrique__task-2"},
        {**base, "private_git": True, "worktree": f"{repo}/x"},
        {"ok": False, "error": "not a repository"},
    ):
        assert enter(reply)["ok"] is False
        assert task_worktree.prepared_for(1, 1) is None
    good = f"{repo}\\.git\\fabrique-worktrees\\fabrique__task-1"  # Windows
    assert enter({**base, "worktree": good})["ok"] is True
    assert task_worktree.prepared_for(1, 1) == good
    assert task_worktree.prepared_for(1, 2) is None
    task_worktree.leave()


def test_the_attempt_records_where_its_tools_ran(monkeypatch):
    box = developer_tools.WorkspaceToolbox(_Requester(), "/w", can_edit=True)
    monkeypatch.setattr(developer_tools, "toolbox_for", lambda *a, **k: box)
    reply = {"ok": True, "worktree": "/w", "branch": SPEC["branch"]}
    monkeypatch.setattr(
        developer_tools, "enter_task_worktree", lambda *a, **k: dict(reply)
    )
    model = _Model([_answer("Done.")])
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    worker_execute._execute_task(
        {"id": 1, "title": "T", "description": "x"}, agent, "S"
    )
    assert agent["tools_offered"]["task_worktree"] == reply
    evidence = workspace_edits.observe_tools({}, agent)
    assert evidence["tools_offered"]["task_worktree"]["worktree"] == "/w"
