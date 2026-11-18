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

With ``environment`` in the request (the backend's ``DELIVERY_SANDBOX_ENV``),
a second kind of volume keeps what an install put outside the worktree:
``fabrique-env-<key>`` at ``/fabrique-env``, with Python's user site
(``PYTHONUSERBASE``), Poetry's and Pipenv's virtualenvs and Go's ``GOPATH``,
module and build caches. Its key is the project, the request's ``scope`` (the
backend's ``company:<id>``), the image the company asked for and the sha256 of
the project's Python and Go manifests, so two companies on one folder never
share one, and a task whose manifests differ gets an environment of its own
instead of writing into the one other tasks read. Only an install mounts it
read-write (and creates it); every other run mounts it read-only, and only once
an install filled it. The same reuse / reset / quarantine as the cache, per
project and company; at most ``MAX_ENV_VOLUMES`` environments are kept. Each
volume carries ``dev.fabrique.sandbox-env=<key>`` and
``dev.fabrique.sandbox-env.project=<project key>``; a reset removes every
volume with both, each checked by name and label. ``FABRIQUE_SANDBOX_ENV=off``
refuses it.

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

# The environment volume (issue #315): what an install leaves outside the
# worktree, per project, company, manifests and image.
ENV_LABEL = "dev.fabrique.sandbox-env"
ENV_PROJECT_LABEL = "dev.fabrique.sandbox-env.project"
ENV_PREFIX = "fabrique-env-"
ENV_MOUNT = "/fabrique-env"
MAX_ENV_VOLUMES = 3
MAX_MANIFEST_FOLDERS = 64
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
ENV_MANIFESTS = (
    "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg",
    "go.mod", "go.sum", "go.work", "go.work.sum",
)
ENV_VARS = {
    # pip's user installation (the default when the image's site-packages is
    # not writable by the run's user) and the user site Python reads.
    "PYTHONUSERBASE": f"{ENV_MOUNT}/python",
    "POETRY_VIRTUALENVS_PATH": f"{ENV_MOUNT}/poetry",
    "WORKON_HOME": f"{ENV_MOUNT}/pipenv",
    "GOPATH": f"{ENV_MOUNT}/go",
    "GOMODCACHE": f"{ENV_MOUNT}/go/pkg/mod",
}
# Only an install writes Go's build cache here; another run keeps its own.
ENV_INSTALL_VARS = {"GOCACHE": f"{ENV_MOUNT}/go/build"}
ENV_PATH = (f"{ENV_MOUNT}/python/bin", f"{ENV_MOUNT}/go/bin")


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


def inspect_volume(runtime: str, name: str, key: str, label: str = LABEL) -> tuple[str, str]:
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
    if entry.get("Name", name) == name and isinstance(labels, dict) and labels.get(label) == key:
        return "owned", ""
    return "foreign", f"a volume named {name} exists without the label {label}={key}"


def _owner_args(runtime: str, mount: str = MOUNT) -> tuple[str, list[str]]:
    """How the volume becomes writable by the run's user: rootless Podman's
    ``:U`` mount option, or a one-off ``chown`` for Docker under a non-root
    uid (a new volume belongs to root); nothing when the daemon is root or on
    Windows."""
    if not hasattr(os, "getuid") or os.getuid() == 0:
        return "", []
    if os.path.basename(runtime).startswith("podman"):
        return ":U", []
    return "", ["chown", f"{os.getuid()}:{os.getgid()}", mount]


def _create(
    runtime: str, name: str, key: str, image: str, label: str = LABEL, mount: str = MOUNT, extra: tuple[str, ...] = ()
) -> str | None:
    """Create the volume with the daemon's label (and the *extra* labels);
    the reason when it failed."""
    try:
        labels = [part for value in (f"{label}={key}", *extra) for part in ("--label", value)]
        done = _call(runtime, "volume", "create", *labels, name)
        if done.returncode != 0:
            return f"the volume could not be created: {_said(done)}"
        _, chown = _owner_args(runtime, mount)
        if not chown:
            return None
        # A fixed command, never agent code: root inside, only CHOWN, no network.
        done = _call(
            runtime, "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges", "--user", "0:0", "-v", f"{name}:{mount}",
            "--entrypoint", chown[0], image, *chown[1:],
        )
    except (OSError, subprocess.SubprocessError) as exc:
        done = None
        reason = f"the container runtime did not answer: {exc}"
    else:
        reason = None if done.returncode == 0 else f"the volume could not be given to the daemon's user: {_said(done)}"
    if reason:
        remove(runtime, name, key, label)
    return reason


def remove(runtime: str, name: str, key: str, label: str = LABEL) -> dict[str, Any]:
    """Remove the volume only if it is the daemon's (name and label)."""
    found, detail = inspect_volume(runtime, name, key, label)
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


def _state_path(key: str, kind: str = "container") -> Path:
    return state_dir() / f"{key}-{kind}.json"


def load(key: str, kind: str = "container") -> dict[str, Any]:
    try:
        state = json.loads(_state_path(key, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(key: str, state: dict[str, Any], kind: str = "container") -> None:
    path = _state_path(key, kind)
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


def reset(cwd: str, runtime: str | None = None, scope: str = "") -> dict[str, Any]:
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
        # The project's environment volumes for that company too.
        out["environment_reset"] = reset_env(runtime, cwd, scope)
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


# ---------------------------------------------------------------------------
# The environment volume: what an install put outside the worktree
# ---------------------------------------------------------------------------


def env_enabled() -> bool:
    """Whether the daemon's machine allows the environment volume at all."""
    return os.getenv("FABRIQUE_SANDBOX_ENV", "on").strip().lower() not in ("off", "0", "false", "no")


def clean_scope(value: Any) -> str:
    """The request's scope (the backend's ``company:<id>``) when it is a plain
    name; anything else reads as no scope."""
    text = value.strip() if isinstance(value, str) else ""
    return text if 0 < len(text) <= 64 and all(c.isalnum() or c in ":_.-" for c in text) else ""


def _manifest_names(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and (
            p.name in ENV_MANIFESTS or (p.name.startswith("requirements") and p.suffix in (".txt", ".in"))))
    except OSError:
        return []


def manifests_digest(cwd: str) -> str:
    """The sha256 over the Python and Go manifests in *cwd* and its direct
    folders (a stack in a folder of its own), each file's path and content,
    read here; not a dot folder, at most ``MAX_MANIFEST_FOLDERS`` folders and
    ``MAX_MANIFEST_BYTES`` a file."""
    root = Path(cwd)
    folders = [root]
    try:
        folders += sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                          and not p.name.startswith(".") and p.name != "node_modules")[:MAX_MANIFEST_FOLDERS]
    except OSError:
        pass
    digest = hashlib.sha256()
    for folder in folders:
        for path in _manifest_names(folder):
            try:
                with path.open("rb") as handle:
                    content = handle.read(MAX_MANIFEST_BYTES)
            except OSError:
                continue
            digest.update(f"{path.relative_to(root).as_posix()}\0{len(content)}\0".encode())
            digest.update(content)
    return digest.hexdigest()


def env_project_key(cwd: str, scope: str) -> str:
    """The project and company an environment belongs to: its state, its
    quarantine and the label every one of its volumes carries."""
    return hashlib.sha256(f"env\0{project_root(cwd)}\0{scope}".encode()).hexdigest()[:16]


def env_volume_key(project: str, image: str, manifests: str) -> str:
    return hashlib.sha256(f"{project}\0{image}\0{manifests}".encode()).hexdigest()[:16]


def _env_args(name: str, runtime: str, writable: bool, base_path: str) -> list[str]:
    suffix = _owner_args(runtime, ENV_MOUNT)[0] if writable else ":ro"
    args = ["-v", f"{name}:{ENV_MOUNT}{suffix}"]
    variables = {**ENV_VARS, **(ENV_INSTALL_VARS if writable else {})}
    # The image's own binaries first: nothing in the volume shadows them.
    variables["PATH"] = ":".join((base_path, *ENV_PATH))
    for variable, value in variables.items():
        args += ["-e", f"{variable}={value}"]
    return args


def prepare_env(
    runtime: str, image: str, cwd: str, purpose: str, scope: str, base_path: str, run_image: str | None = None
) -> dict[str, Any]:
    """The environment volume for a container run in *cwd*: the report's start,
    and ``args`` when it is mounted. An install mounts it read-write (and
    creates it); every other run read-only, and only when an install filled
    it. *image* is the image the company asked for (part of the key);
    *run_image* (its content ID, else *image*) runs the ownership step of a
    new volume."""
    project = env_project_key(cwd, scope)
    manifests = manifests_digest(cwd)
    key = env_volume_key(project, image, manifests)
    name = f"{ENV_PREFIX}{key}"
    writable = purpose == "install"
    plan: dict[str, Any] = {"volume": name, "mount": ENV_MOUNT, "mounted": False, "writable": writable,
                            "project_key": project, "scope": scope or None, "manifests_sha256": manifests}
    if not env_enabled():
        return {**plan, "state": "refused", "reason": "FABRIQUE_SANDBOX_ENV=off on the daemon's machine"}
    state = load(project, "env")
    plan["failed_installs"] = int(state.get("failed_installs") or 0)
    quarantine = state.get("quarantined")
    if quarantine:
        reason = quarantine.get("reason") if isinstance(quarantine, dict) else str(quarantine)
        return {**plan, "state": "quarantined", "quarantined": quarantine,
                "reason": f"the project's sandbox environment is quarantined until it is reset: {reason}"}
    found, detail = inspect_volume(runtime, name, key, ENV_LABEL)
    if found == "foreign":
        return {**plan, "state": "refused", "reason": f"{detail}; it is not used"}
    if found == "error":
        return {**plan, "state": "unavailable", "reason": detail}
    if found == "absent":
        if not writable:
            return {**plan, "state": "absent",
                    "reason": "no install has filled the project's environment for these manifests and this image"}
        failure = _create(runtime, name, key, run_image or image, ENV_LABEL, ENV_MOUNT, (f"{ENV_PROJECT_LABEL}={project}",))
        if failure:
            return {**plan, "state": "unavailable", "reason": failure}
        state["volumes"] = [v for v in state.get("volumes") or [] if v != name] + [name]
        state.update({"project_root": str(project_root(cwd)), "scope": scope or None})
        save(project, state, "env")
        plan["state"] = "created"
    else:
        plan["state"] = "reused"
    return {**plan, "mounted": True, "args": _env_args(name, runtime, writable, base_path)}


def finish_env(runtime: str, plan: dict[str, Any], purpose: str, succeeded: bool) -> dict[str, Any]:
    """After the run, as ``finish`` for the cache: a failed install removes
    the volume and counts, three in a row quarantine the project for that
    company; a successful one clears the count and keeps at most
    ``MAX_ENV_VOLUMES`` environments, the least recently installed removed."""
    plan = {k: v for k, v in plan.items() if k != "args"}
    if not env_enabled():
        return plan
    if purpose != "install":
        if plan.get("mounted"):
            plan["after"] = "kept"
        return plan
    project, name = plan["project_key"], plan["volume"]
    state = load(project, "env")
    volumes = [v for v in state.get("volumes") or [] if isinstance(v, str) and v != name]
    if succeeded:
        state["failed_installs"] = 0
        if plan.get("mounted"):
            plan["after"] = "kept"
            volumes.append(name)
            evicted = []
            while len(volumes) > MAX_ENV_VOLUMES:
                old = volumes.pop(0)
                if remove(runtime, old, old[len(ENV_PREFIX):], ENV_LABEL).get("removed"):
                    evicted.append(old)
            if evicted:
                plan["evicted"] = evicted
    else:
        failures = int(state.get("failed_installs") or 0) + 1
        state["failed_installs"] = failures
        dropped = remove(runtime, name, name[len(ENV_PREFIX):], ENV_LABEL)
        plan["reset"] = dropped
        plan["after"] = "removed" if dropped.get("removed") else ("absent" if dropped.get("absent") else "not removed")
        if not (dropped.get("removed") or dropped.get("absent")):
            volumes.append(name)
        if failures >= MAX_FAILED_INSTALLS and not state.get("quarantined"):
            state["quarantined"] = {"reason": f"{failures} failed installs in a row",
                                    "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state["volumes"] = volumes
    save(project, state, "env")
    plan["failed_installs"] = state["failed_installs"]
    if state.get("quarantined"):
        plan["quarantined"] = state["quarantined"]
    return plan


def reset_env(runtime: str, cwd: str, scope: str) -> dict[str, Any]:
    """A person's reset for one company's project: every environment volume
    of it (the ones the state names and the ones the runtime lists under its
    project label, each removed only with the daemon's name and label); once
    all are gone the failures and the quarantine are cleared."""
    project = env_project_key(cwd, scope)
    state = load(project, "env")
    names = {v for v in state.get("volumes") or [] if isinstance(v, str)}
    try:
        listed = _call(runtime, "volume", "ls", "-q", "--filter", f"label={ENV_PROJECT_LABEL}={project}")
        names |= {n for n in listed.stdout.split() if n.startswith(ENV_PREFIX)} if listed.returncode == 0 else set()
    except (OSError, subprocess.SubprocessError):
        pass
    results = [{"volume": n, **remove(runtime, n, n[len(ENV_PREFIX):], ENV_LABEL)} for n in sorted(names)]
    left = [r["volume"] for r in results if not (r.get("removed") or r.get("absent"))]
    out: dict[str, Any] = {"project_key": project, "scope": scope or None, "volumes": results,
                           "failed_installs_before": int(state.get("failed_installs") or 0),
                           "quarantine_cleared": bool(not left and state.get("quarantined"))}
    if left:
        out["quarantined"] = state.get("quarantined")
    if state or not left:
        state.update({"volumes": left, "project_root": str(project_root(cwd)), "scope": scope or None})
        if not left:
            state.update({"failed_installs": 0, "quarantined": None})
        save(project, state, "env")
    return out


def env_status() -> dict[str, Any]:
    """Whether the environment volume is allowed, and the projects (with their
    company) in quarantine, from the daemon's own state files."""
    quarantined = []
    folder = state_dir()
    try:
        paths = sorted(folder.glob("*-env.json"))[:MAX_STATUS_ENTRIES] if folder.is_dir() else []
    except OSError:
        paths = []
    for path in paths:
        state = load(path.name[: -len("-env.json")], "env")
        if state.get("quarantined"):
            quarantined.append({key: state.get(key) for key in ("project_root", "scope", "failed_installs", "quarantined")})
    return {"enabled": env_enabled(), "quarantined": quarantined}


async def handle_reset(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.sandbox.reset`` → the reset of the project's cache volume."""
    root = request_body(msg).get("workspace_root")
    if not isinstance(root, str) or not os.path.isdir(root):
        body: dict[str, Any] = {"error": {"status": 400, "message": "workspace_root must be a folder on the daemon's machine"}}
    else:
        body = await asyncio.to_thread(reset, root, None, clean_scope(request_body(msg).get("scope")))
    await reply(nc, msg, body)


HANDLERS = (("workspace.sandbox.reset", handle_reset),)
