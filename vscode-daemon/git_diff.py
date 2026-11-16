"""The diff a sprint's integration brings into its base, read with git (``workspace.git.diff``).

A person reviews a sprint's integration before it is merged
(``backend/app/services/release/integration_review.py``): they read this diff
and leave comments on its lines, and the backend anchors each comment to it.
So the diff is observed here, on the machine that holds the repository, and
never taken from the browser: the files between the merge base of ``base``
and the pinned ``commit``, their hunks, and the sha256 of each file's bytes
on both sides, read from git's own objects (``git cat-file --batch``).

Beside them, ``paths`` lists every path the diff touches from
``git diff --name-status -z --no-renames``: NUL-separated and never quoted, so
a name git quotes in a patch header (a tab, a quote, a backslash), which the
patch parser does not read, is still listed, and a rename lists its source
and its destination. The backend matches protected paths against it.

Read-only: no worktree, no checkout, no ref is written.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from git_workspace import _SHA, GIT_TIMEOUT_SECONDS, TASK_BRANCH_PREFIX, GitError, _out, run_git, valid_branch

MAX_FILES = 300
MAX_PATCH_CHARS = 200_000
MAX_PATHS = 5000
_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def _git_bytes(args: List[str], cwd: Path, data: bytes = b"") -> bytes:
    """``git <args>`` in *cwd*, bytes in and out: a file's bytes are hashed as they are."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", *args], cwd=str(cwd), env=env, input=data, capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed on this machine") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took more than {GIT_TIMEOUT_SECONDS:g} s") from e
    if result.returncode != 0:
        raise GitError(result.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return result.stdout


def _section(old_path: str, new_path: str, lines: List[str]) -> Dict[str, Any]:
    change, omitted, start = "modified", None, None
    for index, line in enumerate(lines):
        if line.startswith("@@"):
            start = index
            break
        if line.startswith("new file mode"):
            change = "added"
        elif line.startswith("deleted file mode"):
            change = "deleted"
        elif line.startswith("rename from "):
            change, old_path = "renamed", line[len("rename from "):]
        elif line.startswith("rename to "):
            new_path = line[len("rename to "):]
        elif line.startswith(("Binary files ", "GIT binary patch")):
            omitted = "binary"
    patch_lines = lines[start:] if start is not None else []
    while patch_lines and patch_lines[-1] == "":
        patch_lines.pop()
    patch = "\n".join(patch_lines)
    if len(patch) > MAX_PATCH_CHARS:
        patch, omitted = "", "too_large"
    return {
        "path": old_path if change == "deleted" else new_path,
        "old_path": old_path if change == "renamed" else None,
        "change": change,
        "additions": sum(1 for x in patch_lines if x.startswith("+")),
        "deletions": sum(1 for x in patch_lines if x.startswith("-")),
        "patch": patch or None,
        "omitted": omitted,
    }


def parse(text: str) -> List[Dict[str, Any]]:
    """One entry per file of ``git diff`` output, in its order."""
    files: List[Dict[str, Any]] = []
    header: Optional[re.Match] = None
    body: List[str] = []
    for line in text.split("\n"):
        match = _HEADER.match(line)
        if match:
            if header is not None:
                files.append(_section(header.group(1), header.group(2), body))
            header, body = match, []
        elif header is not None:
            body.append(line)
    if header is not None:
        files.append(_section(header.group(1), header.group(2), body))
    return files


def _sha256s(root: Path, objects: List[str]) -> List[Optional[str]]:
    """The sha256 of each ``<rev>:<path>`` blob, ``None`` for one git does not have."""
    if not objects:
        return []
    out = _git_bytes(["cat-file", "--batch"], root, ("\n".join(objects) + "\n").encode("utf-8"))
    hashes: List[Optional[str]] = []
    pos = 0
    for _ in objects:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        pos = end + 1
        if len(header) != 3 or header[1] != b"blob":
            hashes.append(None)  # "<name> missing", or not a file
            continue
        size = int(header[2])
        hashes.append(hashlib.sha256(out[pos:pos + size]).hexdigest())
        pos += size + 1
    return hashes


def inventory(root: Path, merge_base: str, commit: str) -> List[str]:
    """Every path between *merge_base* and *commit*, renames as both sides."""
    raw = _git_bytes(
        ["diff", "--no-color", "--no-ext-diff", "--no-renames", "--name-status", "-z", merge_base, commit],
        root,
    )
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2:
        raise GitError("git listed the changed paths in a form this daemon cannot read")
    return [tokens[i + 1].decode("utf-8", "replace") for i in range(0, len(tokens), 2)]


def integration_diff(workspace_root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The files *spec*'s pinned ``commit`` changes against the merge base of ``base``."""
    commit = str(spec.get("commit") or "")
    if not _SHA.match(commit):
        raise GitError(f"not a commit: {commit!r}")
    root = Path(_out(["rev-parse", "--show-toplevel"], Path(workspace_root).expanduser()))
    base = str(spec.get("base") or "")
    if not base:  # as a local merge does: the branch the checkout is on
        base = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root, check=False).stdout.strip()
    if not valid_branch(base) or base.startswith(TASK_BRANCH_PREFIX):
        raise GitError(f"not a branch to compare with: {base!r}")
    if run_git(["cat-file", "-e", f"{commit}^{{commit}}"], root, check=False).returncode != 0:
        raise GitError(f"this repository has no commit {commit[:7]}")
    base_commit = _out(["rev-parse", f"refs/heads/{base}"], root)
    merge_base = _out(["merge-base", base_commit, commit], root)
    raw = _git_bytes(["-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "-M", merge_base, commit], root)
    files = parse(raw.decode("utf-8", "replace"))
    paths = inventory(root, merge_base, commit)
    truncated = len(files) > MAX_FILES
    files = files[:MAX_FILES]
    wanted: List[str] = []
    for f in files:
        if f["change"] != "deleted":
            wanted.append(f"{commit}:{f['path']}")
        if f["change"] != "added":
            wanted.append(f"{merge_base}:{f['old_path'] or f['path']}")
    hashes = iter(_sha256s(root, wanted))
    for f in files:
        f["sha256_new"] = next(hashes) if f["change"] != "deleted" else None
        f["sha256_old"] = next(hashes) if f["change"] != "added" else None
    return {
        "ok": True, "base": base, "base_commit": base_commit, "merge_base": merge_base,
        "commit": commit, "files": files, "truncated": truncated,
        "paths": paths[:MAX_PATHS], "paths_truncated": len(paths) > MAX_PATHS,
    }


async def handle_diff(nc, msg, reply, request_body) -> None:
    """``workspace.git.diff`` → the files, hunks and hashes, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(integration_diff, str(data.get("workspace_root") or ""), data)
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
