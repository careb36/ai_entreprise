"""The container sandbox's dependency cache: one named, labelled volume per project.

With ``cache`` in a container-mode request (the backend's
``DELIVERY_SANDBOX_CACHE``), the daemon mounts a volume of its own at
``/fabrique-cache`` and points the package managers' caches there (pip, npm,
Yarn, Go, Cargo, ``XDG_CACHE_HOME``), so an install does not download
everything again on every ephemeral container. The lifecycle:

- **reuse**: after a run that did not fail an install, the volume is kept;
- **reset**: after a failed install (non-zero exit or timeout), or when a
  person asks (``workspace.sandbox.reset``), the volume is removed;
- **quarantine**: ``MAX_FAILED_INSTALLS`` (3) failed installs in a row for a
  project stop the cache being mounted for that project, with the reason in
  every report and in the daemon's status, until a reset clears it.

Ownership is by label **and** name, never by walking the disk: the daemon
creates ``fabrique-cache-<key>`` with the label ``dev.fabrique.sandbox-cache=<key>``
(``<key>``: the first 16 hex characters of the sha256 of the project's root,
the main checkout for a task's worktree) and only ever mounts or removes a
volume that carries both. A volume with that name and without the label is
refused, never used, never removed. The state (failed installs in a row,
quarantine) is kept per project and sandbox mode under
``FABRIQUE_SANDBOX_CACHE_STATE`` (default ``~/.fabrique-daemon/sandbox-cache``).
``FABRIQUE_SANDBOX_CACHE=off`` on the daemon's machine refuses the cache.

The design follows AixleHQ/flow (Apache-2.0): ``runner/protocol/PROTOCOL.md``
§6.4 (scope by ownership, label and name; a same-named volume without the
label refused) and ``docs/design/agent-runtimes/DESIGN.md`` §5.2 and §7
(``reuse`` / ``reset``, a reset after a failed session, out of rotation after
three failures in a row until a reset). Design only: no code was copied.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

LABEL = "dev.fabrique.sandbox-cache"
PREFIX = "fabrique-cache-"
MOUNT = "/fabrique-cache"
MAX_FAILED_INSTALLS = 3
# Each runtime call (volume inspect, create, rm, the ownership run).
CALL_SECONDS = 60
# At most this many state files are read for the daemon's status.
MAX_STATUS_ENTRIES = 100
# Where each package manager keeps its cache, inside the volume.
CACHE_ENV = {
    "PIP_CACHE_DIR": f"{MOUNT}/pip",
    "npm_config_cache": f"{MOUNT}/npm",
    "YARN_CACHE_FOLDER": f"{MOUNT}/yarn",
    "GOMODCACHE": f"{MOUNT}/go/mod",
    "GOCACHE": f"{MOUNT}/go/build",
    "CARGO_HOME": f"{MOUNT}/cargo",
    "XDG_CACHE_HOME": f"{MOUNT}/xdg",
}


def enabled() -> bool:
    """Whether the daemon's machine allows the cache at all."""
    return os.getenv("FABRIQUE_SANDBOX_CACHE", "on").strip().lower() not in ("off", "0", "false", "no")


def state_dir() -> Path:
    return Path(os.getenv("FABRIQUE_SANDBOX_CACHE_STATE", "~/.fabrique-daemon/sandbox-cache")).expanduser()


def project_root(cwd: str) -> Path:
    """The project a folder belongs to: the main checkout of a Git worktree
    (its ``.git`` file names a gitdir whose ``commondir`` is the main
    ``.git``), the repository's top for a checkout, else the folder itself."""
    here = Path(cwd).resolve()
    for folder in (here, *here.parents):
        marker = folder / ".git"
        if marker.is_dir():
            return folder
        if marker.is_file():
            try:
                text = marker.read_text(encoding="utf-8").strip()
            except OSError:
                return folder
            if not text.startswith("gitdir:"):
                return folder
            gitdir = Path(text[len("gitdir:"):].strip())
            gitdir = gitdir if gitdir.is_absolute() else folder / gitdir
            common = gitdir
            try:
                common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
            except OSError:
                pass
            return common.parent if common.name == ".git" else folder
    return here


def project_key(cwd: str) -> str:
    return hashlib.sha256(str(project_root(cwd)).encode("utf-8")).hexdigest()[:16]


def volume_name(key: str) -> str:
    return f"{PREFIX}{key}"


def _call(runtime: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([runtime, *args], capture_output=True, text=True, timeout=CALL_SECONDS, check=False)


def _said(done: subprocess.CompletedProcess) -> str:
    return (done.stderr or done.stdout or "").strip()[-300:] or f"exit {done.returncode}"


def inspect_volume(runtime: str, name: str, key: str) -> tuple[str, str]:
    """``owned`` (the name and the daemon's label for *key*), ``foreign`` (the
    name without that label), ``absent``, or ``error`` with the reason."""
    try:
        done = _call(runtime, "volume", "inspect", name)
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", f"the container runtime did not answer: {exc}"
    if done.returncode != 0:
        said = _said(done)
        return ("absent", said) if "no such" in said.lower() else ("error", said)
    try:
        entries = json.loads(done.stdout or "")
        entry = entries[0] if isinstance(entries, list) else entries
        labels = entry.get("Labels") or {}
    except (ValueError, IndexError, AttributeError, TypeError):
        return "error", "the runtime's description of the volume could not be read"
    if entry.get("Name", name) == name and isinstance(labels, dict) and labels.get(LABEL) == key:
        return "owned", ""
    return "foreign", f"a volume named {name} exists without the label {LABEL}={key}"


def _owner_args(runtime: str) -> tuple[str, list[str]]:
    """How the volume becomes writable by the run's user: rootless Podman's
    ``:U`` mount option, or a one-off ``chown`` for Docker under a non-root
    uid (a new volume belongs to root); nothing when the daemon is root or on
    Windows."""
    if not hasattr(os, "getuid") or os.getuid() == 0:
        return "", []
    if os.path.basename(runtime).startswith("podman"):
        return ":U", []
    return "", ["chown", f"{os.getuid()}:{os.getgid()}", MOUNT]


def _create(runtime: str, name: str, key: str, image: str) -> str | None:
    """Create the volume with the daemon's label; the reason when it failed."""
    try:
        done = _call(runtime, "volume", "create", "--label", f"{LABEL}={key}", name)
        if done.returncode != 0:
            return f"the cache volume could not be created: {_said(done)}"
        _, chown = _owner_args(runtime)
        if not chown:
            return None
        # A fixed command, never agent code: root inside, only CHOWN, no network.
        done = _call(
            runtime, "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges", "--user", "0:0", "-v", f"{name}:{MOUNT}",
            "--entrypoint", chown[0], image, *chown[1:],
        )
    except (OSError, subprocess.SubprocessError) as exc:
        done = None
        reason = f"the container runtime did not answer: {exc}"
    else:
        reason = None if done.returncode == 0 else f"the cache volume could not be given to the daemon's user: {_said(done)}"
    if reason:
        remove(runtime, name, key)
    return reason


def remove(runtime: str, name: str, key: str) -> dict[str, Any]:
    """Remove the volume only if it is the daemon's (name and label)."""
    found, detail = inspect_volume(runtime, name, key)
    if found == "absent":
        return {"removed": False, "absent": True}
    if found == "foreign":
        return {"removed": False, "refused": True, "reason": f"{detail}; it is not the daemon's and was not removed"}
    if found == "error":
        return {"removed": False, "reason": detail}
    try:
        done = _call(runtime, "volume", "rm", name)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"removed": False, "reason": f"the container runtime did not answer: {exc}"}
    if done.returncode != 0:
        return {"removed": False, "reason": _said(done)}
    return {"removed": True}


def _state_path(key: str) -> Path:
    return state_dir() / f"{key}-container.json"


def load(key: str) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(key: str, state: dict[str, Any]) -> None:
    path = _state_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def prepare(runtime: str, image: str, cwd: str) -> dict[str, Any]:
    """The cache for a container run in *cwd*: the report's start, and
    ``args`` (the mount and variables) when it is mounted. *image* runs the
    ownership step of a new volume (the image the run itself uses)."""
    key = project_key(cwd)
    name = volume_name(key)
    plan: dict[str, Any] = {"volume": name, "mount": MOUNT, "mounted": False, "project_key": key}
    if not enabled():
        return {**plan, "state": "refused", "reason": "FABRIQUE_SANDBOX_CACHE=off on the daemon's machine"}
    state = load(key)
    plan["failed_installs"] = int(state.get("failed_installs") or 0)
    quarantine = state.get("quarantined")
    if quarantine:
        reason = quarantine.get("reason") if isinstance(quarantine, dict) else str(quarantine)
        return {**plan, "state": "quarantined", "quarantined": quarantine,
                "reason": f"the project's sandbox cache is quarantined until it is reset: {reason}"}
    found, detail = inspect_volume(runtime, name, key)
    if found == "owned":
        plan["state"] = "reused"
    elif found == "foreign":
        return {**plan, "state": "refused", "reason": f"{detail}; it is not used"}
    elif found == "error":
        return {**plan, "state": "unavailable", "reason": detail}
    else:
        failure = _create(runtime, name, key, image)
        if failure:
            return {**plan, "state": "unavailable", "reason": failure}
        plan["state"] = "created"
    suffix, _ = _owner_args(runtime)
    args = ["-v", f"{name}:{MOUNT}{suffix}"]
    for variable, value in CACHE_ENV.items():
        args += ["-e", f"{variable}={value}"]
    return {**plan, "mounted": True, "args": args}


def finish(runtime: str, plan: dict[str, Any], purpose: str, succeeded: bool, cwd: str) -> dict[str, Any]:
    """After the run: keep the volume (reuse), or, after a failed install,
    remove it (reset) and count the failure, quarantining the project at
    ``MAX_FAILED_INSTALLS`` in a row; a successful install clears the count."""
    plan = {k: v for k, v in plan.items() if k != "args"}
    if not enabled():
        return plan
    if purpose != "install":
        if plan.get("mounted"):
            plan["after"] = "kept"
        return plan
    key = plan["project_key"]
    state = load(key)
    if succeeded:
        state["failed_installs"] = 0
        if plan.get("mounted"):
            plan["after"] = "kept"
    else:
        failures = int(state.get("failed_installs") or 0) + 1
        state["failed_installs"] = failures
        reset = remove(runtime, plan["volume"], key)
        plan["reset"] = reset
        plan["after"] = "removed" if reset.get("removed") else ("absent" if reset.get("absent") else "not removed")
        if failures >= MAX_FAILED_INSTALLS and not state.get("quarantined"):
            state["quarantined"] = {
                "reason": f"{failures} failed installs in a row",
                "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
    state.update({"volume": plan["volume"], "project_root": str(project_root(cwd))})
    save(key, state)
    plan["failed_installs"] = state["failed_installs"]
    if state.get("quarantined"):
        plan["quarantined"] = state["quarantined"]
    return plan


def reset(cwd: str, runtime: str | None = None) -> dict[str, Any]:
    """A person's reset: remove the project's volume (the daemon's only) and,
    once it is gone, clear the failures and the quarantine. A volume that is
    not the daemon's, or one that could not be removed, leaves the
    quarantine in place, with the reason."""
    key = project_key(cwd)
    name = volume_name(key)
    out: dict[str, Any] = {"project_key": key, "volume": name, "project_root": str(project_root(cwd))}
    if runtime is None:
        import sandbox  # Late: sandbox imports this module.

        try:
            runtime = sandbox.container_runtime()
        except sandbox.SandboxUnavailable as exc:
            runtime = None
            out["volume_reset"] = {"removed": False, "reason": str(exc)}
    if runtime is not None:
        out["volume_reset"] = remove(runtime, name, key)
    state = load(key)
    gone = out["volume_reset"].get("removed") or out["volume_reset"].get("absent")
    out["failed_installs_before"] = int(state.get("failed_installs") or 0)
    out["quarantine_cleared"] = bool(gone and state.get("quarantined"))
    if gone:
        state.update({"failed_installs": 0, "quarantined": None, "volume": name, "project_root": out["project_root"]})
        save(key, state)
    else:
        out["quarantined"] = state.get("quarantined")
    return out


def status() -> dict[str, Any]:
    """For ``workspace.status``: whether the cache is allowed, and the
    projects in quarantine, read from the daemon's own state files."""
    quarantined = []
    folder = state_dir()
    try:
        paths = sorted(folder.glob("*-container.json"))[:MAX_STATUS_ENTRIES] if folder.is_dir() else []
    except OSError:
        paths = []
    for path in paths:
        state = load(path.name[: -len("-container.json")])
        if state.get("quarantined"):
            quarantined.append({
                "project_root": state.get("project_root"),
                "volume": state.get("volume"),
                "failed_installs": state.get("failed_installs"),
                "quarantined": state["quarantined"],
            })
    return {"enabled": enabled(), "quarantined": quarantined}


async def handle_reset(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.sandbox.reset`` → the reset of the project's cache volume."""
    root = request_body(msg).get("workspace_root")
    if not isinstance(root, str) or not os.path.isdir(root):
        body: dict[str, Any] = {"error": {"status": 400, "message": "workspace_root must be a folder on the daemon's machine"}}
    else:
        body = await asyncio.to_thread(reset, root)
    await reply(nc, msg, body)


HANDLERS = (("workspace.sandbox.reset", handle_reset),)
