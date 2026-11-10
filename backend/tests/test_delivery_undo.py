"""
Undoing a task's delivery from a snapshot kept outside the project.

Taken from opencode's snapshots: before a delivery without the Git workflow,
the daemon keeps the files it is about to change in a bare Git store of its
own under ``FABRIQUE_SNAPSHOT_STATE`` (``vscode-daemon/delivery_snapshot.py``),
never in the project's ``.git``; undo restores only those paths, and only
when each still holds what the delivery wrote. These tests write real files
in ``tmp_path``, in a folder that is not a repository and in one that is.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from models.agent_run import AgentRun, Verification
from models.audit import AuditLog
from services.workspace import git_workflow
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import delivery_snapshot
import project_lock
import workspace_edit
from test_git_workflow import SPEC, _company, _member, daemon, enforcement, git  # noqa: F401
from workspace_read import ReadError


@pytest.fixture(params=["plain", "repository"])
def project(request, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('old')\n", encoding="utf-8")
    (root / "keep.txt").write_text("untouched\n", encoding="utf-8")
    if request.param == "repository":
        git(root, "init", "-q", "-b", "main")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "first")
    return root


FILES = [
    {"path": "app.py", "content": "print('new')\n"},
    {"path": "pkg/sub/new.py", "content": "x = 1\n"},
]


def _git_state(root):
    """Every object and ref the project's own repository holds, or ``None`` without one."""
    if not (root / ".git").exists():
        return None
    objects = git(root, "count-objects", "-v")
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
    return objects, refs, git(root, "status", "--porcelain")


def _undo(root, snapshot_id):
    return delivery_snapshot.undo(str(root), snapshot_id, daemon._resolve_target_path)


# --- the daemon --------------------------------------------------------------


def test_a_delivery_keeps_a_snapshot_outside_the_project(project, tmp_path):
    before = _git_state(project)

    reply = daemon.deliver_files(str(project), FILES)

    assert reply["ok"] is True and "snapshot_error" not in reply
    snapshot = reply["snapshot"]
    by_path = {f["path"]: f for f in snapshot["files"]}
    assert by_path["app.py"]["existed"] is True
    assert by_path["app.py"]["before_sha256"] is not None
    assert by_path["pkg/sub/new.py"] == {
        "path": "pkg/sub/new.py",
        "existed": False,
        "before_sha256": None,
        "after_sha256": next(
            r["sha256"] for r in reply["files"] if r["path"] == "pkg/sub/new.py"
        ),
    }
    store = delivery_snapshot.store_for(project.resolve())
    assert store.parent == tmp_path / "snapshot-state"
    assert not store.is_relative_to(project)
    ref = f"refs/fabrique/snapshots/{snapshot['id']}"
    manifest = json.loads(
        subprocess.run(
            [
                "git",
                "--git-dir",
                str(store),
                "cat-file",
                "blob",
                f"{ref}:manifest.json",
            ],
            capture_output=True,
            check=True,
        ).stdout
    )
    assert manifest["project_root"] == str(project.resolve())
    # The project's own repository gained nothing: no object, no ref.
    after = _git_state(project)
    if before is not None:
        assert after[:2] == before[:2]


def test_undo_restores_modified_files_and_removes_created_ones(project):
    reply = daemon.deliver_files(str(project), FILES)

    undone = _undo(project, reply["snapshot"]["id"])

    assert (project / "app.py").read_text() == "print('old')\n"
    assert not (project / "pkg").exists()  # the folders it created, once empty
    assert (project / "keep.txt").read_text() == "untouched\n"
    assert [r["path"] for r in undone["restored"]] == ["app.py"]
    assert undone["removed"] == [{"path": "pkg/sub/new.py"}]
    with pytest.raises(ReadError) as second:
        _undo(project, reply["snapshot"]["id"])
    assert second.value.status == 409 and "No snapshot" in second.value.message


def test_a_file_changed_after_the_delivery_is_a_conflict_and_nothing_changes(project):
    reply = daemon.deliver_files(str(project), FILES)
    (project / "pkg" / "sub" / "new.py").write_text("x = 2\n", encoding="utf-8")

    with pytest.raises(ReadError) as conflict:
        _undo(project, reply["snapshot"]["id"])

    assert conflict.value.status == 409
    assert "pkg/sub/new.py (changed since the delivery)" in conflict.value.message
    assert "app.py" not in conflict.value.message.split(":", 1)[1]
    assert (project / "app.py").read_text() == "print('new')\n"
    assert (project / "pkg" / "sub" / "new.py").read_text() == "x = 2\n"
    (project / "pkg" / "sub" / "new.py").unlink()
    with pytest.raises(ReadError) as deleted:
        _undo(project, reply["snapshot"]["id"])
    assert "deleted since the delivery" in deleted.value.message


def test_the_bytes_of_a_file_that_may_hold_secrets_are_never_kept(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text("TOKEN=real\n", encoding="utf-8")

    reply = daemon.deliver_files(str(root), [{"path": ".env", "content": "TOKEN=x\n"}])

    assert reply["ok"] is True  # the delivery is never blocked
    assert "snapshot" not in reply
    assert "may hold secrets" in reply["snapshot_error"]


def test_a_delivery_on_a_git_branch_has_no_snapshot(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "first")

    reply = daemon.deliver_files(str(root), [{"path": "a.py", "content": "a\n"}], SPEC)

    assert reply["git"]["commit"] and "snapshot" not in reply


def test_the_daemon_answers_workspace_undo_in_the_reply_shape(project):
    reply = daemon.deliver_files(str(project), FILES)
    sent = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def _reply(nc, msg, body):
        sent.append(body)

    for body in (
        {"workspace_root": str(project), "snapshot_id": reply["snapshot"]["id"]},
        {"workspace_root": str(project), "snapshot_id": reply["snapshot"]["id"]},
        {"workspace_root": str(project), "snapshot_id": "../../etc"},
    ):
        asyncio.run(
            delivery_snapshot.handle_undo(
                None,
                _Msg(body),
                _reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )
        )

    assert sent[0]["ok"] is True
    assert sent[1]["error"]["status"] == 409
    assert sent[2]["error"]["status"] == 400


def test_a_file_written_with_the_same_bytes_is_not_in_the_snapshot(project):
    reply = daemon.deliver_files(
        str(project),
        [{"path": "app.py", "content": "print('old')\n"}, *FILES[1:]],
    )
    assert [f["path"] for f in reply["snapshot"]["files"]] == ["pkg/sub/new.py"]
    # A person's later edit of the file it did not change blocks nothing.
    (project / "app.py").write_text("print('Ana')\n", encoding="utf-8")

    undone = _undo(project, reply["snapshot"]["id"])

    assert undone["removed"] == [{"path": "pkg/sub/new.py"}]
    assert (project / "app.py").read_text() == "print('Ana')\n"


def test_aliases_of_one_file_are_kept_once_with_the_last_write(project):
    reply = daemon.deliver_files(
        str(project),
        [
            {"path": "app.py", "content": "print('first')\n"},
            {"path": "./app.py", "content": "print('last')\n"},
        ],
    )
    [entry] = reply["snapshot"]["files"]
    assert entry["path"] == "app.py"
    assert entry["after_sha256"] == reply["files"][-1]["sha256"]

    _undo(project, reply["snapshot"]["id"])

    assert (project / "app.py").read_text() == "print('old')\n"


def _held(project):
    """The project's lock, held by another thread until the returned event is set."""
    taken, release = threading.Event(), threading.Event()

    def hold():
        with project_lock.lock_for(str(project)):
            taken.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    taken.wait(5)
    return release, holder


@pytest.mark.parametrize("step", ["undo", "deliver", "edit"])
def test_delivery_edit_and_undo_wait_for_the_projects_lock(project, step):
    reply = daemon.deliver_files(str(project), FILES)
    alias = str(project / "pkg" / "..")  # another spelling of the same project
    calls = {
        "undo": lambda: _undo(alias, reply["snapshot"]["id"]),
        "deliver": lambda: daemon.deliver_files(
            alias, [{"path": "app.py", "content": "b\n"}]
        ),
        "edit": lambda: workspace_edit.edit_file(
            alias,
            {
                "session": "ab" * 8,
                "path": "keep.txt",
                "search": "untouched",
                "replace": "edited",
            },
            daemon._resolve_target_path,
        ),
    }
    release, holder = _held(project)
    worker = threading.Thread(target=calls[step])
    worker.start()
    worker.join(0.3)
    try:
        assert worker.is_alive()  # waiting: nothing read or written yet
        assert (project / "app.py").read_text() == "print('new')\n"
        assert (project / "keep.txt").read_text() == "untouched\n"
    finally:
        release.set()
        holder.join(5)
        worker.join(10)
    assert not worker.is_alive()


def test_a_write_that_fails_midway_puts_the_delivery_back_and_the_snapshot_stays(
    project, monkeypatch
):
    reply = daemon.deliver_files(
        str(project),
        [*FILES, {"path": "keep.txt", "content": "changed\n"}],
    )
    real_write = workspace_edit._write_atomically
    calls = []

    def flaky(target, data):
        calls.append(target.name)
        if len(calls) == 2:
            raise OSError("disk full")
        real_write(target, data)

    monkeypatch.setattr(workspace_edit, "_write_atomically", flaky)

    with pytest.raises(ReadError) as failed:
        _undo(project, reply["snapshot"]["id"])

    assert failed.value.status == 500 and "can still undo it" in failed.value.message
    assert calls == ["app.py", "keep.txt", "app.py"]  # restored, failed, rolled back
    assert (project / "app.py").read_text() == "print('new')\n"
    assert (project / "keep.txt").read_text() == "changed\n"
    assert (project / "pkg" / "sub" / "new.py").exists()
    monkeypatch.setattr(workspace_edit, "_write_atomically", real_write)
    undone = _undo(project, reply["snapshot"]["id"])
    assert (project / "app.py").read_text() == "print('old')\n"
    assert (project / "keep.txt").read_text() == "untouched\n"
    assert undone["removed"] == [{"path": "pkg/sub/new.py"}]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only folder",
)
def test_a_folder_that_cannot_be_written_is_found_before_anything_changes(project):
    (project / "lib").mkdir()
    (project / "lib" / "util.py").write_text("u = 0\n", encoding="utf-8")
    reply = daemon.deliver_files(
        str(project),
        [FILES[0], {"path": "lib/util.py", "content": "u = 1\n"}],
    )
    (project / "lib").chmod(0o555)
    try:
        with pytest.raises(ReadError) as refused:
            _undo(project, reply["snapshot"]["id"])
        assert refused.value.status == 409
        assert "lib/util.py (its folder cannot be written)" in refused.value.message
        assert (project / "app.py").read_text() == "print('new')\n"
    finally:
        (project / "lib").chmod(0o755)
    _undo(project, reply["snapshot"]["id"])
    assert (project / "app.py").read_text() == "print('old')\n"
    assert (project / "lib" / "util.py").read_text() == "u = 0\n"


# --- the backend -------------------------------------------------------------


class _Daemon:
    """``request`` runs the real daemon's undo; the app's shutdown calls the rest."""

    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        try:
            return delivery_snapshot.undo(
                payload["workspace_root"],
                payload["snapshot_id"],
                daemon._resolve_target_path,
            )
        except ReadError as exc:
            return {"error": {"status": exc.status, "message": exc.message}}

    async def close(self):
        return None

    async def drain(self):
        return None


def _delivered_run(session, company, root, *, spec=None, ref="task-7"):
    reply = daemon.deliver_files(str(root), FILES, spec)
    run = AgentRun(
        company_id=company.id,
        external_ref=ref,
        producer="le-fabrique-task-worker",
        agent_role="Developer",
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    evidence = {"workspace_root": str(root), "workspace_writes": reply["files"]}
    git_workflow.record_reply(evidence, reply)
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence=evidence,
        )
    )
    session.commit()
    return run, reply


def test_the_snapshot_is_kept_on_the_evidence_as_the_daemon_observed_it(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    reply = daemon.deliver_files(str(root), FILES)
    evidence = {}

    git_workflow.record_reply(evidence, reply)

    assert evidence["snapshot"] == reply["snapshot"]
    assert "git" not in evidence


@pytest.mark.usefixtures("enforcement")
def test_a_manager_undoes_a_delivery_and_a_viewer_cannot(
    sqlite_session, test_client, project, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session, on=False)
    run, reply = _delivered_run(sqlite_session, company, project)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, _company(sqlite_session).id, "Manager")
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    url = f"/v1/agent_runs/{run.id}/undo?company_id={company.id}"

    changes = test_client.get(
        f"/v1/agent_runs/{run.id}/changes",
        params={"company_id": company.id},
        headers=viewer,
    ).json()
    assert changes["snapshot"]["id"] == reply["snapshot"]["id"]
    assert changes["snapshot"]["undone_at"] is None
    assert test_client.post(url, headers=viewer).status_code == 403
    assert test_client.post(url, headers=outsider).status_code == 403
    assert (project / "app.py").read_text() == "print('new')\n"

    undone = test_client.post(url, headers=manager)

    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert [r["path"] for r in body["restored"]] == ["app.py"]
    assert body["removed"] == [{"path": "pkg/sub/new.py"}]
    assert (project / "app.py").read_text() == "print('old')\n"
    assert nats.requests == [
        (
            "workspace.undo",
            {"workspace_root": str(project), "snapshot_id": reply["snapshot"]["id"]},
        )
    ]
    audit = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.action == "run_delivery_undone", AuditLog.company_id == company.id
        )
    ).one()
    assert audit.id == body["audit_log_id"] and audit.company_id == company.id
    sqlite_session.expire_all()
    changes = test_client.get(
        f"/v1/agent_runs/{run.id}/changes",
        params={"company_id": company.id},
        headers=viewer,
    ).json()
    assert changes["snapshot"]["undone_at"] is not None
    again = test_client.post(url, headers=manager)
    assert again.status_code == 409 and "No snapshot" in again.json()["detail"]
    other = _company(sqlite_session)
    other_manager = _member(sqlite_session, other.id, "Manager")
    assert (
        test_client.post(
            f"/v1/agent_runs/{run.id}/undo?company_id={other.id}", headers=other_manager
        ).status_code
        == 404
    )


def test_a_conflict_comes_back_as_409_and_nothing_changes(
    sqlite_session, test_client, project, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session, on=False)
    run, _ = _delivered_run(sqlite_session, company, project)
    (project / "app.py").write_text("print('edited by Ana')\n", encoding="utf-8")
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())

    refused = test_client.post(f"/v1/agent_runs/{run.id}/undo?company_id={company.id}")

    assert refused.status_code == 409
    assert "app.py (changed since the delivery)" in refused.json()["detail"]
    assert (project / "pkg" / "sub" / "new.py").exists()
    assert (
        sqlite_session.exec(
            select(AuditLog).where(AuditLog.company_id == company.id)
        ).all()
        == []
    )


def test_a_branch_delivery_or_no_daemon_is_refused(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# App\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "first")
    plain = tmp_path / "plain"
    plain.mkdir()
    company = _company(sqlite_session)
    branched, _ = _delivered_run(sqlite_session, company, repo, spec=SPEC)
    undoable, _ = _delivered_run(sqlite_session, company, plain, ref="task-8")
    bare = AgentRun(
        company_id=company.id,
        external_ref="task-9",
        producer="le-fabrique-task-worker",
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    sqlite_session.add(bare)
    sqlite_session.commit()

    class _NoDaemon(_Daemon):
        async def request(self, subject, payload, timeout=5.0):
            raise TimeoutError("nobody answered")

    monkeypatch.setattr(test_client.app.state, "nats_client", _NoDaemon())

    def post(run):
        return test_client.post(f"/v1/agent_runs/{run.id}/undo?company_id={company.id}")

    refused = post(branched)
    assert refused.status_code == 409
    assert "discard the branch" in refused.json()["detail"]
    assert (
        test_client.get(
            f"/v1/agent_runs/{branched.id}/changes", params={"company_id": company.id}
        ).json()["snapshot"]
        is None
    )
    assert post(bare).status_code == 409
    assert post(undoable).status_code == 503
    assert (plain / "app.py").exists()
