"""Where a La Fabrique branch starts: the company's base as its remote has it.

A task's branch (``git_workspace.deliver``) and a sprint's integration branch
(``sprint_integration.integrate``) start from the company's base branch. The
local copy of that branch may be far behind its upstream (nobody pulled for
weeks), and a branch started there is built on old code: its files are the
old ones, and its pull request rewrites what others changed since.

So before a new branch starts, the base's own branch is fetched from its
remote (``git fetch`` of that one branch, with the person's credentials on
this machine, no shell, no prompt, bounded in time), the way
``git_workspace.publish`` pushes:

- local and remote equal, or the local one only ahead (unpushed work of the
  person's): the branch starts from the local base, as before;
- the remote ahead and the local base an ancestor of it (a fast-forward):
  the branch starts from the remote tip. Nothing of the person's is lost
  (the remote tip holds every local commit) and nothing of theirs moves:
  their checked-out branch, index and files stay as they are;
- both moved (diverged): refused, naming both counts. Neither side can be
  chosen without dropping commits, so a person brings the base up to date;
- no remote (a repository kept only on this machine), a base branch the
  remote does not have (``base_not_on_remote``) or a base that is a commit, a
  tag or a remote ref named explicitly: the local start, as before;
- the fetch fails (offline, credentials refused): the local start, with
  ``remote_unreachable`` and git's reason. A fetch never blocks a delivery.
  Nothing prompts (``git_env``): a remote that wants a passphrase, a password
  or a host key fails at once, with ``remote_needs_interaction: true`` and
  the reason prefixed ``remote_needs_interaction:``.

What was observed goes on the answer: ``base_commit`` (where the branch
started), ``base_ref``, ``base_remote`` and ``base_remote_commit``, and
``base_behind_remote`` (how far the local base was behind) when it was.

**Only when the forge policy allows it** (``forge_gate``, issue #727): with
``none``, the default for a request that names no policy, nothing is fetched
and the branch starts from the local base, as for a repository kept only on
this machine; the answer says ``remote_contacted: false``, ``forge_policy``
and, when the base has an upstream, ``base_remote_not_contacted``.

``workspace.git.base_status`` also says, read locally whatever the policy,
the checkout's ``current_branch``, whether it is ``clean``, any
``operation_in_progress`` and the base remote's ``remote_url`` without
credentials (``git_local.py``, issue #745).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import forge_gate
import git_env
import git_local
from git_workspace import GitError, run_git, valid_branch

# A fetch is one branch; a remote that does not answer in this time is offline.
# It runs inside the backend's ``workspace.generate`` request, which waits
# ``WORKSPACE_WRITE_TIMEOUT_SECONDS`` (30) plus ``git_workflow.FETCH_ALLOWANCE_SECONDS``
# (15) for a delivery with a git spec: keep this well below that budget, so a
# stalled fetch falls back to the local base and the reply still arrives in time
# (a backend that gave up would queue the same delivery again).
FETCH_TIMEOUT_SECONDS = 10
# The settings page asks without a delivery waiting on it: a shorter bound.
STATUS_FETCH_TIMEOUT_SECONDS = 15


def toplevel(folder: Path) -> Optional[Path]:
    """The repository's top folder, or ``None`` for a folder that is not in a repository."""
    result = run_git(["rev-parse", "--show-toplevel"], folder, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    if "not a git repository" in (result.stderr or "").lower():
        return None
    raise GitError((result.stderr or result.stdout).strip() or "git rev-parse failed")


def not_a_repository(workspace_root: str) -> bool:
    """True only for a folder on this machine that git says is not in a repository."""
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        return False
    try:
        return toplevel(folder) is None
    except GitError:
        # git missing or failing for another reason: the delivery reports it.
        return False


def _config(root: Path, key: str) -> str:
    return run_git(["config", "--get", key], root, check=False).stdout.strip()


def _commit(root: Path, ref: str) -> Optional[str]:
    result = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root, check=False)
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def upstream_of(root: Path, branch: str) -> Optional[Tuple[str, str]]:
    """``(remote, branch there)``: the branch's configured upstream, else ``origin``'s (or the one remote's) same name."""
    remote = _config(root, f"branch.{branch}.remote")
    merge = _config(root, f"branch.{branch}.merge")
    if remote and remote != "." and merge.startswith("refs/heads/"):
        candidate = (remote, merge[len("refs/heads/"):])
    else:
        remotes = run_git(["remote"], root, check=False).stdout.split()
        name = "origin" if "origin" in remotes else (remotes[0] if len(remotes) == 1 else "")
        candidate = (name, branch) if name else None
    if candidate is None or candidate[0].startswith("-") or not valid_branch(candidate[1]):
        return None
    return candidate


def _count(root: Path, left: str, right: str) -> int:
    return int(run_git(["rev-list", "--count", f"{left}..{right}"], root).stdout.strip() or 0)


def observe(root: Path, base: str, fetch_timeout: Optional[float] = None,
            policy: str = forge_gate.NONE) -> Dict[str, Any]:
    """The base as it is here and, when *policy* allows a fetch, at its remote.

    ``base_commit`` is the local base; with a remote that answered,
    ``base_remote_commit`` and how many commits each side has that the other
    lacks (``base_behind_remote``, ``base_ahead_of_remote``). Never refuses,
    only observes; ``forge_policy`` and ``remote_contacted`` say what was applied.
    """
    contacted: list = []
    observed = _observe(root, base, fetch_timeout, policy, contacted)
    return {**observed, "forge_policy": policy, "remote_contacted": bool(contacted)}


def _observe(root: Path, base: str, fetch_timeout: Optional[float], policy: str, contacted: list) -> Dict[str, Any]:
    name = base if base and base != "HEAD" else ""
    if not name:
        head = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root, check=False)
        name = head.stdout.strip() if head.returncode == 0 else ""
    if not name or run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], root, check=False).returncode != 0:
        # Detached, or a commit, tag or remote ref named on purpose: started where named.
        commit = _commit(root, base or "HEAD")
        if commit is None:
            raise GitError(f"not a branch or commit in this repository: {base!r}")
        return {"base_ref": base or "HEAD", "base_commit": commit, "base_remote": None}
    local = _commit(root, f"refs/heads/{name}")
    observed: Dict[str, Any] = {"base_ref": name, "base_commit": local, "base_remote": None}
    upstream = upstream_of(root, name)
    if upstream is None:
        return observed  # a repository kept only on this machine
    remote, there = upstream
    if not forge_gate.allows_fetch(policy):
        # The forge policy keeps git local: the base as it is here.
        observed["base_remote_not_contacted"] = f"{remote}/{there}"
        return observed
    tracking = f"refs/remotes/{remote}/{there}"
    observed["base_remote"] = f"{remote}/{there}"
    contacted.append(remote)
    try:
        fetched = run_git(["fetch", "--no-tags", "--quiet", remote, f"+refs/heads/{there}:{tracking}"],
                          root, check=False, timeout=fetch_timeout or FETCH_TIMEOUT_SECONDS)
        reason = None if fetched.returncode == 0 else ((fetched.stderr or fetched.stdout).strip() or "git fetch failed")
    except GitError as e:
        reason = str(e)
    if reason and "couldn't find remote ref" in reason.lower():
        # The remote answered: it has no such branch (a branch kept only here).
        observed.update(base_remote=None, base_not_on_remote=f"{remote}/{there}")
        return observed
    if reason:
        if git_env.needs_interaction(reason):
            # The remote wanted a passphrase, a password or a host key: asked nothing (git_env).
            observed["remote_needs_interaction"] = True
            reason = f"{git_env.NEEDS_INTERACTION}: {reason}"
        observed["remote_unreachable"] = reason[-500:]
        return observed
    remote_tip = _commit(root, tracking)
    if remote_tip is None:
        observed["remote_unreachable"] = f"{remote} has no branch {there}"
        return observed
    observed["base_remote_commit"] = remote_tip
    observed["base_behind_remote"] = _count(root, local, remote_tip)
    observed["base_ahead_of_remote"] = _count(root, remote_tip, local)
    return observed


def start_point(root: Path, base: str, policy: str = forge_gate.NONE) -> Tuple[str, Dict[str, Any]]:
    """The commit a new branch starts from, and the observation to keep with it.

    The remote tip on a fast-forward, the local base otherwise; diverged is a
    ``GitError`` naming both counts (see the module's docstring). The remote
    is read only when *policy* allows a fetch.
    """
    observed = observe(root, base, policy=policy)
    behind = observed.get("base_behind_remote") or 0
    ahead = observed.get("base_ahead_of_remote") or 0
    if behind and ahead:
        raise GitError(
            f"the local {observed['base_ref']} and {observed['base_remote']} have diverged "
            f"({ahead} commit(s) only here, {behind} only there): bring {observed['base_ref']} "
            "up to date before La Fabrique starts a branch from it"
        )
    if behind:
        observed["base_local_commit"] = observed["base_commit"]
        observed["base_commit"] = observed["base_remote_commit"]
        observed["started_from"] = observed["base_remote"]
    else:
        observed["started_from"] = observed["base_ref"]
        observed.pop("base_behind_remote", None)
    return observed["base_commit"], observed


def behind_remote_at_publish(root: Path, base: str, commit: str, policy: str = forge_gate.NONE) -> int:
    """How many commits of ``origin/<base>`` *commit* lacks, fetched now; unreadable is a ``GitError``."""
    if not forge_gate.allows_fetch(policy):
        raise GitError(forge_gate.refusal_message(policy, f"fetch origin/{base}"))
    tracking = f"refs/remotes/origin/{base}"
    fetched = run_git(["fetch", "--no-tags", "--quiet", "origin", f"+refs/heads/{base}:{tracking}"],
                      root, check=False, timeout=FETCH_TIMEOUT_SECONDS)
    if fetched.returncode != 0 or _commit(root, tracking) is None:
        said = (fetched.stderr or fetched.stdout).strip() or "no such branch"
        named = f"{git_env.NEEDS_INTERACTION}: " if git_env.needs_interaction(said) else ""
        raise GitError(f"could not read origin/{base} to check the branch is current: {named}{said}")
    return _count(root, commit, tracking)


def status(workspace_root: str, base: str, policy: str = forge_gate.NONE) -> Dict[str, Any]:
    """``workspace.git.base_status``: whether the folder is a repository, and its base against the remote."""
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        raise GitError(f"the project folder does not exist on this machine: {workspace_root}")
    root = toplevel(folder)
    if root is None:
        return {"is_repo": False}
    if base and not valid_branch(base):
        raise GitError(f"not a branch name: {base!r}")
    observed = observe(root, base, STATUS_FETCH_TIMEOUT_SECONDS, policy)
    # How the person's checkout stands, read locally whatever the policy
    # (issue #745): what the New company form shows before anything moves.
    upstream = upstream_of(root, observed["base_ref"]) if observed.get("base_ref") != "HEAD" else None
    remote = git_local.remote_url(root, upstream[0]) if upstream else None
    return {"is_repo": True, "repository": str(root), **observed, **git_local.state(folder), "remote_url": remote}


async def handle_status(nc, msg, reply, request_body) -> None:
    """``workspace.git.base_status`` → the observation, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(status, str(data.get("workspace_root") or ""), str(data.get("base") or ""),
                                       forge_gate.of(data))
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
