"""
Bringing the local copy up to date with its remote (issue #745).

The workspace daemon fast-forwards the person's local base branch to its
remote's tip and nothing else (``vscode-daemon/git_sync.py``): ``update-ref``
with the old value when the base is not checked out, ``merge --ff-only`` in
the checkout that has it. Diverged, dirty, mid-rebase and unreachable
repositories are refused with their reason and left byte-identical (refs and
index compared); under the forge policy ``none`` no remote command runs. The
New company route (``POST /v1/init_company``) records the Owner's policy
(``forge_policy_changed``, ``at: creation``) and the daemon's observation
(``base_sync_observed``) and creates the company whatever the outcome; the
settings action is a Manager's. Real git in ``tmp_path``: a bare remote, a
clone behind it, and an unreachable URL. Nothing touches a real repository.
"""

import asyncio
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from config import settings
from models import AuditLog, Company, ProjectDiscovery
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_base
import git_local
import git_sync
import git_workspace
import local_merge
import test_git_workflow
from test_git_workflow import _company, _member, git

enforcement = test_git_workflow.enforcement
REMOTE_VERBS = {"fetch", "push", "pull", "ls-remote", "clone", "remote-https"}


def _commit(root, name, text):
    (root / name).write_text(text, encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", f"{name}: {text.strip()}")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def behind(tmp_path):
    """``(clone, upstream, remote)``: a clone whose ``main`` is 5 commits behind ``origin``."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", str(remote), str(upstream))
    git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _commit(upstream, "core.py", "VERSION = 0\n")
    git(upstream, "push", "-q", "origin", "main")
    clone = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    for version in range(1, 6):
        _commit(upstream, "core.py", f"VERSION = {version}\n")
    git(upstream, "push", "-q", "origin", "main")
    return clone, upstream, remote


@pytest.fixture
def recorded(monkeypatch):
    """Every git command the daemon's modules run."""
    commands: list[list[str]] = []
    real = git_workspace.run_git

    def recording(args, cwd, check=True, timeout=git_workspace.GIT_TIMEOUT_SECONDS):
        commands.append(list(args))
        return real(args, cwd, check=check, timeout=timeout)

    for module in (git_workspace, git_base, git_local, git_sync, local_merge):
        monkeypatch.setattr(module, "run_git", recording)
    return commands


def _snapshot(root: Path):
    """The refs, HEAD and the index's bytes: what a refusal must leave as it was."""
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
    head = (root / ".git" / "HEAD").read_text()
    index = root / ".git" / "index"
    digest = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
    return refs, head, digest


# --- the daemon ------------------------------------------------------------------


def test_a_base_five_behind_is_fast_forwarded_in_the_checkout_that_has_it(behind):
    clone, upstream, _ = behind
    before = git(clone, "rev-parse", "main")
    tip = git(upstream, "rev-parse", "main")

    answer = git_sync.sync(str(clone), "main", "fetch")

    assert answer["ok"] is True and answer["outcome"] == "updated"
    assert (answer["before"], answer["after"]) == (before, tip)
    assert (answer["behind"], answer["ahead"]) == (5, 0)
    assert answer["remote"] == "origin/main" and answer["remote_contacted"] is True
    assert git(clone, "rev-parse", "main") == tip
    # The person's files followed, as ``git pull`` would; no merge commit.
    assert (clone / "core.py").read_text() == "VERSION = 5\n"
    assert git(clone, "rev-list", "--merges", "--count", "main") == "0"
    assert git(clone, "rev-parse", "origin/main") == tip


def test_a_base_not_checked_out_moves_by_update_ref_and_the_checkout_stays(behind):
    clone, upstream, _ = behind
    git(clone, "switch", "-q", "-c", "feature")
    (clone / "wip.py").write_text("x = 1\n")  # untracked work stays

    answer = git_sync.sync(str(clone), "main", "fetch")

    assert answer["outcome"] == "updated"
    assert git(clone, "rev-parse", "main") == git(upstream, "rev-parse", "main")
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "feature"
    assert (clone / "core.py").read_text() == "VERSION = 0\n"
    assert (clone / "wip.py").exists()


def test_up_to_date_and_ahead_move_nothing(behind):
    clone, _, _ = behind
    assert git_sync.sync(str(clone), "", "fetch")["outcome"] == "updated"
    assert git_sync.sync(str(clone), "", "fetch")["outcome"] == "up_to_date"
    _commit(clone, "mine.py", "mine = 1\n")
    ahead = git_sync.sync(str(clone), "main", "publish")
    assert (ahead["ok"], ahead["outcome"], ahead["ahead"]) == (True, "ahead", 1)
    assert ahead["after"] == ahead["before"] == git(clone, "rev-parse", "main")


def _diverge(clone):
    _commit(clone, "mine.py", "mine = 1\n")


def _dirty(clone):
    (clone / "core.py").write_text("VERSION = 'edited'\n")


def _rebasing(clone):
    _commit(clone, "core.py", "VERSION = 'mine'\n")
    git(clone, "switch", "-q", "-c", "other", "HEAD~1")
    _commit(clone, "core.py", "VERSION = 'other'\n")
    result = subprocess.run(
        ["git", "rebase", "main"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0  # stopped on the conflict, rebase in progress


def _unreachable(clone):
    git(clone, "remote", "set-url", "origin", str(clone.parent / "gone.git"))


@pytest.mark.parametrize(
    "setup,outcome,contacted",
    [
        (_diverge, "diverged", True),
        (_dirty, "dirty", False),
        (_rebasing, "operation_in_progress", False),
        (_unreachable, "remote_unreachable", True),
    ],
)
def test_each_refusal_says_why_and_leaves_the_repository_byte_identical(
    behind, setup, outcome, contacted
):
    clone, _, _ = behind
    setup(clone)
    before = _snapshot(clone)
    files = (clone / "core.py").read_text()

    answer = git_sync.sync(str(clone), "main", "fetch")

    assert answer["ok"] is False and answer["outcome"] == outcome, answer
    assert answer["reason"]
    assert answer["remote_contacted"] is contacted
    assert answer["after"] == answer["before"]
    assert _snapshot(clone) == before
    assert (clone / "core.py").read_text() == files
    if outcome == "diverged":
        assert (answer["behind"], answer["ahead"]) == (5, 1)
        assert "1 commit(s) only here, 5 only there" in answer["reason"]


def test_under_none_nothing_contacts_the_remote(behind, recorded):
    clone, _, _ = behind
    before = _snapshot(clone)

    answer = git_sync.sync(str(clone), "main", "none")

    assert (answer["ok"], answer["outcome"]) == (False, "forge_disabled")
    assert answer["remote_contacted"] is False
    assert recorded and not [c for c in recorded if REMOTE_VERBS & set(c)]
    assert _snapshot(clone) == before


def test_the_daemon_ceiling_caps_the_request(behind, monkeypatch):
    clone, _, _ = behind
    monkeypatch.setenv("FORGE_POLICY_MAX", "none")
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    asyncio.run(
        git_sync.handle_sync(
            None,
            None,
            reply,
            lambda _m: {
                "workspace_root": str(clone),
                "base": "main",
                "forge_policy": "publish",
            },
        )
    )
    assert replies[0]["outcome"] == "forge_disabled"
    assert replies[0]["forge_policy"] == "none"


def test_the_status_reads_the_checkout_and_the_remote_url_without_credentials(
    behind,
):
    clone, _, _ = behind
    # A password made up now, so that no credential-shaped text sits in the file.
    password = secrets.token_hex(6)
    git(
        clone,
        "remote",
        "set-url",
        "origin",
        f"https://dev:{password}@example.invalid/app.git",
    )
    _dirty(clone)

    status = git_base.status(str(clone), "main", "none")

    assert status["remote_url"] == "https://example.invalid/app.git"
    assert status["current_branch"] == "main"
    assert status["clean"] is False and status["operation_in_progress"] is None
    assert status["remote_contacted"] is False


# --- the routes ----------------------------------------------------------------


class _SyncingNATS:
    """The daemon's inspection, and its real base status and sync."""

    def __init__(self):
        self.subjects = []

    async def request(self, subject, payload, timeout=5.0):
        self.subjects.append((subject, payload))
        if subject == "workspace.inspect":
            return {"is_dir": True, "path": payload["path"], "git": {"is_repo": True}}
        if subject == "workspace.git.sync_base":
            return git_sync.sync(
                payload["workspace_root"], payload["base"], payload["forge_policy"]
            )
        if subject == "workspace.git.base_status":
            return git_base.status(
                payload["workspace_root"], payload["base"], payload["forge_policy"]
            )
        raise TimeoutError(subject)

    async def close(self):
        pass


def _rows(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _create(test_client, clone, **body):
    response = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Sync Corp",
            "project_path": str(clone),
            "start_now": False,
            **body,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_company_created_with_fetch_and_the_box_ticked_starts_at_the_remote_tip(
    test_client, sqlite_session, behind
):
    clone, upstream, _ = behind
    before, tip = git(clone, "rev-parse", "main"), git(upstream, "rev-parse", "main")
    test_client.app.state.nats_client = nats = _SyncingNATS()

    created = _create(test_client, clone, forge_policy="fetch", sync_base=True)

    assert git(clone, "rev-parse", "main") == tip
    assert created["base_sync"]["outcome"] == "updated"
    # The sync ran before the folder was inspected: the creation reads the new files.
    subjects = [s for s, _ in nats.subjects]
    assert subjects.index("workspace.git.sync_base") < subjects.index(
        "workspace.inspect"
    )
    company = sqlite_session.get(Company, created["company_id"])
    assert company.forge_policy == "fetch"
    [policy] = _rows(sqlite_session, company.id, "forge_policy_changed")
    assert policy.result == {"from": "none", "to": "fetch"}
    assert policy.details["at"] == "creation"
    [record] = _rows(sqlite_session, company.id, "base_sync_observed")
    assert (record.result["before"], record.result["after"]) == (before, tip)
    assert record.result["remote"] == "origin/main"
    assert record.details["observed_by"] == "workspace_daemon"


def test_a_refused_sync_still_creates_the_company_and_records_why(
    test_client, sqlite_session, behind
):
    clone, _, _ = behind
    _dirty(clone)
    before = _snapshot(clone)
    test_client.app.state.nats_client = _SyncingNATS()

    created = _create(test_client, clone, forge_policy="fetch", sync_base=True)

    assert created["company_id"] and created["base_sync"]["outcome"] == "dirty"
    assert _snapshot(clone) == before
    [record] = _rows(sqlite_session, created["company_id"], "base_sync_observed")
    assert record.result["ok"] is False and record.result["outcome"] == "dirty"


def test_choosing_none_is_audited_and_sends_no_sync(
    test_client, sqlite_session, behind, recorded
):
    clone, _, _ = behind
    test_client.app.state.nats_client = nats = _SyncingNATS()

    created = _create(test_client, clone, forge_policy="none", sync_base=True)

    assert "workspace.git.sync_base" not in [s for s, _ in nats.subjects]
    assert created["base_sync"]["outcome"] == "forge_disabled"
    assert not [c for c in recorded if REMOTE_VERBS & set(c)]
    [policy] = _rows(sqlite_session, created["company_id"], "forge_policy_changed")
    assert policy.result == {"from": "none", "to": "none"}
    assert policy.details["at"] == "creation"


def test_a_policy_above_the_ceiling_is_refused_before_anything_is_created(
    test_client, sqlite_session, behind, monkeypatch
):
    clone, _, _ = behind
    monkeypatch.setattr(settings, "FORGE_POLICY_MAX", "none")
    test_client.app.state.nats_client = nats = _SyncingNATS()
    count = len(sqlite_session.exec(select(Company)).all())

    refused = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Too wide",
            "project_path": str(clone),
            "forge_policy": "fetch",
        },
    )

    assert refused.status_code == 409 and "FORGE_POLICY_MAX" in refused.text
    assert len(sqlite_session.exec(select(Company)).all()) == count
    assert nats.subjects == []


def test_the_check_reads_the_remote_only_when_asked_capped_by_the_ceiling(
    test_client, behind, monkeypatch, recorded
):
    clone, _, _ = behind
    test_client.app.state.nats_client = nats = _SyncingNATS()
    url = "/v1/workspace/git_check"

    local = test_client.post(url, json={"project_path": str(clone)}).json()
    assert local["state"] == "observed" and local["remote_contacted"] is False
    assert local["clean"] is True and local["current_branch"] == "main"
    assert not [c for c in recorded if REMOTE_VERBS & set(c)]

    checked = test_client.post(
        url, json={"project_path": str(clone), "contact_remote": True}
    ).json()
    assert checked["remote_contacted"] is True
    assert (
        checked["base_behind_remote"] == 5 and checked["forge_policy_max"] == "publish"
    )

    monkeypatch.setattr(settings, "FORGE_POLICY_MAX", "none")
    capped = test_client.post(
        url, json={"project_path": str(clone), "contact_remote": True}
    ).json()
    assert capped["remote_contacted"] is False and capped["forge_policy_max"] == "none"
    assert [p["forge_policy"] for _, p in nats.subjects] == ["none", "fetch", "none"]


@pytest.mark.usefixtures("enforcement")
def test_the_settings_action_is_a_managers_and_held_to_the_policy(
    test_client, sqlite_session, behind
):
    clone, upstream, _ = behind
    company = _company(sqlite_session)
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(clone)))
    sqlite_session.commit()
    url = f"/v1/companies/{company.id}/git/sync_base"
    test_client.app.state.nats_client = nats = _SyncingNATS()
    viewer = _member(sqlite_session, company.id, "Viewer")
    manager = _member(sqlite_session, company.id, "Manager")

    assert test_client.post(url, headers=viewer).status_code == 403
    company.forge_policy = "none"
    sqlite_session.add(company)
    sqlite_session.commit()
    held = test_client.post(url, headers=manager).json()
    assert held["outcome"] == "forge_disabled" and nats.subjects == []

    company.forge_policy = "fetch"
    sqlite_session.add(company)
    sqlite_session.commit()
    done = test_client.post(url, headers=manager).json()
    assert done["outcome"] == "updated"
    assert git(clone, "rev-parse", "main") == git(upstream, "rev-parse", "main")
    rows = _rows(sqlite_session, company.id, "base_sync_observed")
    assert [r.result["outcome"] for r in rows] == ["forge_disabled", "updated"]
    assert all(r.details["at"] == "settings" for r in rows)
