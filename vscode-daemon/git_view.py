"""Read-only views of a task's repository for the agents' git tool (issue #766).

The agents had no read-only git: Developers asked ``workspace__run_tests``
for ``git status`` or ``git diff`` (and got the test suite instead), and QA,
told by the Architect's brief to run ``git diff --name-only``, wrote that
without it it could not know which files to verify. ``workspace__git``
(``backend/app/services/tools/workspace_git.py``) asks for one of these fixed
views, carried by ``workspace.git.diff`` with a ``view`` field (PROTOCOL.md
§5.6, announced as the ``view`` feature of that subject):

- ``status``: ``git status --porcelain=v1`` of the project folder, bounded;
- ``changed``: the paths the working tree changes against the merge base of
  the task's base branch and ``HEAD``, untracked files included: what this
  task changed, committed or not (the branches of the tasks it depends on,
  merged into its worktree, count as its base's side only once merged there);
- ``diff``: one path's diff against that merge base, bounded;
- ``log``: the last ``limit`` (at most 20) commits of ``HEAD``, one line each;
- ``blame``: who-less blame of one path's line range (at most 200 lines):
  for each group of consecutive lines last changed by the same commit, the
  commit, its author date and its subject. Authors' and committers' names and
  e-mail addresses are never returned (the porcelain carries them; they are
  dropped while parsing): the repositories may be public and the date and
  subject say when and why. Announced as its own ``blame`` feature.

The base is the request's ``base`` (a local branch), else the default branch
``git_read_at.default_branch`` names (origin's HEAD, ``main``, ``master``).
A path is relative to ``workspace_root``, resolved inside it (symlinks
followed), never VCS metadata or a file that may hold secrets
(``workspace_read.is_sensitive``). Every git command is an argument list run
without a shell, with no prompt (``git_env``), bounded in time; nothing is
written, no remote is contacted, whatever the forge policy.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import git_base
import git_env
import git_read_at
from git_workspace import GIT_TIMEOUT_SECONDS, GitError, valid_branch
from workspace_read import is_sensitive

VIEWS = ("status", "changed", "diff", "log", "blame")
MAX_ENTRIES = 300
MAX_DIFF_CHARS = 60_000
MAX_LOG = 20
DEFAULT_LOG = 10
MAX_SUBJECT_CHARS = 200
MAX_BLAME_LINES = 200
DEFAULT_BLAME_LINES = 100
UNCOMMITTED = "0" * 40
_CHANGE = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}


def _git(args: List[str], cwd: Path, check: bool = True) -> Tuple[int, str]:
    """``git <args>`` in *cwd*: an argument list, no shell, no prompt; text decoded leniently."""
    env = git_env.env_for(args, cwd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", *args], cwd=str(cwd), env=env, capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed on this machine") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took more than {GIT_TIMEOUT_SECONDS:g} s") from e
    if check and result.returncode != 0:
        raise GitError(result.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return result.returncode, result.stdout.decode("utf-8", "replace")


def _folder(workspace_root: str) -> Path:
    folder = Path(workspace_root).expanduser() if workspace_root else None
    if folder is None or not folder.is_absolute():
        raise ValueError("workspace_root is an absolute path")
    if not folder.is_dir():
        raise FileNotFoundError(f"the project folder does not exist on this machine: {workspace_root}")
    if git_base.toplevel(folder) is None:
        raise GitError(f"{workspace_root} is not a git repository")
    return folder.resolve()


def relative_path(folder: Path, value: Any) -> str:
    """*value* as a path inside *folder*, or ValueError: never outside, VCS metadata or a secret."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("a path inside the project is required")
    text = value.strip().replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or not parts or ".." in parts or (len(text) > 1 and text[1] == ":"):
        raise ValueError(f"not a path inside the project: {value}")
    target = folder.joinpath(*parts)
    try:
        target.resolve().relative_to(folder)
    except (OSError, ValueError) as e:
        raise ValueError(f"not a path inside the project: {value}") from e
    if is_sensitive(folder, target):
        raise ValueError(f"VCS metadata or a file that may hold secrets is never shown: {value}")
    return "/".join(parts)


def _base(folder: Path, base: str) -> Tuple[str, str]:
    """``(base branch, merge base of it and HEAD)``."""
    if base and not valid_branch(base):
        raise ValueError(f"not a branch name: {base!r}")
    name = git_read_at.default_branch(folder, base)
    if name is None:
        raise GitError(f"no base branch to compare with: {base!r} is not a local branch" if base
                       else "no base branch to compare with (no origin/HEAD, main or master)")
    code, out = _git(["merge-base", f"refs/heads/{name}", "HEAD"], folder, check=False)
    if code != 0 or not out.strip():
        raise GitError(f"HEAD has no common commit with {name}")
    return name, out.strip()


def _branch(folder: Path) -> Optional[str]:
    code, out = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], folder, check=False)
    return (out.strip() or None) if code == 0 else None


def _records(out: str) -> List[str]:
    records = out.split("\0")
    return records[:-1] if records and records[-1] == "" else records


def status(folder: Path) -> Dict[str, Any]:
    _, prefix = _git(["rev-parse", "--show-prefix"], folder)
    prefix = prefix.strip()
    _, out = _git(["status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", "."], folder)
    entries: List[Dict[str, Any]] = []
    records = iter(_records(out))
    for record in records:
        code, path = record[:2], record[3:]
        entry: Dict[str, Any] = {"code": code, "path": path[len(prefix):] if path.startswith(prefix) else path}
        if code[0] in "RC":  # the origin follows as its own record
            origin = next(records, "")
            entry["from"] = origin[len(prefix):] if origin.startswith(prefix) else origin
        entries.append(entry)
    return {"branch": _branch(folder), "entries": entries[:MAX_ENTRIES],
            "count": len(entries), "truncated": len(entries) > MAX_ENTRIES}


def changed(folder: Path, base: str) -> Dict[str, Any]:
    name, merge_base = _base(folder, base)
    _, out = _git(["diff", "--no-color", "--no-ext-diff", "--no-renames", "--relative",
                   "--name-status", "-z", merge_base], folder)
    tokens = _records(out)
    files = [{"path": tokens[i + 1], "change": _CHANGE.get(tokens[i][:1], "modified")}
             for i in range(0, len(tokens) - 1, 2)]
    _, others = _git(["ls-files", "--others", "--exclude-standard", "-z"], folder)
    files += [{"path": p, "change": "untracked"} for p in _records(others)]
    files.sort(key=lambda f: f["path"])
    return {"base": name, "merge_base": merge_base, "branch": _branch(folder),
            "files": files[:MAX_ENTRIES], "count": len(files), "truncated": len(files) > MAX_ENTRIES}


def diff(folder: Path, base: str, path: Any) -> Dict[str, Any]:
    relative = relative_path(folder, path)
    name, merge_base = _base(folder, base)
    _, text = _git(["-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
                    "--relative", merge_base, "--", relative], folder)
    _, others = _git(["ls-files", "--others", "--exclude-standard", "-z", "--", relative], folder)
    truncated = len(text) > MAX_DIFF_CHARS
    return {"base": name, "merge_base": merge_base, "path": relative,
            "patch": text[:MAX_DIFF_CHARS], "truncated": truncated,
            "untracked": relative in _records(others)}


def log(folder: Path, limit: Any) -> Dict[str, Any]:
    count = limit if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_LOG
    count = max(1, min(count, MAX_LOG))
    code, out = _git(["log", "--no-color", "--no-decorate", "--format=%H%x09%s", "-n", str(count), "HEAD"],
                     folder, check=False)
    commits = []
    for line in out.splitlines() if code == 0 else []:
        sha, _, subject = line.partition("\t")
        commits.append({"commit": sha, "subject": subject[:MAX_SUBJECT_CHARS]})
    return {"branch": _branch(folder), "commits": commits, "limit": count}


def _line_number(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _blame_groups(out: str) -> List[Dict[str, Any]]:
    """``git blame --porcelain`` as line groups: commit, date, subject; never a name or e-mail."""
    seen: Dict[str, Dict[str, Any]] = {}
    groups: List[Dict[str, Any]] = []
    current: Optional[str] = None
    line_no = 0
    for raw in out.split("\n"):
        if raw.startswith("\t"):  # The line's content: not returned.
            if current is None:
                continue
            last = groups[-1] if groups else None
            if last and last["commit"] == current and last["end_line"] == line_no - 1:
                last["end_line"] = line_no
            else:
                groups.append({"commit": current, "start_line": line_no, "end_line": line_no})
            continue
        head = raw.split(" ")
        if len(head) >= 3 and len(head[0]) == 40 and all(c in "0123456789abcdef" for c in head[0]):
            current, line_no = head[0], int(head[2])
            seen.setdefault(current, {})
        elif current is not None and raw.startswith("author-time "):
            seen[current]["time"] = raw[len("author-time "):].strip()
        elif current is not None and raw.startswith("summary "):
            seen[current]["summary"] = raw[len("summary "):][:MAX_SUBJECT_CHARS]
        # author, author-mail, committer, committer-mail…: dropped on purpose.
    for group in groups:
        info = seen.get(group["commit"], {})
        stamp = info.get("time", "")
        group["date"] = (
            datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if stamp.isdigit() else None
        )
        group["uncommitted"] = group["commit"] == UNCOMMITTED
        group["subject"] = "" if group["uncommitted"] else info.get("summary", "")
    return groups


def blame(folder: Path, path: Any, start_line: Any, end_line: Any) -> Dict[str, Any]:
    """One path's line range, at most ``MAX_BLAME_LINES``, as line groups (``_blame_groups``)."""
    relative = relative_path(folder, path)
    target = folder.joinpath(*relative.split("/"))
    if not target.is_file():
        raise FileNotFoundError(f"no such file: {relative}")
    with target.open("rb") as handle:
        total = sum(1 for _ in handle)
    start = max(1, _line_number(start_line, 1))
    if start > max(total, 1):
        raise ValueError(f"start_line {start} is past the end of {relative}, which has {total} lines")
    asked = _line_number(end_line, start + DEFAULT_BLAME_LINES - 1)
    end = max(start, min(asked, start + MAX_BLAME_LINES - 1, total))
    _, out = _git(["-c", "core.quotePath=false", "blame", "--porcelain", "-L", f"{start},{end}",
                   "--", relative], folder)
    return {"path": relative, "start_line": start, "end_line": end, "file_lines": total,
            "groups": _blame_groups(out), "truncated": min(asked, total) > end}


def view(workspace_root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The answer to a ``view`` request (module docstring); ValueError (400), FileNotFoundError (404), GitError (409)."""
    which = spec.get("view")
    if which not in VIEWS:
        raise ValueError(f"view is one of {', '.join(VIEWS)}")
    folder = _folder(workspace_root)
    base = str(spec.get("base") or "")
    if which == "status":
        body = status(folder)
    elif which == "changed":
        body = changed(folder, base)
    elif which == "diff":
        body = diff(folder, base, spec.get("path"))
    elif which == "blame":
        body = blame(folder, spec.get("path"), spec.get("start_line"), spec.get("end_line"))
    else:
        body = log(folder, spec.get("limit"))
    return {"ok": True, "view": which, **body}


async def handle(nc, msg, reply, data: Dict[str, Any]) -> None:
    """``workspace.git.diff`` with ``view`` → that view, or ``{"error": {status, message}}``."""
    try:
        body = await asyncio.to_thread(view, str(data.get("workspace_root") or ""), data)
    except ValueError as e:
        body = {"error": {"status": 400, "message": str(e)}}
    except FileNotFoundError as e:
        body = {"error": {"status": 404, "message": str(e)}}
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
