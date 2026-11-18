"""The container sandbox's environment volume (issue #315): one named,
labelled volume per project and company, also keyed by the project's
dependency manifests and the image, where Python's user site, Poetry's and
Pipenv's virtualenvs and Go's GOPATH, module and build caches live, so what an
install put there is still there at the next run. Writable only by an
install; mounted read-only by every other run. Reset and quarantine as the
dependency cache (``sandbox_cache.py``).

A fake ``docker`` (a Python script on the PATH) keeps volumes as folders,
maps a volume's mount and the variables that point into it to that folder,
and runs the container's command on the host."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest
import sandbox
import sandbox_cache

PY = sys.executable
IMAGE_ID = "c" * 64
WRITE = 'mkdir -p "$PYTHONUSERBASE/lib" && echo installed > "$PYTHONUSERBASE/lib/pkg.txt"'
READ = 'cat "$PYTHONUSERBASE/lib/pkg.txt"'


@pytest.fixture
def docker(tmp_path, monkeypatch):
    state = tmp_path / "docker-state"
    (state / "vols").mkdir(parents=True)
    (state / "volumes.json").write_text("{}")
    images = {"python:3.12-slim": {"Id": f"sha256:{IMAGE_ID}", "RepoDigests": []},
              "python:3.13-slim": {"Id": "sha256:" + "a" * 64, "RepoDigests": []}}
    (state / "images.json").write_text(json.dumps(images))
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(f"#!{PY}\n" + textwrap.dedent(f"""
        import json, os, subprocess, sys
        state = {str(state)!r}
        args = sys.argv[1:]
        open(os.path.join(state, "calls.log"), "a").write(json.dumps(args) + "\\n")
        def load(n): return json.load(open(os.path.join(state, n)))
        def save(n, d): json.dump(d, open(os.path.join(state, n), "w"))
        if args[:2] == ["image", "inspect"]:
            images = load("images.json")
            entry = images.get(args[2]) or next((e for e in images.values() if e["Id"].endswith(args[2])), None)
            if entry is None:
                sys.stderr.write("Error: No such image"); sys.exit(1)
            print(json.dumps([entry])); sys.exit(0)
        if args[:2] == ["volume", "inspect"]:
            v = load("volumes.json")
            if args[2] not in v:
                sys.stderr.write("Error: no such volume: " + args[2]); sys.exit(1)
            print(json.dumps([{{"Name": args[2], "Labels": v[args[2]]}}])); sys.exit(0)
        if args[:2] == ["volume", "create"]:
            v = load("volumes.json")
            v[args[-1]] = dict(a.split("=", 1) for a in args[3:-1:2]); save("volumes.json", v)
            os.makedirs(os.path.join(state, "vols", args[-1]), exist_ok=True); sys.exit(0)
        if args[:2] == ["volume", "rm"]:
            v = load("volumes.json"); v.pop(args[2], None); save("volumes.json", v); sys.exit(0)
        if args[:2] == ["volume", "ls"]:
            want = args[args.index("--filter") + 1].split("=", 1)[1]
            k, _, val = want.partition("=")
            print("\\n".join(n for n, labels in load("volumes.json").items() if labels.get(k) == val)); sys.exit(0)
        if args[0] == "run":
            if "--entrypoint" in args:
                sys.exit(0)
            src = next(a.split(":/workspace")[0] for a in args if a.endswith(":/workspace:rw"))
            mounts = {{}}
            for i, a in enumerate(args):
                if a == "-v" and ":/fabrique-" in args[i + 1]:
                    name, mount = args[i + 1].split(":")[:2]
                    mounts[mount] = os.path.join(state, "vols", name)
            env = dict(os.environ)
            for i, a in enumerate(args):
                if a == "-e":
                    k, _, val = args[i + 1].partition("=")
                    for mount, host in mounts.items():
                        val = val.replace(mount, host)
                    env[k] = val
            sys.exit(subprocess.run(["/bin/sh", "-c", args[-1]], cwd=src, env=env).returncode)
        sys.exit(0)
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FABRIQUE_SANDBOX_CACHE_STATE", str(tmp_path / "cache-state"))
    for name in ("FABRIQUE_SANDBOX_RUNTIME", "FABRIQUE_SANDBOX_REQUIRED", "FABRIQUE_SANDBOX_CACHE", "FABRIQUE_SANDBOX_ENV"):
        monkeypatch.delenv(name, raising=False)
    return state


def _calls(state):
    path = state / "calls.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _volumes(state):
    return json.loads((state / "volumes.json").read_text())


def _project(tmp_path, name="app", manifest="flask==3.0\n"):
    work = tmp_path / name
    work.mkdir()
    (work / "requirements.txt").write_text(manifest)
    return work


def _run(work, command, purpose="install", scope="company:1", **box):
    request = {"mode": "container", "image": "python:3.12-slim", "environment": True,
               "scope": scope, "purpose": purpose, **box}
    return sandbox.run_sandboxed(command, str(work), request, 60)


def _env_args(state):
    run = [c for c in _calls(state) if c[0] == "run" and "--entrypoint" not in c][-1]
    env = dict(run[i + 1].split("=", 1) for i, a in enumerate(run) if a == "-e")
    mount = next(run[i + 1] for i, a in enumerate(run) if a == "-v" and ":/fabrique-env" in run[i + 1])
    return env, mount


# ---------------------------------------------------------------------------
# What an install leaves is there at the next run
# ---------------------------------------------------------------------------


def test_an_install_fills_the_environment_and_a_later_test_run_reads_it(docker, tmp_path):
    work = _project(tmp_path)
    first = _run(work, WRITE)
    assert first["success"], first
    env = first["sandbox"]["environment"]
    assert env["state"] == "created" and env["mounted"] and env["writable"] and env["after"] == "kept"
    name = env["volume"]
    assert name.startswith("fabrique-env-")
    labels = _volumes(docker)[name]
    assert labels[sandbox_cache.ENV_LABEL] == name[len("fabrique-env-"):]
    assert labels[sandbox_cache.ENV_PROJECT_LABEL] == env["project_key"]
    variables, mount = _env_args(docker)
    assert mount == f"{name}:/fabrique-env"
    assert variables["PYTHONUSERBASE"] == "/fabrique-env/python"
    assert variables["GOPATH"] == "/fabrique-env/go"
    assert variables["GOMODCACHE"] == "/fabrique-env/go/pkg/mod"
    assert variables["GOCACHE"] == "/fabrique-env/go/build"
    # The image's own binaries come first: a file planted in the volume's bin
    # folders never shadows ``python`` or ``go``.
    assert variables["PATH"].startswith(sandbox.BASE_ENV["PATH"] + ":")
    assert variables["PATH"].endswith("/fabrique-env/python/bin:/fabrique-env/go/bin")

    second = _run(work, READ, purpose="test")
    assert second["success"] and second["stdout"] == "installed\n"
    env = second["sandbox"]["environment"]
    assert env["state"] == "reused" and env["volume"] == name and not env["writable"]
    variables, mount = _env_args(docker)
    # Only an install writes it; Go's build cache of a test run is elsewhere.
    assert mount == f"{name}:/fabrique-env:ro"
    assert "GOCACHE" not in variables


def test_a_run_before_any_install_mounts_nothing_and_creates_nothing(docker, tmp_path):
    result = _run(_project(tmp_path), "true", purpose="test")
    env = result["sandbox"]["environment"]
    assert env["state"] == "absent" and not env["mounted"] and "no install" in env["reason"]
    assert _volumes(docker) == {}


def test_another_company_and_another_project_never_share_a_volume(docker, tmp_path):
    work = _project(tmp_path)
    mine = _run(work, WRITE)["sandbox"]["environment"]["volume"]
    theirs = _run(work, READ, purpose="test", scope="company:2")["sandbox"]["environment"]
    assert theirs["volume"] != mine and theirs["state"] == "absent"
    other = _run(_project(tmp_path, "other"), READ, purpose="test")["sandbox"]["environment"]
    assert other["volume"] != mine and other["state"] == "absent"


def test_a_task_worktree_shares_its_projects_environment(docker, tmp_path):
    work = _project(tmp_path)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    tree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(work), "worktree", "add", "-q", str(tree)], check=True)
    name = _run(work, WRITE)["sandbox"]["environment"]["volume"]
    result = _run(tree, READ, purpose="test")
    assert result["stdout"] == "installed\n" and result["sandbox"]["environment"]["volume"] == name


def test_changed_manifests_or_another_image_get_an_environment_of_their_own(docker, tmp_path):
    work = _project(tmp_path)
    first = _run(work, WRITE)["sandbox"]["environment"]
    (work / "requirements.txt").write_text("flask==3.1\n")
    changed = _run(work, READ, purpose="test")["sandbox"]["environment"]
    assert changed["volume"] != first["volume"] and changed["state"] == "absent"
    assert changed["project_key"] == first["project_key"]
    (work / "requirements.txt").write_text("flask==3.0\n")
    image = _run(work, READ, purpose="test", image="python:3.13-slim")["sandbox"]["environment"]
    assert image["volume"] != first["volume"]
    # Back on the first manifests and image: the first environment again.
    assert _run(work, READ, purpose="test")["stdout"] == "installed\n"


def test_manifests_in_a_stack_folder_count_too(tmp_path):
    work = _project(tmp_path)
    before = sandbox_cache.manifests_digest(str(work))
    (work / "service").mkdir()
    (work / "service" / "go.mod").write_text("module x\n")
    with_folder = sandbox_cache.manifests_digest(str(work))
    assert with_folder != before
    # Neither a file that is no manifest nor a dot folder changes the key.
    (work / "README.md").write_text("not a manifest")
    (work / ".hidden").mkdir()
    (work / ".hidden" / "go.mod").write_text("module y\n")
    assert sandbox_cache.manifests_digest(str(work)) == with_folder


def test_at_most_three_environments_are_kept_per_project(docker, tmp_path):
    work = _project(tmp_path)
    names = []
    for version in range(4):
        (work / "requirements.txt").write_text(f"flask==3.{version}\n")
        env = _run(work, WRITE)["sandbox"]["environment"]
        names.append(env["volume"])
    assert env["evicted"] == [names[0]]
    assert set(_volumes(docker)) == set(names[1:])


# ---------------------------------------------------------------------------
# Reset and quarantine, as the dependency cache
# ---------------------------------------------------------------------------


def test_a_failed_install_resets_and_three_quarantine_until_a_reset(docker, tmp_path):
    work = _project(tmp_path)
    for attempt in range(1, 4):
        env = _run(work, f"{WRITE} && exit 1")["sandbox"]["environment"]
        assert env["after"] == "removed" and env["failed_installs"] == attempt
    assert "3 failed installs in a row" in env["quarantined"]["reason"]
    assert _volumes(docker) == {}
    blocked = _run(work, WRITE)["sandbox"]["environment"]
    assert blocked["state"] == "quarantined" and not blocked["mounted"]
    assert "quarantined" in blocked["reason"]
    status = sandbox_cache.env_status()
    assert status["enabled"] and status["quarantined"][0]["scope"] == "company:1"
    assert sandbox_cache.status()["quarantined"] == []  # the cache's own: untouched

    # A reset for another company lifts nothing.
    other = sandbox_cache.reset(str(work), runtime="docker", scope="company:2")
    assert not other["environment_reset"]["quarantine_cleared"]
    assert _run(work, WRITE)["sandbox"]["environment"]["state"] == "quarantined"

    done = sandbox_cache.reset(str(work), runtime="docker", scope="company:1")
    assert done["environment_reset"]["quarantine_cleared"]
    assert sandbox_cache.env_status()["quarantined"] == []
    assert _run(work, WRITE)["sandbox"]["environment"]["state"] == "created"


def test_a_reset_removes_every_environment_of_the_project_and_company(docker, tmp_path):
    work = _project(tmp_path)
    mine = _run(work, WRITE)["sandbox"]["environment"]["volume"]
    (work / "requirements.txt").write_text("flask==3.1\n")
    mine2 = _run(work, WRITE)["sandbox"]["environment"]["volume"]
    theirs = _run(work, WRITE, scope="company:2")["sandbox"]["environment"]["volume"]
    out = sandbox_cache.reset(str(work), runtime="docker", scope="company:1")["environment_reset"]
    assert sorted(v["volume"] for v in out["volumes"] if v["removed"]) == sorted([mine, mine2])
    assert set(_volumes(docker)) == {theirs}


def test_a_same_named_volume_without_the_label_is_never_used_or_removed(docker, tmp_path):
    work = _project(tmp_path)
    key = sandbox_cache.env_volume_key(
        sandbox_cache.env_project_key(str(work), "company:1"), "python:3.12-slim",
        sandbox_cache.manifests_digest(str(work)))
    name = f"fabrique-env-{key}"
    (docker / "volumes.json").write_text(json.dumps({name: {"owner": "someone"}}))
    env = _run(work, f"{WRITE} && exit 1")["sandbox"]["environment"]
    assert env["state"] == "refused" and not env["mounted"]
    assert name in _volumes(docker)


def test_the_daemons_machine_can_refuse_the_environment(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_SANDBOX_ENV", "off")
    env = _run(_project(tmp_path), WRITE)["sandbox"]["environment"]
    assert env["state"] == "refused" and "FABRIQUE_SANDBOX_ENV=off" in env["reason"]
    assert _volumes(docker) == {}


def test_outside_container_mode_the_environment_is_not_used_and_says_why():
    box = sandbox.normalize({"mode": "bwrap", "environment": True})
    assert sandbox.report(box)["environment"]["state"] == "not_used"


def test_the_request_scope_is_kept_only_when_it_is_a_plain_name():
    assert sandbox.normalize({"mode": "container", "scope": "company:7"})["scope"] == "company:7"
    assert sandbox.normalize({"mode": "container", "scope": "../x y"})["scope"] == ""
    assert sandbox.normalize({"mode": "container", "environment": "yes"})["environment"] is False


def test_the_reset_over_nats_passes_the_company(docker, tmp_path, monkeypatch):
    work = _project(tmp_path)
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", "docker")
    _run(work, WRITE)
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    async def go():
        await sandbox_cache.handle_reset(None, None, reply,
                                         lambda m: {"workspace_root": str(work), "scope": "company:1"}, None)

    asyncio.run(go())
    assert replies[0]["environment_reset"]["scope"] == "company:1"
    assert _volumes(docker) == {}
