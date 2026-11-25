"""An earlier self-hosting snapshot is refreshed from the source, never answered
stale (``self_host_checkout.py``, PROTOCOL.md §5.9, issue #701).

The source moved on: its files replace the snapshot's ``main`` in one commit
that records the source commit, with deletions carried and the task branches
of earlier sprints kept. Work the snapshot holds and the source does not, a
checkout off ``main`` and a dirty working tree are refused (409), never
overwritten.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

import self_host_checkout as shc


def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture()
def machine(tmp_path, monkeypatch):
    source = tmp_path / "la-fabrique"
    (source / "backend").mkdir(parents=True)
    (source / "backend" / "app.py").write_text("print('v1')\n")
    (source / "old.txt").write_text("gone in v2\n")
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "v1")
    root = tmp_path / "snapshots"
    monkeypatch.setenv(shc.SOURCE_ENV, str(source))
    monkeypatch.setenv(shc.ROOT_ENV, str(root))
    return source, root / "lf"


def _advance(source):
    (source / "backend" / "app.py").write_text("print('v2')\n")
    (source / "old.txt").unlink()
    (source / "added.txt").write_text("new in v2\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "v2")
    return _git(source, "rev-parse", "HEAD")


def test_a_new_snapshot_records_the_source_commit(machine):
    source, target = machine
    reply = shc.checkout("lf")
    trailer = _git(target, "log", "-1", f"--format=%(trailers:key={shc.TRAILER},valueonly)")
    assert trailer == reply["source_head"] == _git(source, "rev-parse", "HEAD")
    assert (reply["created"], reply["refreshed"], reply["previous_head"]) == (True, False, None)


def test_the_source_unchanged_answers_the_snapshot_as_it_is(machine):
    _, target = machine
    first = shc.checkout("lf")
    again = shc.checkout("lf")
    assert (again["created"], again["refreshed"], again["files"]) == (False, False, None)
    assert again["head"] == again["previous_head"] == first["head"]
    assert _git(target, "rev-list", "--count", "HEAD") == "1"


def test_the_source_moved_on_refreshes_main_and_keeps_task_branches(machine):
    source, target = machine
    first = shc.checkout("lf")
    _git(target, "branch", "fabrique/task-1")
    v2 = _advance(source)
    reply = shc.checkout("lf")
    assert (reply["created"], reply["refreshed"]) == (False, True)
    assert reply["previous_head"] == first["head"] != reply["head"]
    assert reply["source_head"] == v2 and reply["files"] == 2
    assert (target / "backend" / "app.py").read_text() == "print('v2')\n"
    assert (target / "added.txt").is_file() and not (target / "old.txt").exists()
    assert _git(target, "log", "-1", f"--format=%(trailers:key={shc.TRAILER},valueonly)") == v2
    assert _git(target, "rev-parse", "HEAD~1") == first["head"]  # history kept, one commit on top
    assert _git(target, "rev-parse", "fabrique/task-1") == first["head"]
    assert _git(target, "status", "--porcelain") == ""
    # Asked once more: nothing new to copy.
    assert shc.checkout("lf")["refreshed"] is False


def test_a_snapshot_without_the_trailer_is_compared_by_its_files(machine):
    source, target = machine
    shc.checkout("lf")
    # As snapshots made before the trailer: the same author, no Source-Commit.
    _git(target, "-c", "user.name=s", "-c", "user.email=self-host@localhost",
         "commit", "-q", "--amend", "-m", shc.MESSAGE, "--reset-author")
    before = _git(target, "rev-parse", "HEAD")
    reply = shc.checkout("lf")
    assert (reply["refreshed"], reply["head"]) == (False, before)  # same files: no commit
    _advance(source)
    assert shc.checkout("lf")["refreshed"] is True


def test_work_on_main_the_source_does_not_have_is_refused(machine):
    source, target = machine
    shc.checkout("lf")
    (target / "agent.txt").write_text("merged by a sprint\n")
    _git(target, "add", ".")
    _git(target, "commit", "-q", "-m", "sprint work")
    head = _git(target, "rev-parse", "HEAD")
    _advance(source)
    with pytest.raises(shc.Conflict, match="commits the source does not"):
        shc.checkout("lf")
    assert _git(target, "rev-parse", "HEAD") == head
    assert (target / "agent.txt").is_file()


@pytest.mark.parametrize("dirt", ["changed", "untracked"])
def test_a_dirty_working_tree_is_refused(machine, dirt):
    source, target = machine
    shc.checkout("lf")
    if dirt == "changed":
        (target / "backend" / "app.py").write_text("print('mine')\n")
    else:
        (target / "notes.txt").write_text("mine\n")
    _advance(source)
    with pytest.raises(shc.Conflict, match="changes or untracked"):
        shc.checkout("lf")
    assert "mine" in ((target / "backend" / "app.py").read_text() + (
        (target / "notes.txt").read_text() if dirt == "untracked" else ""))


def test_a_checkout_off_main_is_refused(machine):
    source, target = machine
    shc.checkout("lf")
    _git(target, "checkout", "-q", "-b", "elsewhere")
    _advance(source)
    with pytest.raises(shc.Conflict, match="not on main"):
        shc.checkout("lf")


def test_the_handler_answers_a_conflict_as_a_409(machine):
    _, target = machine
    shc.checkout("lf")
    (target / "notes.txt").write_text("mine\n")
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    asyncio.run(shc.handle_checkout(None, object(), reply, lambda msg: {"name": "lf"}))
    assert replies[0]["error"]["status"] == 409
