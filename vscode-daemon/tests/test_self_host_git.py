"""Landing and mirroring the self-hosting source (``self_host_git.py``,
PROTOCOL.md §5.10), on real repositories in a temporary folder.

Nothing here runs land.sh or pushes, not even in the setup: the land's process is a recorded fake,
and a push is refused before git is asked to push, or recorded instead. The
"origin" is a bare repository in the temporary folder.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

import abilities
import daemon
import self_host_checkout as shc
import self_host_git as shg


def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(root, name, text, message):
    (root / name).write_text(text)
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    source = tmp_path / "la-fabrique"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    (source / "docs" / "architecture").mkdir(parents=True)
    (source / "docs" / "architecture" / "contexts.json").write_text(
        '{"tests": {"files": {"test_verifier.py": ["evidence"]}}}\n')
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "init")
    _git(tmp_path, "clone", "-q", "--bare", str(source), str(origin))
    _git(source, "remote", "add", "origin", str(origin))
    _git(source, "checkout", "-q", "-b", "feat/x")
    head = _commit(source, "a.txt", "a\n", "Add a (#42)")
    _git(source, "checkout", "-q", "main")
    monkeypatch.setenv(shc.SOURCE_ENV, str(source))
    return source, origin, head


def test_branches_and_the_land_preview(repo):
    source, _, head = repo
    reply = shg.view({"view": "branches"})
    assert [b["name"] for b in reply["branches"]] == ["feat/x"]
    preview = shg.view({"view": "land_preview", "branch": "feat/x"})
    assert preview["head"] == head and preview["already_in_main"] is False
    assert [c["subject"] for c in preview["commits"]] == ["Add a (#42)"]
    assert preview["diffstat"] == [{"path": "a.txt", "added": 1, "deleted": 0}]
    assert preview["changed"] == ["a.txt"]
    assert preview["test_map"] == {"test_verifier.py": ["evidence"]}


@pytest.mark.parametrize("name", ["main", "../x", "-x", "nope", "a b", "x..y", None, 3, "a/", "x.lock"])
def test_a_branch_must_be_one_the_daemon_lists(repo, name):
    with pytest.raises(shc.Refused):
        shg.view({"view": "land_preview", "branch": name})


def test_the_land_runs_land_sh_as_an_argv_and_land_owner_only_when_asked(repo, monkeypatch):
    source, _, head = repo
    monkeypatch.setenv("LAND_OWNER", "1")  # this daemon's own: never passed on
    monkeypatch.setenv("LAND_CHECK_CMD", "true")
    calls = []

    def runner(argv, root, env, timeout):
        calls.append((argv, env))
        return 3, "land: refused\n"

    reply = shg.land({"branch": "feat/x", "expected_head": head}, runner=runner)
    (argv, env), = calls
    assert argv == ["bash", "scripts/land.sh", "feat/x"]  # never --push
    assert "LAND_OWNER" not in env and "LAND_CHECK_CMD" not in env
    assert env["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert (reply["landed"], reply["outcome"], reply["land_owner"]) == (False, "refused", False)
    shg.land({"branch": "feat/x", "expected_head": head, "land_owner": True}, runner=runner)
    assert calls[-1][1]["LAND_OWNER"] == "1"
    shg.land({"branch": "feat/x", "expected_head": head, "land_owner": "yes"}, runner=runner)
    assert "LAND_OWNER" not in calls[-1][1]


def test_a_land_of_a_branch_that_moved_since_the_preview_is_refused(repo):
    source, _, head = repo
    with pytest.raises(shg.Conflict, match="preview again"):
        shg.land({"branch": "feat/x", "expected_head": "0" * 40}, runner=lambda *a: (0, ""))


def test_a_landed_branch_reports_main_and_the_record(repo):
    source, _, head = repo

    def runner(argv, root, env, timeout):  # what land.sh does, in short
        _git(root, "merge", "-q", "--no-ff", "-m", "Land feat/x into main", "feat/x")
        merge = _git(root, "rev-parse", "HEAD")
        records = root / ".git" / "fabrique" / "verifications"
        records.mkdir(parents=True)
        (records / f"{merge}.json").write_text('{"nature": "claim", "status": "passed", "steps": []}')
        return 0, "land: main is now ...\n"

    main_before = _git(source, "rev-parse", "main")
    reply = shg.land({"branch": "feat/x", "expected_head": head}, runner=runner)
    assert reply["landed"] and reply["main_before"] == main_before
    assert reply["main_after"] == _git(source, "rev-parse", "main") != main_before
    assert reply["record"] == {"nature": "claim", "status": "passed", "steps": []}


def test_push_preview_lists_what_becomes_public(repo):
    source, _, _ = repo
    _git(source, "merge", "-q", "--ff-only", "feat/x")
    preview = shg.view({"view": "push_preview"})
    assert preview["fast_forward"] and not preview["up_to_date"]
    assert [c["subject"] for c in preview["commits"]] == ["Add a (#42)"]
    assert preview["has_record"] is False


def test_a_push_that_is_not_a_fast_forward_is_refused_before_any_push(repo, tmp_path, monkeypatch):
    source, origin, _ = repo
    # Someone else moved origin's main: local main no longer contains it.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _commit(other, "b.txt", "b\n", "elsewhere")
    _git(origin, "fetch", "-q", str(other), "main:main")  # a fetch into the bare origin, no push
    _git(source, "merge", "-q", "--ff-only", "feat/x")
    preview = shg.view({"view": "push_preview"})
    assert preview["fast_forward"] is False and preview["commits"] == []
    pushed = []
    real = shg.run_git
    monkeypatch.setattr(shg, "run_git", lambda args, *a, **k: pushed.append(args) if args[0] == "push" else real(args, *a, **k))
    with pytest.raises(shg.Conflict, match="not a fast-forward"):
        shg.push({"main": preview["main"], "origin_main": preview["origin_main"]})
    assert pushed == []
    assert _git(origin, "rev-parse", "main") == preview["origin_main"]


def test_a_push_is_a_plain_fast_forward_of_the_previewed_commit(repo, monkeypatch):
    source, origin, _ = repo
    _git(source, "merge", "-q", "--ff-only", "feat/x")
    _git(source, "notes", "--ref=verification", "add", "-m", "{}", "HEAD")
    preview = shg.view({"view": "push_preview"})
    assert preview["has_record"] and preview["notes"]
    pushed = []
    real = shg.run_git
    monkeypatch.setattr(shg, "run_git", lambda args, *a, **k: pushed.append(args) if args[0] == "push" else real(args, *a, **k))
    reply = shg.push({"main": preview["main"], "origin_main": preview["origin_main"]})
    (argv,) = pushed
    assert argv == ["push", "--atomic", "--quiet", "origin", f"{preview['main']}:refs/heads/main",
                    "refs/notes/verification:refs/notes/verification"]
    assert "--force" not in argv and not any(a.startswith("+") for a in argv)
    assert reply["before"] == preview["origin_main"] and reply["pushed"] is False  # recorded, not sent
    with pytest.raises(shg.Conflict, match="moved"):
        shg.push({"main": "1" * 40, "origin_main": preview["origin_main"]})


def test_fix_commit_names_the_commits_and_whether_origin_has_them(repo):
    source, _, head = repo
    _git(source, "merge", "-q", "--ff-only", "feat/x")
    reply = shg.view({"view": "fix_commit", "issue": 42})
    assert [(c["sha"], c["on_origin"]) for c in reply["commits"]] == [(head, False)]
    assert shg.view({"view": "fix_commit", "issue": 4})["commits"] == []
    with pytest.raises(shc.Refused):
        shg.view({"view": "fix_commit", "issue": "42; rm"})


def test_the_handlers_answer_refusals_and_are_served_and_announced():
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    handler = dict(shg.HANDLERS)[shg.GIT_SUBJECT]
    asyncio.run(handler(None, object(), reply, lambda msg: {"view": "rm"}))
    assert replies[0]["error"]["status"] in (400, 500)
    served = {subject for subject, _ in daemon.subscriptions(None)}
    for subject in (shg.GIT_SUBJECT, shg.LAND_SUBJECT, shg.PUSH_SUBJECT):
        assert subject in served and subject in abilities.SUBJECTS
