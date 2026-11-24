"""The project's Git, read and written the way GitHub and GitLab work.

Reading: ``git_info`` tells the branch and the origin of a repository from
its git files, for the folder picker and stack detection.

Writing: with the company's Git workflow on, a delivery never lands in the
person's checkout. Each task has a branch, ``fabrique/task-<id>``, started
from the base the company named (or the commit the repository has checked
out) at its first delivery, from the remote's tip when the local base is
behind it (``git_base``); each delivery is one commit on it, written in a
worktree under the repository's git directory
(``<git dir>/fabrique-worktrees/``), so the person's working tree, index and
checked-out branch are never touched. The answer is what git itself says:
the branch, the commit and its parent. A branch is reviewed like a pull
request, and merged by a person or by the platform it is pushed to.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit

import forge_gate
import git_env

GIT_TIMEOUT_SECONDS = 60
WORKTREES_DIR = "fabrique-worktrees"
# A branch name La Fabrique writes: letters, digits, '/', '-', '_' and '.'.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}$")
COMMITTER = ("La Fabrique", "fabrique@localhost")


class GitError(Exception):
    """A git command that failed, with git's own words."""


class ForgeDisabled(GitError):
    """A remote operation the request's forge policy does not allow (``forge_gate``)."""

    code = forge_gate.FORGE_DISABLED


def error_body(e: Exception, status: int = 409) -> Dict[str, Any]:
    """The ``{"error": ...}`` answer for *e*, with its ``code`` when it has one."""
    code = getattr(e, "code", None)
    return {"error": {"status": status, "message": str(e), **({"code": code} if code else {})}}


def run_git(
    args: list[str], cwd: Path, check: bool = True, timeout: float = GIT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess:
    """``git <args>`` in *cwd*: no shell, no prompt (``git_env``), C locale, bounded in time."""
    env = git_env.env_for(args, cwd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed on this machine") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took more than {timeout:g} s") from e
    if check and result.returncode != 0:
        raise GitError(
            (result.stderr or result.stdout).strip() or f"git {args[0]} failed"
        )
    return result


def _out(args: list[str], cwd: Path) -> str:
    return run_git(args, cwd).stdout.strip()


# Only the branches La Fabrique writes are pushed from here.
TASK_BRANCH_PREFIX = "fabrique/"


def valid_branch(name: str) -> bool:
    return (
        bool(_BRANCH.match(name))
        and ".." not in name
        and not name.endswith((".lock", "/", "."))
    )


def has_branch(root: Path, branch: str) -> bool:
    return run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], root, check=False).returncode == 0


def fresh_start(root: Path, branch: str, base: str, policy: str = forge_gate.NONE) -> tuple:
    """``(start point, observation)``: the base checked against its remote, when
    *branch* is new and the forge *policy* allows a fetch (``git_base``)."""
    if has_branch(root, branch):
        # Started at an earlier delivery, whose evidence says from where.
        return base, {"forge_policy": policy, "remote_contacted": False}
    import git_base  # git_base reads git through this module

    return git_base.start_point(root, base, policy)


def admin_dir(worktree: Path) -> Path:
    """A La Fabrique worktree's own git dir, found from the common dir (never from ``.git``).

    A task worktree is ``<common>/fabrique-worktrees/<name>`` and git records
    it in ``<common>/worktrees/<id>/gitdir``: both outside what a sandbox
    mounts."""
    if worktree.parent.name != WORKTREES_DIR:
        raise GitError("not a task worktree La Fabrique made")
    common = worktree.parent.parent
    real = worktree.resolve()
    admins = common / "worktrees"
    for admin in sorted(admins.iterdir()) if admins.is_dir() else []:
        try:
            recorded = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            continue
        if recorded.name == ".git" and recorded.parent.resolve() == real:
            return admin
    raise GitError("not a task worktree La Fabrique made")


def pointer_problem(worktree: Path, admin: Path) -> Optional[str]:
    """Why the worktree's ``.git`` is not git's pointer to *admin*, else ``None``."""
    dotgit = worktree / ".git"
    try:
        if not stat.S_ISREG(os.lstat(dotgit).st_mode):
            return "the worktree's .git is no longer the file La Fabrique wrote"
        text = dotgit.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "the worktree's .git cannot be read"
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        return "the worktree's .git was rewritten"
    named = Path(lines[0][len("gitdir: "):])
    named = named if named.is_absolute() else worktree / named
    if os.path.realpath(named) != os.path.realpath(admin):
        return "the worktree's .git points at another git dir"
    return None


def restore_pointer(worktree: Path, admin: Path) -> None:
    """The worktree's ``.git`` written back as git writes it: plain file operations, no git.

    A folder there (a console producer's private repository) is removed
    without following a symlink; a symlink is removed, never its target."""
    dotgit = worktree / ".git"
    if dotgit.is_dir() and not dotgit.is_symlink():
        shutil.rmtree(dotgit)
    elif os.path.lexists(dotgit):
        dotgit.unlink()
    dotgit.write_text(f"gitdir: {admin}\n", encoding="utf-8")


def repair_pointer(worktree: Path) -> Optional[str]:
    """Put a La Fabrique worktree's ``.git`` back to git's own pointer before
    any git runs there (a console producer's private repository left by a run
    never collected, or a rewritten pointer): why it was needed, else ``None``."""
    try:
        admin = admin_dir(worktree)
    except (OSError, GitError):
        return None
    problem = pointer_problem(worktree, admin)
    if problem:
        restore_pointer(worktree, admin)
    return problem


def worktree_for(root: Path, branch: str, start_point: str) -> Path:
    """The worktree where *branch* is checked out, made (and the branch with it) when missing."""
    common = Path(_out(["rev-parse", "--git-common-dir"], root))
    common = common if common.is_absolute() else (root / common).resolve()
    path = common / WORKTREES_DIR / branch.replace("/", "__")
    # Before git looks at it (``prune`` included): its .git is git's pointer.
    if os.path.lexists(path / ".git"):
        # A command still running there (a console producer's CLI a restarted
        # backend no longer waits for) keeps its repository: never repaired
        # under it, the attempt is refused until that command ends.
        import sprint_integration  # noqa: PLC0415 - it imports this module

        if sprint_integration.is_active(path) and pointer_problem(path, admin_dir(path)):
            raise GitError(f"a command is still running in {path}; try again once it ends")
        repair_pointer(path)
    run_git(["worktree", "prune"], root, check=False)
    if (path / ".git").exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if has_branch(root, branch):
        run_git(["worktree", "add", str(path), branch], root)
    else:
        start = _out(["rev-parse", "--verify", f"{start_point}^{{commit}}"], root)
        run_git(["worktree", "add", "-b", branch, str(path), start], root)
    return path


def deliver(
    workspace_root: str,
    spec: Dict[str, Any],
    files: list,
    write_files: Callable[[str, list], list],
) -> Dict[str, Any]:
    """Write *files* on the task's branch, in its worktree, and commit them.

    *spec*: ``branch`` (the task's), ``base`` (where it starts: a branch or a
    commit; empty for what the repository has checked out), ``message`` and
    ``merge`` (branches of the tasks this one depends on, merged into the
    worktree first so the task builds on their files; one that does not exist
    or is already in is skipped, one that conflicts is an error).
    """
    branch = str(spec.get("branch") or "")
    if not valid_branch(branch):
        raise GitError(f"not a branch name La Fabrique writes: {branch!r}")
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        return {
            "ok": False,
            "workspace_root": workspace_root,
            "files": [
                {"path": f.get("path", ""), "status": "workspace_missing"}
                for f in files
            ],
        }
    root = Path(_out(["rev-parse", "--show-toplevel"], folder))
    base = str(spec.get("base") or "HEAD")
    start, started = fresh_start(root, branch, base, forge_gate.of(spec))
    worktree = worktree_for(root, branch, start)
    merged = merge_branches(root, worktree, [str(b) for b in spec.get("merge") or []])
    parent = _out(["rev-parse", "HEAD"], worktree)
    results = write_files(str(worktree), files)
    written = [r["path"] for r in results if r.get("status") == "written"]
    deleted = [r["path"] for r in results if r.get("status") == "deleted"]
    commit = None
    if written or deleted:
        if written:
            run_git(["add", "--", *written], worktree)
        if deleted:
            # ``git add`` of a path Git never tracked fails; ``rm --cached
            # --ignore-unmatch`` records a tracked file's removal, skips the rest.
            run_git(["rm", "-q", "--cached", "--ignore-unmatch", "--", *deleted], worktree)
        if (
            run_git(["diff", "--cached", "--quiet"], worktree, check=False).returncode
            != 0
        ):
            name, email = COMMITTER
            message = str(spec.get("message") or f"La Fabrique: {branch}")
            run_git(
                [
                    "-c",
                    f"user.name={name}",
                    "-c",
                    f"user.email={email}",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--no-verify",
                    "-m",
                    message,
                ],
                worktree,
            )
            commit = _out(["rev-parse", "HEAD"], worktree)
    return {
        "ok": all(r.get("status") in ("written", "deleted") for r in results),
        "workspace_root": str(worktree),
        "files": results,
        "git": {
            "repository": str(root),
            "branch": branch,
            "base": base,
            "worktree": str(worktree),
            "parent": parent,
            # None: nothing changed, so there is no new commit.
            "commit": commit,
            # The prerequisites' branches merged into the worktree this time.
            "merged": merged,
            # Where a new branch started, checked against the base's remote
            # (``base_commit``, ``base_behind_remote``, ``remote_unreachable``).
            **started,
        },
    }


def merge_state(root: Path, worktree: Path, ref: str) -> str:
    """``skip`` (not La Fabrique's), ``missing``, ``already`` (in HEAD) or ``todo``."""
    if not valid_branch(ref) or not ref.startswith(TASK_BRANCH_PREFIX):
        return "skip"
    if run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{ref}"], root, check=False).returncode != 0:
        return "missing"
    if run_git(["merge-base", "--is-ancestor", ref, "HEAD"], worktree, check=False).returncode == 0:
        return "already"
    return "todo"


def merge_one(worktree: Path, ref: str, message: str) -> subprocess.CompletedProcess:
    """``git merge --no-ff`` of *ref* as La Fabrique; a conflict is left for the caller to read and abort."""
    name, email = COMMITTER
    return run_git(
        ["-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false",
         "merge", "--no-ff", "--no-edit", "--no-verify", "-m", message, ref],
        worktree,
        check=False,
    )


def merge_branches(root: Path, worktree: Path, branches: list) -> list:
    """Merge each of *branches* (La Fabrique's own) into *worktree*; the ones merged now."""
    merged = []
    for ref in branches:
        if merge_state(root, worktree, ref) != "todo":
            continue
        result = merge_one(worktree, ref, f"Merge {ref} (a task this one depends on)")
        if result.returncode != 0:
            run_git(["merge", "--abort"], worktree, check=False)
            raise GitError(f"could not merge {ref} into the task's worktree: {(result.stderr or result.stdout).strip()}")
        merged.append(ref)
    return merged



def default_branch(root: Path) -> str:
    """The remote's default branch (``origin/HEAD``), else the branch checked out here."""
    ref = run_git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root, check=False)
    if ref.returncode == 0 and ref.stdout.strip().startswith("origin/"):
        return ref.stdout.strip()[len("origin/"):]
    return _out(["rev-parse", "--abbrev-ref", "HEAD"], root)


def compare_url(provider: Optional[str], web_url: Optional[str], base: str, branch: str) -> Optional[str]:
    """The page where a person opens the pull request (or merge request) by hand."""
    if not web_url:
        return None
    b, h = quote(base, safe=""), quote(branch, safe="")
    if provider == "github":
        return f"{web_url}/compare/{b}...{h}?expand=1"
    if provider == "gitlab":
        return f"{web_url}/-/merge_requests/new?merge_request%5Bsource_branch%5D={h}&merge_request%5Btarget_branch%5D={b}"
    if provider == "bitbucket":
        return f"{web_url}/pull-requests/new?source={h}&dest={b}"
    return None


# What a request's page looks like, so a diagnostic or help link is never taken for one.
REQUEST_URL = {"github": r"https://\S+/pull/\d+", "gitlab": r"https://\S+/-/merge_requests/\d+"}
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _cli(args: list[str], cwd: Path, body: str = "") -> Optional[str]:
    """Run ``gh`` or ``glab`` as the person is logged in: its output when it succeeded, else ``None``."""
    if shutil.which(args[0]) is None:
        return None
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            args, cwd=str(cwd), env=env, input=body, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _request_url(output: Optional[str], provider: str) -> Optional[str]:
    found = re.search(REQUEST_URL[provider], output or "")
    return found.group(0) if found else None


def _open_request(provider: str, root: Path, branch: str, base: str, title: str, body: str) -> tuple:
    """``(url, updated)``: the open request's description brought up to date, else a new draft."""
    if provider == "github":
        existing = _request_url(_cli(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url",
                                      "--jq", ".[0].url"], root), provider)
        if existing:
            done = _cli(["gh", "pr", "edit", existing, "--title", title, "--body-file", "-"], root, body)
            return (existing, True) if done is not None else (None, False)
        created = _cli(["gh", "pr", "create", "--draft", "--head", branch, "--base", base, "--title", title,
                        "--body-file", "-"], root, body)
        return _request_url(created, provider), False
    # glab updates the merge request of a branch by its name, and fails when there is none.
    updated = _request_url(_cli(["glab", "mr", "update", branch, "--title", title, "--description", body], root), provider)
    if updated:
        return updated, True
    created = _cli(["glab", "mr", "create", "--draft", "--yes", "--source-branch", branch, "--target-branch", base,
                    "--title", title, "--description", body], root)
    return _request_url(created, provider), False


def publish(workspace_root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Push the task's branch to ``origin`` and open its pull request or merge request.

    The push uses the person's own Git credentials on this machine. *spec*'s
    ``commit`` pins what is pushed: the commit the run's evidence covers; a
    branch that has moved past it (a later delivery) is refused rather than
    published under this run's evidence. With ``gh`` (GitHub) or ``glab``
    (GitLab) installed and logged in, the open request's description is
    updated, or a draft is opened, with *spec*'s ``title`` and ``body``;
    without them, the answer is the page where the person opens it by hand.

    Refused, before anything runs, unless the forge policy is ``publish``
    (``forge_gate``; ``ForgeDisabled``).
    """
    policy = forge_gate.of(spec)
    if not forge_gate.allows_publish(policy):
        raise ForgeDisabled(forge_gate.refusal_message(policy, "push or open a request"))
    branch = str(spec.get("branch") or "")
    if not (valid_branch(branch) and branch.startswith(TASK_BRANCH_PREFIX)):
        raise GitError(f"not a branch name La Fabrique writes: {branch!r}")
    root = Path(_out(["rev-parse", "--show-toplevel"], Path(workspace_root).expanduser()))
    remote_url = run_git(["config", "--get", "remote.origin.url"], root, check=False).stdout.strip()
    if not remote_url:
        raise GitError("the repository has no origin to push to")
    base = str(spec.get("base") or "") or default_branch(root)
    if not valid_branch(base):
        raise GitError(f"not a branch to open a request against: {base!r}")
    tip = _out(["rev-parse", f"refs/heads/{branch}"], root)
    pinned = str(spec.get("commit") or "")
    if pinned and not _SHA.match(pinned):
        raise GitError(f"not a commit: {pinned!r}")
    if pinned and pinned != tip:
        raise GitError(f"{branch} has moved on to {tip[:7]} since this run's commit {pinned[:7]}: publish the task's latest run")
    behind = None
    if spec.get("require_current_base"):
        import git_base

        behind = git_base.behind_remote_at_publish(root, base, tip, policy)
        if behind:
            raise GitError(f"{branch} is {behind} commit(s) behind origin/{base}: integrate the sprint again, "
                           f"which merges origin/{base} into it first")
    run_git(["push", "--set-upstream", "origin", f"{tip}:refs/heads/{branch}"], root)
    provider, web_url = remote_web_url(_strip_credentials(remote_url))
    title = str(spec.get("title") or branch)[:250]
    body = str(spec.get("body") or "")
    url, updated = (None, False)
    if provider in REQUEST_URL:
        url, updated = _open_request(provider, root, branch, base, title, body)
    return {
        "ok": True,
        "provider": provider,
        "branch": branch,
        "base": base,
        "commit": tip,
        "url": url or compare_url(provider, web_url, base, branch),
        "how": ("gh" if provider == "github" else "glab") if url else "compare",
        "updated": updated,
        "forge_policy": policy,
        "remote_contacted": True,
        # Checked against origin/<base> just before the push, when asked.
        **({"base_behind_remote": behind} if behind is not None else {}),
    }


async def handle_publish(nc, msg, reply, request_body) -> None:
    """``workspace.git.publish`` → the published branch, or ``{"error": {status, message}}``."""
    import asyncio

    data = request_body(msg)
    try:
        body = await asyncio.to_thread(publish, str(data.get("workspace_root") or ""), data)
    except GitError as e:
        body = error_body(e)
    await reply(nc, msg, body)


def _git_dir(root: Path) -> Optional[Path]:
    """The repository's git directory: ``.git`` itself, or where a ``.git`` file points."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            line = dot_git.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            git_dir = Path(line[len("gitdir:") :].strip())
            return git_dir if git_dir.is_absolute() else (root / git_dir).resolve()
    return None


def _strip_credentials(url: str) -> str:
    """Drop a password or token embedded in a remote URL (``https://TOKEN@host/...``)."""
    if "://" not in url:
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    userinfo, host_port = parts.netloc.rsplit("@", 1)
    if ":" in userinfo or parts.scheme in {"http", "https"}:
        return parts._replace(netloc=host_port).geturl()
    # ssh://git@host/... names the login user, not a secret.
    return url


def remote_web_url(remote_url: Optional[str]) -> tuple:
    """``(provider, web_url)`` for a git remote: github, gitlab, bitbucket or other."""
    if not remote_url:
        return None, None
    url = remote_url.strip()
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    elif ":" in url:
        # scp-like syntax: git@github.com:owner/repo.git
        host, path = url.split(":", 1)
        host = host.split("@")[-1]
    else:
        return "other", None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    host = host.lower()
    if host == "github.com" or host.endswith(".github.com"):
        provider = "github"
    elif "gitlab" in host:
        provider = "gitlab"
    elif "bitbucket" in host:
        provider = "bitbucket"
    else:
        provider = "other"
    return provider, (f"https://{host}/{path}" if host and path else None)


def git_info(root: Path) -> Dict[str, Any]:
    """Branch and origin of the repository at *root*, read from its git files."""
    info: Dict[str, Any] = {
        "is_repo": False,
        "branch": None,
        "remote_url": None,
        "provider": None,
        "web_url": None,
    }
    git_dir = _git_dir(root)
    if git_dir is None:
        return info
    info["is_repo"] = True
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="ignore").strip()
        if head.startswith("ref: refs/heads/"):
            info["branch"] = head[len("ref: refs/heads/") :]
    except OSError:
        pass
    # Linked worktrees keep the shared config in the main repository.
    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            common = Path(commondir_file.read_text(encoding="utf-8").strip())
            common_dir = (
                common if common.is_absolute() else (git_dir / common).resolve()
            )
        except OSError:
            pass
    config = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        config.read(common_dir / "config", encoding="utf-8")
    except (configparser.Error, OSError):
        return info
    remotes = [
        section for section in config.sections() if section.startswith('remote "')
    ]
    preferred = (
        'remote "origin"'
        if 'remote "origin"' in remotes
        else (remotes[0] if remotes else None)
    )
    if preferred and config.has_option(preferred, "url"):
        remote = _strip_credentials(config.get(preferred, "url"))
        info["remote_url"] = remote
        info["provider"], info["web_url"] = remote_web_url(remote)
    return info
