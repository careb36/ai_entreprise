"""The self-hosting snapshot made on the daemon's computer (``self_host_checkout.py``,
PROTOCOL.md §5.9, issue #701), on a real repository in a temporary folder.

The request names a folder, never a path: a name with a separator or ``..``,
a symlinked folder that resolves outside the root, the source itself or a
folder inside it, and a non-empty folder that is not a snapshot are refused.
The copy takes what git tracks or would track, never an ignored file, and
never follows a symlink.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

import abilities
import daemon
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
    (source / "backend" / "app.py").write_text("print('hi')\n")
    (source / ".gitignore").write_text(".env\n")
    (source / ".env").write_text("SECRET=1\n")
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "init")
    (source / "new.txt").write_text("not committed yet\n")  # would be tracked
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    (source / "link.txt").symlink_to(outside)
    root = tmp_path / "snapshots"
    monkeypatch.setenv(shc.SOURCE_ENV, str(source))
    monkeypatch.setenv(shc.ROOT_ENV, str(root))
    return source, root, tmp_path


def test_the_snapshot_copies_what_git_tracks_or_would_track(machine):
    source, root, _ = machine
    reply = shc.checkout("lf-701")
    target = root / "lf-701"
    assert reply["ok"] and reply["created"] and reply["path"] == str(target)
    assert reply["source_head"] == _git(source, "rev-parse", "HEAD")
    assert reply["head"] == _git(target, "rev-parse", "HEAD")
    assert (target / "backend" / "app.py").read_text() == "print('hi')\n"
    assert (target / "new.txt").is_file()
    assert not (target / ".env").exists()  # ignored: never copied
    assert not (target / "link.txt").exists()  # a link is never followed
    assert _git(target, "branch", "--show-current") == "main"
    assert _git(target, "status", "--porcelain") == ""
    # Asked again: the earlier snapshot, as it is.
    again = shc.checkout("lf-701")
    assert (again["created"], again["head"], again["files"]) == (False, reply["head"], None)


@pytest.mark.parametrize("name", ["../escape", "a/b", "..", ".", "", "/abs", "Upper", "a\\b", None, 7])
def test_a_name_that_is_not_one_folder_is_refused(machine, name):
    _, root, tmp_path = machine
    with pytest.raises(shc.Refused):
        shc.checkout(name)
    assert not (tmp_path / "escape").exists()


def test_a_symlinked_folder_out_of_the_root_is_refused(machine):
    _, root, tmp_path = machine
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "lf").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(shc.Refused, match="outside"):
        shc.checkout("lf")
    assert list(elsewhere.iterdir()) == []


def test_the_source_and_a_root_inside_it_are_refused(machine, monkeypatch):
    source, _, _ = machine
    monkeypatch.setenv(shc.ROOT_ENV, str(source))
    with pytest.raises(shc.Refused, match="inside the source"):
        shc.checkout("backend")
    monkeypatch.setenv(shc.ROOT_ENV, str(source.parent))
    with pytest.raises(shc.Refused, match="outside the source"):
        shc.checkout("la-fabrique")


def test_a_folder_with_other_files_is_never_overwritten(machine):
    _, root, _ = machine
    (root / "taken").mkdir(parents=True)
    (root / "taken" / "notes.txt").write_text("mine\n")
    with pytest.raises(shc.Refused, match="not an earlier snapshot"):
        shc.checkout("taken")
    assert (root / "taken" / "notes.txt").read_text() == "mine\n"


def test_the_handler_answers_a_refusal_as_a_400(machine):
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    asyncio.run(shc.handle_checkout(None, object(), reply, lambda msg: {"name": "../x"}))
    assert replies[0]["error"]["status"] == 400


def test_the_subject_is_served_and_announced():
    served = {subject for subject, _ in daemon.subscriptions(None)}
    assert shc.SUBJECT in served and shc.SUBJECT in abilities.SUBJECTS
