"""Landing and mirroring the self-hosting source, on this computer (PROTOCOL.md §5.10).

The owner actions ``land_branch`` and ``push_main``, and the observations
``close_issue`` needs (``docs/design/owner-actions.md``), run here because this
daemon is the only component that touches the user's files and holds this
machine's git credentials. The repository is always this machine's
``FABRIQUE_SELF_HOST_SOURCE`` (``self_host_checkout.source_repository``),
never one the request names. Three subjects:

- ``workspace.self_host.git`` (read-only) with a ``view``:
  ``branches`` (the local branches but ``main``), ``land_preview`` (a listed
  branch's commits, diffstat and changed files against ``main``, and main's
  test map, from which the backend computes the protected paths),
  ``origin`` (the remote's URL without credentials), ``push_preview`` (after a
  fetch: the commits of ``origin/main..main``, whether that is a
  fast-forward, whether ``main`` has land.sh's verification record) and
  ``fix_commit`` (after a fetch: the commits on ``main`` whose message names
  ``#<issue>``, and whether ``origin/main`` contains each).
- ``workspace.self_host.land``: ``scripts/land.sh <branch>`` as an argument
  list, never through a shell, never ``--push``. ``LAND_*`` variables of this
  daemon's environment are dropped; ``LAND_OWNER=1`` is set for that process
  only when the request's ``land_owner`` is ``true`` (the backend sends it
  after the Owner's step-up). The branch must be one ``branches`` lists, pass
  ``git check-ref-format --branch`` and still be at the commit the preview
  showed.
- ``workspace.self_host.push``: after a fetch, ``git push --atomic origin
  <previewed main>:refs/heads/main`` and land.sh's notes ref, never
  ``--force`` and never a ``+`` refspec, with this machine's own credentials
  (no prompt, ``git_env``). Refused unless ``origin/main`` is an ancestor of
  ``main`` (a fast-forward), ``main`` and ``origin/main`` are where the
  preview saw them, and ``main`` has a verification record.

Every reply is what this daemon observed (refs, exit codes, files it read).
land.sh's verification record is passed on as land.sh wrote it: it says of
itself that it is a claim by the machine that ran the checks.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import git_env
from git_local import remote_url
from git_workspace import GitError, run_git
from self_host_checkout import Refused, source_repository

GIT_SUBJECT = "workspace.self_host.git"
LAND_SUBJECT = "workspace.self_host.land"
PUSH_SUBJECT = "workspace.self_host.push"
MAIN = "main"
REMOTE = "origin"
NOTES_REF = "refs/notes/verification"
TIMEOUT_ENV = "FABRIQUE_SELF_HOST_LAND_TIMEOUT"
LAND_TIMEOUT_SECONDS = 5400
FETCH_SECONDS = 120
PUSH_SECONDS = 300
MAX_COMMITS = 200
MAX_FILES = 500
MAX_BRANCHES = 200
OUTPUT_TAIL = 6000
RECORD_MAX_BYTES = 256 * 1024
KEYRING = ("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
# A plain branch name: no leading '-', no "..", no control character, no space.
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
SHA = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")
EXIT_MEANING = {0: "landed", 1: "checks_failed_or_conflict", 2: "usage_or_environment",
                3: "refused", 4: "locked"}


class Conflict(Refused):
    """Refused because the repository is not as the preview saw it (a 409)."""


def _git(args: List[str], root: Path, timeout: float = 60) -> str:
    return run_git(args, root, timeout=timeout).stdout.strip()


def _commit(root: Path, ref: str) -> Optional[str]:
    found = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root, check=False)
    return found.stdout.strip() or None if found.returncode == 0 else None


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    return run_git(["merge-base", "--is-ancestor", older, newer], root, check=False).returncode == 0


def _log(root: Path, revisions: List[str], extra: Optional[List[str]] = None) -> Dict[str, Any]:
    out = _git(["log", f"--max-count={MAX_COMMITS + 1}", "--format=%H%x1f%s", *(extra or []),
                *revisions, "--"], root)
    rows = [line.split("\x1f", 1) for line in out.splitlines() if line]
    commits = [{"sha": r[0], "subject": r[1] if len(r) > 1 else ""} for r in rows]
    return {"commits": commits[:MAX_COMMITS], "truncated": len(commits) > MAX_COMMITS}


def _fetch(root: Path) -> None:
    try:
        run_git(["fetch", "--quiet", REMOTE, f"+refs/heads/{MAIN}:refs/remotes/{REMOTE}/{MAIN}"],
                root, timeout=FETCH_SECONDS)
    except GitError as e:
        reason = "remote_needs_interaction" if git_env.needs_interaction(str(e)) else "remote_unreachable"
        raise GitError(f"{reason}: cannot fetch {MAIN} from {REMOTE}: {e}") from e


def branches(root: Path) -> List[Dict[str, str]]:
    out = _git(["for-each-ref", "--sort=-committerdate", f"--count={MAX_BRANCHES + 1}",
                "--format=%(refname:short)%09%(objectname)%09%(subject)", "refs/heads"], root)
    found = []
    for line in out.splitlines():
        name, head, subject = (line.split("\t", 2) + ["", ""])[:3]
        if name and name != MAIN:
            found.append({"name": name, "head": head, "subject": subject})
    return found[:MAX_BRANCHES]


def listed_branch(name: Any, root: Path) -> str:
    """The head of *name*, a branch this daemon lists; ``Refused`` otherwise."""
    if not isinstance(name, str) or not BRANCH.match(name) or name == MAIN or ".." in name \
            or name.endswith((".lock", "/", ".")) or "//" in name or "/." in name:
        raise Refused("branch must be a plain local branch name other than main")
    if run_git(["check-ref-format", "--branch", name], root, check=False).returncode != 0:
        raise Refused(f"{name!r} is not a valid branch name")
    for branch in branches(root):
        if branch["name"] == name:
            return branch["head"]
    raise Refused(f"{name!r} is not one of this repository's local branches")


def _test_map(root: Path) -> Optional[Dict[str, Any]]:
    shown = run_git(["show", f"refs/heads/{MAIN}:docs/architecture/contexts.json"], root, check=False)
    if shown.returncode != 0:
        return None
    try:
        files = json.loads(shown.stdout)["tests"]["files"]
    except (ValueError, KeyError, TypeError):
        return None
    return files if isinstance(files, dict) else None


def land_preview(name: Any, root: Path) -> Dict[str, Any]:
    head = listed_branch(name, root)
    main = _commit(root, f"refs/heads/{MAIN}")
    if main is None:
        raise Refused(f"there is no local branch {MAIN}")
    ref = f"refs/heads/{name}"
    numstat = _git(["diff", "--numstat", "--no-renames", "-z", f"{main}...{ref}"], root).split("\0")
    diffstat = []
    for row in filter(None, numstat):
        added, deleted, path = (row.split("\t", 2) + ["", ""])[:3]
        diffstat.append({"path": path, "added": None if added == "-" else int(added),
                         "deleted": None if deleted == "-" else int(deleted)})
    changed = [p for p in _git(["diff", "--name-only", "--no-renames", "-z", f"{main}...{ref}"],
                               root).split("\0") if p]
    return {"ok": True, "view": "land_preview", "branch": name, "head": head, "main": main,
            "already_in_main": _is_ancestor(root, head, main), **_log(root, [f"{main}..{ref}"]),
            "diffstat": diffstat[:MAX_FILES], "changed": changed[: MAX_FILES * 4],
            "files_truncated": len(diffstat) > MAX_FILES, "test_map": _test_map(root)}


def _common_dir(root: Path) -> Path:
    common = Path(_git(["rev-parse", "--git-common-dir"], root))
    return (common if common.is_absolute() else root / common).resolve()


def has_record(root: Path, commit: str) -> bool:
    if (_common_dir(root) / "fabrique" / "verifications" / f"{commit}.json").is_file():
        return True
    return run_git(["notes", f"--ref={NOTES_REF}", "show", commit], root, check=False).returncode == 0


def push_preview(root: Path) -> Dict[str, Any]:
    _fetch(root)
    main = _commit(root, f"refs/heads/{MAIN}")
    if main is None:
        raise Refused(f"there is no local branch {MAIN}")
    origin = _commit(root, f"refs/remotes/{REMOTE}/{MAIN}")
    fast_forward = origin is None or _is_ancestor(root, origin, main)
    log = _log(root, [f"{origin}..{main}" if origin else main]) if fast_forward else \
        {"commits": [], "truncated": False}
    return {"ok": True, "view": "push_preview", "main": main, "origin_main": origin,
            "fast_forward": fast_forward, "up_to_date": origin == main, **log,
            "has_record": has_record(root, main), "notes": _commit(root, NOTES_REF) is not None,
            "remote_url": remote_url(root, REMOTE)}


def fix_commit(issue: Any, root: Path) -> Dict[str, Any]:
    if not isinstance(issue, int) or isinstance(issue, bool) or not 0 < issue < 10**9:
        raise Refused("issue must be a positive issue number")
    fetched, reason = True, None
    try:
        _fetch(root)
    except GitError as e:
        fetched, reason = False, str(e)[:300]
    origin = _commit(root, f"refs/remotes/{REMOTE}/{MAIN}")
    found = _log(root, [f"refs/heads/{MAIN}"], ["-E", f"--grep=#{issue}([^0-9]|$)"])
    for commit in found["commits"]:
        commit["on_origin"] = bool(origin) and _is_ancestor(root, commit["sha"], origin)
    return {"ok": True, "view": "fix_commit", "issue": issue, "origin_main": origin,
            "fetched": fetched, "fetch_error": reason, **found, "remote_url": remote_url(root, REMOTE)}


def view(data: Dict[str, Any]) -> Dict[str, Any]:
    root = source_repository()
    name = data.get("view")
    if name == "branches":
        return {"ok": True, "view": name, "main": _commit(root, f"refs/heads/{MAIN}"),
                "branches": branches(root)}
    if name == "land_preview":
        return land_preview(data.get("branch"), root)
    if name == "origin":
        return {"ok": True, "view": name, "remote_url": remote_url(root, REMOTE)}
    if name == "push_preview":
        return push_preview(root)
    if name == "fix_commit":
        return fix_commit(data.get("issue"), root)
    raise Refused("view must be branches, land_preview, origin, push_preview or fix_commit")


# -- land ------------------------------------------------------------------------------


def land_env(land_owner: bool, root: Path, environ=os.environ) -> Dict[str, str]:
    """land.sh's environment: no prompt, no ``LAND_*`` of this daemon, ``LAND_OWNER=1``
    only when asked, and a keyring that never waits (the proxy's Poetry install)."""
    env = {k: v for k, v in git_env.env_for(["fetch"], root, environ).items() if not k.startswith("LAND_")}
    env.setdefault(*KEYRING)
    if land_owner is True:
        env["LAND_OWNER"] = "1"
    return env


def _run(argv: List[str], root: Path, env: Dict[str, str], timeout: float) -> tuple[int, str]:
    posix = hasattr(os, "killpg")
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell.
        argv, cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=posix)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if posix:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        out, _ = proc.communicate()
        return -1, (out or "") + f"\nland: stopped by the daemon after {timeout:g} s"
    return proc.returncode, out or ""


def _record(root: Path, commit: str) -> Optional[Dict[str, Any]]:
    path = _common_dir(root) / "fabrique" / "verifications" / f"{commit}.json"
    if not path.is_file() or path.stat().st_size > RECORD_MAX_BYTES:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def land(data: Dict[str, Any], runner=_run) -> Dict[str, Any]:
    root = source_repository()
    name = data.get("branch")
    head = listed_branch(name, root)
    expected = data.get("expected_head")
    if not isinstance(expected, str) or not SHA.match(expected) or expected != head:
        raise Conflict(f"{name} is no longer at the commit the preview showed: preview again")
    main_before = _commit(root, f"refs/heads/{MAIN}")
    timeout = float(os.getenv(TIMEOUT_ENV, "") or LAND_TIMEOUT_SECONDS)
    land_owner = data.get("land_owner") is True
    code, output = runner(["bash", "scripts/land.sh", name], root, land_env(land_owner, root), timeout)
    main_after = _commit(root, f"refs/heads/{MAIN}")
    landed = code == 0 and main_after != main_before
    record = _record(root, main_after) if landed and main_after else None
    return {"ok": True, "branch": name, "head": head, "land_owner": land_owner,
            "exit_code": code, "outcome": EXIT_MEANING.get(code, "stopped"), "landed": landed,
            "main_before": main_before, "main_after": main_after, "record": record,
            "output_tail": output[-OUTPUT_TAIL:]}


# -- push ------------------------------------------------------------------------------


def push(data: Dict[str, Any]) -> Dict[str, Any]:
    root = source_repository()
    expected_main, expected_origin = data.get("main"), data.get("origin_main")
    if not isinstance(expected_main, str) or not SHA.match(expected_main):
        raise Refused("main must be the commit the preview showed")
    if expected_origin is not None and (not isinstance(expected_origin, str) or not SHA.match(expected_origin)):
        raise Refused("origin_main must be the commit the preview showed, or null")
    _fetch(root)
    main = _commit(root, f"refs/heads/{MAIN}")
    origin = _commit(root, f"refs/remotes/{REMOTE}/{MAIN}")
    if main != expected_main:
        raise Conflict(f"{MAIN} moved since the preview: preview again")
    if origin != expected_origin:
        raise Conflict(f"{REMOTE}/{MAIN} moved since the preview: preview again")
    if origin is not None and not _is_ancestor(root, origin, main):
        raise Conflict(f"{REMOTE}/{MAIN} is not an ancestor of {MAIN}: not a fast-forward; "
                       "reconcile by hand (merge, never force)")
    if origin == main:
        raise Conflict(f"{REMOTE}/{MAIN} already is {MAIN}: nothing to push")
    if not has_record(root, main):
        raise Conflict(f"{MAIN} ({main[:12]}) has no verification record; land with scripts/land.sh")
    refspecs = [f"{main}:refs/heads/{MAIN}"]
    notes = _commit(root, NOTES_REF)
    if notes:
        refspecs.append(f"{NOTES_REF}:{NOTES_REF}")
    assert not any(r.startswith("+") for r in refspecs)  # never a forced update
    run_git(["push", "--atomic", "--quiet", REMOTE, *refspecs], root, timeout=PUSH_SECONDS)
    _fetch(root)
    after = _commit(root, f"refs/remotes/{REMOTE}/{MAIN}")
    return {"ok": True, "before": origin, "after": after, "main": main, "pushed": after == main,
            "notes_ref": NOTES_REF if notes else None, "refspecs": refspecs,
            "remote_url": remote_url(root, REMOTE)}


# -- handlers --------------------------------------------------------------------------


def _error(e: Exception) -> Dict[str, Any]:
    if isinstance(e, Conflict):
        return {"error": {"status": 409, "message": str(e)}}
    if isinstance(e, Refused):
        return {"error": {"status": 400, "message": str(e)}}
    if isinstance(e, GitError):
        status = 502 if str(e).startswith(("remote_unreachable", "remote_needs_interaction")) else 409
        return {"error": {"status": status, "message": str(e)[:2000]}}
    return {"error": {"status": 500, "message": f"{type(e).__name__}: {e}"[:2000]}}


def _handler(work):
    async def handle(nc, msg, reply, request_body) -> None:
        data = request_body(msg)
        try:
            body = await asyncio.to_thread(work, data if isinstance(data, dict) else {})
        except (Refused, GitError, OSError) as e:
            body = _error(e)
        await reply(nc, msg, body)

    return handle


HANDLERS = ((GIT_SUBJECT, _handler(view)), (LAND_SUBJECT, _handler(land)),
            (PUSH_SUBJECT, _handler(push)))
