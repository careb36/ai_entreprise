"""Which image a sandboxed run used, and the container sandbox's dependency
cache (issue #315): the daemon observes the image's content ID and registry
digest itself and runs the container from that ID
(``vscode-daemon/sandbox_image.py``); a named, labelled volume per project
holds the package caches, kept after a run, removed after a failed install,
quarantined after three in a row until a person resets it
(``vscode-daemon/sandbox_cache.py``). Ownership is the daemon's label and
name: a same-named volume without the label is neither used nor removed.
The backend records the daemon's report and never takes an identity that is
not a content ID (``services/contracts/sandbox_report.py``).

A fake ``docker`` (a Python script on the PATH) keeps volumes in a JSON file
and runs a container's command on the host in the mounted folder."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap

import pytest
import sandbox as daemon_sandbox  # the daemon's modules, on sys.path via conftest
import sandbox_cache
import sandbox_image
import shell_exec
from config import settings
from models.company import Company, ProjectDiscovery
from services.contracts import sandbox_report
from services.evidence import app_check
from services.workspace import sandbox
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company

PY = sys.executable
IMAGE_ID = "c" * 64
DIGEST = "d" * 64


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """A ``docker`` with volumes (``volumes.json``) and images
    (``images.json``: reference → inspect entry), logging every call."""
    state = tmp_path / "docker-state"
    state.mkdir()
    (state / "volumes.json").write_text("{}")
    (state / "images.json").write_text(
        json.dumps(
            {
                "python:3.12-slim": {
                    "Id": f"sha256:{IMAGE_ID}",
                    "RepoDigests": [
                        f"other/mirror@sha256:{'e' * 64}",
                        f"python@sha256:{DIGEST}",
                    ],
                }
            }
        )
    )
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(
        f"#!{PY}\n"
        + textwrap.dedent(
            f"""
            import json, os, subprocess, sys
            state = {str(state)!r}
            args = sys.argv[1:]
            open(os.path.join(state, "calls.log"), "a").write(json.dumps(args) + "\\n")
            def load(name):
                return json.load(open(os.path.join(state, name)))
            def save(name, data):
                json.dump(data, open(os.path.join(state, name), "w"))
            if args[:2] == ["image", "inspect"]:
                images = load("images.json")
                entry = images.get(args[2]) or next(
                    (e for e in images.values() if e["Id"].endswith(args[2])), None)
                if entry is None:
                    sys.stderr.write("Error: No such image: " + args[2]); sys.exit(1)
                print(json.dumps([entry])); sys.exit(0)
            if args[:2] == ["volume", "inspect"]:
                volumes = load("volumes.json")
                if args[2] not in volumes:
                    sys.stderr.write("Error: no such volume: " + args[2]); sys.exit(1)
                print(json.dumps([{{"Name": args[2], "Labels": volumes[args[2]]}}])); sys.exit(0)
            if args[:2] == ["volume", "create"]:
                volumes = load("volumes.json")
                labels = dict(a.split("=", 1) for a in args[3:-1:2])
                volumes[args[-1]] = labels; save("volumes.json", volumes); print(args[-1]); sys.exit(0)
            if args[:2] == ["volume", "rm"]:
                volumes = load("volumes.json"); volumes.pop(args[2], None); save("volumes.json", volumes); sys.exit(0)
            if args[0] == "kill":
                sys.exit(0)
            if args[0] == "run":
                images = load("images.json")
                if os.path.exists(os.path.join(state, "pull-on-run")):
                    # The run pulls the image: from now on the runtime has it.
                    images["node:22"] = {{"Id": "sha256:" + "f" * 64, "RepoDigests": []}}
                    save("images.json", images)
                if "--entrypoint" in args:  # the volume's ownership step
                    sys.exit(0)
                src = next(a.split(":/workspace")[0] for a in args if a.endswith(":/workspace:rw"))
                env = dict(os.environ)
                for i, a in enumerate(args):
                    if a == "-e":
                        k, _, v = args[i + 1].partition("=")
                        env[k] = v
                sys.exit(subprocess.run(["/bin/sh", "-c", args[-1]], cwd=src, env=env).returncode)
            sys.exit(0)
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FABRIQUE_SANDBOX_CACHE_STATE", str(tmp_path / "cache-state"))
    for name in (
        "FABRIQUE_SANDBOX_RUNTIME",
        "FABRIQUE_SANDBOX_REQUIRED",
        "FABRIQUE_SANDBOX_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)
    return state


def _calls(state) -> list[list[str]]:
    path = state / "calls.log"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _volumes(state) -> dict:
    return json.loads((state / "volumes.json").read_text())


def _run(work, command, purpose="install", **box):
    return shell_exec.execute_shell_command(
        command,
        str(work),
        {
            "mode": "container",
            "image": "python:3.12-slim",
            "cache": True,
            "purpose": purpose,
            **box,
        },
    )


# ---------------------------------------------------------------------------
# Which image ran
# ---------------------------------------------------------------------------


def test_the_container_runs_from_the_content_id_the_daemon_observed(docker, tmp_path):
    result = shell_exec.execute_shell_command(
        "echo ran", str(tmp_path), {"mode": "container", "image": "python:3.12-slim"}
    )
    assert result["success"] and result["stdout"] == "ran\n"
    identity = result["sandbox"]["image_identity"]
    # The digest of the reference's own repository, not another mirror's.
    assert identity == {
        "id": f"sha256:{IMAGE_ID}",
        "digest": f"python@sha256:{DIGEST}",
        "observed_by": "daemon",
        "observed": "before_run",
    }
    run = next(c for c in _calls(docker) if c[0] == "run")
    assert run[run.index("/bin/sh") - 1] == f"sha256:{IMAGE_ID}"
    assert "python:3.12-slim" not in run
    # No cache asked for: no volume touched, none reported.
    assert "cache" not in result["sandbox"]
    assert not any(c[0] == "volume" for c in _calls(docker))


def test_an_image_the_run_pulled_is_observed_after_it_and_said_so(docker, tmp_path):
    (docker / "pull-on-run").write_text("")
    result = shell_exec.execute_shell_command(
        "true", str(tmp_path), {"mode": "container", "image": "node:22"}
    )
    identity = result["sandbox"]["image_identity"]
    assert identity["id"] == "sha256:" + "f" * 64
    assert identity["observed"] == "after_run" and "pulled" in identity["note"]
    # A locally built image has no registry digest: the reason, not a blank.
    assert (
        identity["digest"] is None and "no registry digest" in identity["digest_reason"]
    )
    run = next(c for c in _calls(docker) if c[0] == "run")
    assert "node:22" in run  # Nothing to pin before the pull.


def test_no_identity_is_a_reason_never_an_empty_success(docker, tmp_path, monkeypatch):
    result = shell_exec.execute_shell_command(
        "true", str(tmp_path), {"mode": "container", "image": "missing:1"}
    )
    identity = result["sandbox"]["image_identity"]
    assert identity["id"] is None and "No such image" in identity["reason"]
    assert "No such image" in identity["before_run"]
    # bwrap runs no image; neither does the host.
    bwrap = daemon_sandbox.report(daemon_sandbox.normalize({"mode": "bwrap"}))
    assert bwrap["image_identity"]["id"] is None
    assert bwrap["image_identity"]["reason"].startswith("bwrap runs no image")
    # Podman leaves out "sha256:": recorded with it, run with Podman's spelling.
    images = json.loads((docker / "images.json").read_text())
    images["python:3.12-slim"]["Id"] = IMAGE_ID
    (docker / "images.json").write_text(json.dumps(images))
    box, seen = sandbox_image.pinned(
        str(docker.parent / "bin" / "docker"), {"image": "python:3.12-slim"}
    )
    assert box["image"] == IMAGE_ID and seen["id"] == f"sha256:{IMAGE_ID}"
    # An answer that is not a content ID is no identity.
    images["python:3.12-slim"]["Id"] = "latest"
    (docker / "images.json").write_text(json.dumps(images))
    seen = sandbox_image.identity("docker", "python:3.12-slim")
    assert seen["id"] is None and "no content ID" in seen["reason"]
    assert (
        sandbox_image.repository("registry.local:5000/team/app:1.2")
        == "registry.local:5000/team/app"
    )


def test_the_verifier_records_the_daemon_s_identity_and_refuses_a_forged_one():
    applied = {
        "mode": "container",
        "isolated": True,
        "network": "off",
        "runtime": "docker",
        "image": "python:3.12-slim",
        "env": [],
        "image_identity": {
            "id": f"sha256:{IMAGE_ID}",
            "digest": f"python@sha256:{DIGEST}",
            "observed_by": "daemon",
            "observed": "before_run",
        },
    }
    check = sandbox.check_sandbox(
        {"test_run": {"command": "pytest", "exit_code": 0, "sandbox": applied}}
    )
    assert f"content sha256:{IMAGE_ID}, python@sha256:{DIGEST}" in check["observation"]
    assert check["image_identity"]["id"] == f"sha256:{IMAGE_ID}"
    forged = {
        **applied,
        "image_identity": {"id": "trust-me", "digest": "python@sha256:x"},
    }
    record = sandbox_report.image_identity(forged)
    assert record["id"] is None and record["digest"] is None
    assert "no content ID" in record["reason"]


# ---------------------------------------------------------------------------
# The dependency cache: reuse, reset, quarantine
# ---------------------------------------------------------------------------


def test_the_cache_is_created_labelled_mounted_and_reused(
    docker, tmp_path, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(sandbox_cache.os, "getuid", lambda: 1000)
    monkeypatch.setattr(sandbox_cache.os, "getgid", lambda: 1000)
    first = _run(work, 'echo "$PIP_CACHE_DIR $npm_config_cache"')
    assert first["success"], first
    assert first["stdout"].strip() == "/fabrique-cache/pip /fabrique-cache/npm"
    cache = first["sandbox"]["cache"]
    key = sandbox_cache.project_key(str(work))
    name = f"fabrique-cache-{key}"
    assert cache["state"] == "created" and cache["mounted"] and cache["after"] == "kept"
    assert cache["volume"] == name and cache["failed_installs"] == 0
    assert _volumes(docker) == {name: {"dev.fabrique.sandbox-cache": key}}
    # Docker under a non-root uid: a fixed chown, root inside, only CHOWN.
    owner = next(c for c in _calls(docker) if "--entrypoint" in c)
    assert owner[owner.index("--entrypoint") + 1] == "chown" and "1000:1000" in owner
    assert "--cap-add" in owner and owner[owner.index("--cap-add") + 1] == "CHOWN"
    assert owner[owner.index("--network") + 1] == "none"
    run = [c for c in _calls(docker) if c[0] == "run" and "--entrypoint" not in c][-1]
    assert f"{name}:/fabrique-cache" in run
    second = _run(work, "true", purpose="test")
    assert second["sandbox"]["cache"]["state"] == "reused"
    assert second["sandbox"]["cache"]["after"] == "kept"
    assert len([c for c in _calls(docker) if c[:2] == ["volume", "create"]]) == 1


def test_a_failed_install_resets_and_three_in_a_row_quarantine_until_reset(
    docker, tmp_path
):
    work = tmp_path / "work"
    work.mkdir()
    name = f"fabrique-cache-{sandbox_cache.project_key(str(work))}"
    # A failing test run is no reason to reset the cache.
    tests = _run(work, "exit 1", purpose="test")
    assert tests["sandbox"]["cache"]["after"] == "kept" and name in _volumes(docker)
    for attempt in (1, 2):
        failed = _run(work, "exit 3")
        cache = failed["sandbox"]["cache"]
        assert cache["after"] == "removed" and cache["reset"] == {"removed": True}
        assert cache["failed_installs"] == attempt and "quarantined" not in cache
        assert name not in _volumes(docker)
    third = _run(work, "exit 3")["sandbox"]["cache"]
    assert third["failed_installs"] == 3
    assert third["quarantined"]["reason"] == "3 failed installs in a row"
    # Quarantined: the next run mounts nothing, and says why.
    held = _run(work, 'echo "${PIP_CACHE_DIR:-none}"')
    assert held["stdout"].strip() == "none"
    cache = held["sandbox"]["cache"]
    assert cache["state"] == "quarantined" and cache["mounted"] is False
    assert "quarantined until it is reset" in cache["reason"]
    status = sandbox_cache.status()
    assert status["enabled"] is True
    assert [q["volume"] for q in status["quarantined"]] == [name]
    assert status["quarantined"][0]["project_root"] == str(work.resolve())
    # A person's reset lifts it; the next install mounts a fresh volume.
    reset = sandbox_cache.reset(str(work), "docker")
    assert reset["quarantine_cleared"] is True and reset["volume_reset"] == {
        "removed": False,
        "absent": True,
    }
    assert sandbox_cache.status()["quarantined"] == []
    again = _run(work, "true")["sandbox"]["cache"]
    assert again["state"] == "created" and again["failed_installs"] == 0


def test_a_same_named_volume_without_the_label_is_never_used_or_removed(
    docker, tmp_path
):
    work = tmp_path / "work"
    work.mkdir()
    name = f"fabrique-cache-{sandbox_cache.project_key(str(work))}"
    (docker / "volumes.json").write_text(json.dumps({name: {"owner": "someone-else"}}))
    result = _run(work, "exit 1")
    cache = result["sandbox"]["cache"]
    assert cache["state"] == "refused" and cache["mounted"] is False
    assert "without the label" in cache["reason"]
    assert cache["reset"]["refused"] is True and cache["after"] == "not removed"
    assert _volumes(docker) == {name: {"owner": "someone-else"}}
    assert not any(c[:2] == ["volume", "rm"] for c in _calls(docker))
    run = next(c for c in _calls(docker) if c[0] == "run")
    assert not any("/fabrique-cache" in a for a in run)
    # Nor by a person's reset: the quarantine (if any) stays with the reason.
    reset = sandbox_cache.reset(str(work), "docker")
    assert (
        reset["volume_reset"]["refused"] is True
        and reset["quarantine_cleared"] is False
    )
    assert name in _volumes(docker)


def test_the_daemon_s_machine_can_refuse_the_cache(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_SANDBOX_CACHE", "off")
    result = _run(tmp_path, "exit 1")
    cache = result["sandbox"]["cache"]
    assert (
        cache["state"] == "refused" and "FABRIQUE_SANDBOX_CACHE=off" in cache["reason"]
    )
    assert not any(c[0] == "volume" for c in _calls(docker))
    assert sandbox_cache.status()["enabled"] is False


def test_a_task_worktree_shares_its_project_s_cache(tmp_path):
    project = tmp_path / "project"
    (project / ".git" / "worktrees" / "task-7").mkdir(parents=True)
    (project / ".git" / "worktrees" / "task-7" / "commondir").write_text("../..\n")
    worktree = project / ".git" / "fabrique-worktrees" / "task-7"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(
        f"gitdir: {project / '.git' / 'worktrees' / 'task-7'}\n"
    )
    (worktree / "src").mkdir()
    assert sandbox_cache.project_root(str(worktree / "src")) == project.resolve()
    assert sandbox_cache.project_key(str(worktree)) == sandbox_cache.project_key(
        str(project)
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    assert sandbox_cache.project_root(str(plain)) == plain.resolve()


def test_a_cache_asked_for_outside_container_mode_says_it_is_not_used(tmp_path):
    result = shell_exec.execute_shell_command(
        "true", str(tmp_path), {"mode": "host", "cache": True, "purpose": "install"}
    )
    assert result["sandbox"]["cache"]["state"] == "not_used"
    assert "only in container mode" in result["sandbox"]["cache"]["reason"]
    box = daemon_sandbox.normalize(
        {"mode": "container", "purpose": "deploy", "cache": "yes"}
    )
    assert box["purpose"] == "test" and box["cache"] is False


def test_the_reset_is_answered_over_nats(docker, tmp_path):
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    class _Msg:
        def __init__(self, body):
            self.body = body

    async def go():
        await sandbox_cache.handle_reset(
            None, _Msg({"workspace_root": str(tmp_path)}), reply, lambda m: m.body, None
        )
        await sandbox_cache.handle_reset(
            None,
            _Msg({"workspace_root": str(tmp_path / "nope")}),
            reply,
            lambda m: m.body,
            None,
        )

    asyncio.run(go())
    assert replies[0]["volume_reset"] == {"removed": False, "absent": True}
    assert replies[1]["error"]["status"] == 400


# ---------------------------------------------------------------------------
# The backend: the request, the install check, a person's reset
# ---------------------------------------------------------------------------


def test_the_backend_asks_for_the_cache_and_the_install_check_says_what_it_did(
    monkeypatch,
):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_CACHE", True)
    assert sandbox.request_for(Company(company_name="x"))["cache"] is True
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_CACHE", False)
    assert sandbox.request_for(Company(company_name="x"))["cache"] is False
    check = app_check.check_install(
        {
            "dependency_install": {
                "command": "pip install -r requirements.txt",
                "manifests": [{"path": "requirements.txt"}],
                "exit_code": 1,
                "output_tail": "No matching distribution",
                "sandbox": {
                    "mode": "container",
                    "image_identity": {
                        "id": f"sha256:{IMAGE_ID}",
                        "digest": None,
                        "digest_reason": "built here",
                    },
                    "cache": {
                        "state": "reused",
                        "mounted": True,
                        "volume": "fabrique-cache-0123",
                        "after": "removed",
                        "failed_installs": 3,
                        "quarantined": {"reason": "3 failed installs in a row"},
                    },
                },
            }
        }
    )
    assert check["outcome"] == "refuted"
    assert (
        f"content sha256:{IMAGE_ID}, no registry digest: built here"
        in check["observation"]
    )
    assert "reset (removed) after the failed install" in check["observation"]
    assert "now quarantined: 3 failed installs in a row" in check["observation"]
    assert check["cache"]["failed_installs"] == 3


class _ResetNATS:
    def __init__(self, reply):
        self.reply = reply
        self.subjects = []

    async def request(self, subject, payload, timeout=5.0):
        self.subjects.append((subject, payload))
        return self.reply

    async def close(self):
        pass


@pytest.mark.usefixtures("enforcement")
def test_a_manager_resets_the_cache_through_the_daemon_and_it_is_audited(
    test_client, sqlite_session
):
    company = _company(sqlite_session)
    url = f"/v1/companies/{company.id}/sandbox/cache/reset"
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    nats = _ResetNATS(
        {
            "volume": "fabrique-cache-0123",
            "volume_reset": {"removed": True},
            "quarantine_cleared": True,
            "failed_installs_before": 3,
        }
    )
    test_client.app.state.nats_client = nats
    assert test_client.post(url, headers=manager).status_code == 409  # no folder yet
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path="/srv/app"))
    sqlite_session.commit()
    assert test_client.post(url, headers=viewer).status_code == 403
    answer = test_client.post(url, headers=manager)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["quarantine_cleared"] is True and body["volume_reset"] == {
        "removed": True
    }
    assert body["audit_log_id"] is not None and body["path"] == "/srv/app"
    assert nats.subjects == [
        ("workspace.sandbox.reset", {"workspace_root": "/srv/app"})
    ]
    test_client.app.state.nats_client = _ResetNATS({"volume": "v"})
    assert test_client.post(url, headers=manager).status_code == 502
    test_client.app.state.nats_client = None
    assert test_client.post(url, headers=manager).status_code == 503
