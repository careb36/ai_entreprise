"""Files as they are at a commit of the default branch, never the checkout's (issue #700).

A company's standards file (``.fabrique/standards.toml``) and the files its
``command`` standards depend on (their ``guards``) are imported from the
**default branch's** head commit, never from a task branch, a sprint's
integration branch or a delivery's worktree
(``docs/design/COMPANY_STANDARDS.md`` §4, §10 T2): a delivery that edits the
file cannot change the rule it is judged by.

``workspace.git.read_at`` answers, read locally (no remote is contacted,
whatever the forge policy):

- ``commit``: the commit read. With ``commit`` in the request, that commit
  (re-read from a draft's record); otherwise the head of ``base`` (the
  company's base branch), else of the branch ``origin/HEAD`` names, else of
  ``main``, else of ``master``. The checked-out branch is never used: it may
  be anything the person or an agent left there.
- ``files``: for each of ``paths``, its bytes in base64 with ``status``
  ``present``, or ``missing``, ``symlink`` (refused: a link's target is not
  the file reviewed), ``not_file`` (a folder or a submodule) or
  ``too_large``.
- ``folders``: for each of ``folders``, every file under it at that commit,
  recursively, sorted, the same way (a guard hashes whole folders, §12
  decision 5); ``status`` ``missing`` when nothing is there.
- ``tree``: with ``list``, every path at that commit (for the import's dry
  run), up to ``MAX_TREE``.
- ``sandbox_required``: this machine's ``FABRIQUE_SANDBOX_REQUIRED``, the
  floor it applies to every command.

The daemon sends bytes, never a hash: the backend hashes what it received
(rule 6 of ``docs/POSITIONING.md``).
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import git_base
import git_env
from git_workspace import GitError, run_git, valid_branch

SUBJECT = "workspace.git.read_at"
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PATHS = 2000
MAX_FOLDERS = 50
MAX_TREE = 50000
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _relative(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    text = value.strip().replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or re.match(r"[A-Za-z]:", text):
        return None
    return "/".join(parts)


def default_branch(root: Path, base: str) -> Optional[str]:
    """The branch whose head is read: *base*, else origin's HEAD, else main or master."""
    if base:
        return base if git_base._commit(root, f"refs/heads/{base}") else None
    head = run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root, check=False)
    name = head.stdout.strip().rpartition("refs/remotes/origin/")[2] if head.returncode == 0 else ""
    for candidate in (name, "main", "master"):
        if candidate and valid_branch(candidate) and git_base._commit(root, f"refs/heads/{candidate}"):
            return candidate
    return None


def _ls_tree(root: Path, commit: str, path: str = "") -> List[Tuple[str, str, str, str]]:
    """``(mode, type, object, path)`` of every entry under *path* at *commit*."""
    args = ["ls-tree", "-r", "-z", commit]
    if path:
        args += ["--", path]
    result = run_git(args, root)
    out = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        meta, _, name = record.partition("\t")
        mode, kind, obj = meta.split(" ")[:3]
        out.append((mode, kind, obj, name))
    return out


def _blobs(root: Path, objects: List[str]) -> Dict[str, bytes]:
    """The bytes of each blob, read in one ``git cat-file --batch``."""
    if not objects:
        return {}
    env = git_env.env_for(["cat-file"], root)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
        ["git", "cat-file", "--batch"], cwd=str(root), env=env, capture_output=True,
        input=("\n".join(objects) + "\n").encode(), timeout=60, check=False,
    )
    if result.returncode != 0:
        raise GitError((result.stderr or b"").decode("utf-8", "replace").strip() or "git cat-file failed")
    data, out, i = result.stdout, {}, 0
    while i < len(data):
        end = data.index(b"\n", i)
        header = data[i:end].decode().split(" ")
        i = end + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        out[header[0]] = data[i:i + size]
        i += size + 1
    return out


class _Reader:
    def __init__(self, root: Path, commit: str):
        self.root, self.commit, self.left = root, commit, MAX_TOTAL_BYTES

    def entries(self, rows: List[Tuple[str, str, str, str]]) -> List[Dict[str, Any]]:
        sized = []
        for mode, kind, obj, name in rows:
            if mode == "120000":
                sized.append(({"path": name, "status": "symlink"}, None))
            elif kind != "blob":
                sized.append(({"path": name, "status": "not_file"}, None))
            else:
                sized.append(({"path": name, "status": "present"}, obj))
        sizes = self._sizes([obj for _, obj in sized if obj])
        wanted = []
        for entry, obj in sized:
            if obj is None:
                continue
            size = sizes.get(obj, MAX_FILE_BYTES + 1)
            if size > MAX_FILE_BYTES or size > self.left:
                entry["status"] = "too_large"
                continue
            self.left -= size
            wanted.append(obj)
        blobs = _blobs(self.root, wanted)
        out = []
        for entry, obj in sized:
            if entry["status"] == "present":
                entry["content_b64"] = base64.b64encode(blobs.get(obj, b"")).decode("ascii")
            out.append(entry)
        return out

    def _sizes(self, objects: List[str]) -> Dict[str, int]:
        if not objects:
            return {}
        env = git_env.env_for(["cat-file"], self.root)
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", "cat-file", "--batch-check"], cwd=str(self.root), env=env, capture_output=True,
            input=("\n".join(objects) + "\n").encode(), timeout=60, check=False,
        )
        sizes = {}
        for line in result.stdout.decode().splitlines():
            parts = line.split(" ")
            if len(parts) == 3 and parts[2].isdigit():
                sizes[parts[0]] = int(parts[2])
        return sizes

    def file(self, path: str) -> Dict[str, Any]:
        rows = [r for r in _ls_tree(self.root, self.commit, path) if r[3] == path]
        if not rows:
            folder = _ls_tree(self.root, self.commit, path)
            return {"path": path, "status": "not_file" if folder else "missing"}
        return self.entries(rows)[0]

    def folder(self, path: str) -> Dict[str, Any]:
        rows = sorted(_ls_tree(self.root, self.commit, path), key=lambda r: r[3])
        rows = [r for r in rows if r[3] == path or r[3].startswith(path + "/")]
        if not rows:
            return {"path": path, "status": "missing", "files": []}
        return {"path": path, "status": "present", "files": self.entries(rows)}


def read_at(workspace_root: str, base: str = "", commit: str = "", paths: Optional[List[Any]] = None,
            folders: Optional[List[Any]] = None, list_tree: bool = False) -> Dict[str, Any]:
    """The answer to ``workspace.git.read_at`` (module docstring); raises ``GitError`` (409) or ValueError (400)."""
    folder = Path(workspace_root).expanduser()
    if not workspace_root or not folder.is_absolute():
        raise ValueError("workspace_root is an absolute path")
    if not folder.is_dir():
        raise FileNotFoundError(f"the project folder does not exist on this machine: {workspace_root}")
    if base and not valid_branch(base):
        raise ValueError(f"not a branch name: {base!r}")
    if commit and not _SHA.fullmatch(commit):
        raise ValueError("commit is a full commit id")
    paths, folders = list(paths or []), list(folders or [])
    if len(paths) > MAX_PATHS or len(folders) > MAX_FOLDERS:
        raise ValueError(f"at most {MAX_PATHS} paths and {MAX_FOLDERS} folders")
    clean_paths, clean_folders = [_relative(p) for p in paths], [_relative(f) for f in folders]
    if None in clean_paths or None in clean_folders:
        raise ValueError("every path is a path inside the repository")
    root = git_base.toplevel(folder)
    if root is None:
        raise GitError(f"{workspace_root} is not a git repository")
    name = None
    if commit:
        if git_base._commit(root, commit) != commit:
            raise GitError(f"no such commit in this repository: {commit}")
    else:
        name = default_branch(root, base)
        if name is None:
            raise GitError(f"no default branch to read: {base!r} is not a local branch" if base
                           else "no default branch to read (no origin/HEAD, main or master)")
        commit = git_base._commit(root, f"refs/heads/{name}") or ""
    reader = _Reader(root, commit)
    answer: Dict[str, Any] = {
        "ok": True, "commit": commit, "base": name,
        "files": [reader.file(p) for p in clean_paths],
        "folders": [reader.folder(f) for f in clean_folders],
        "sandbox_required": os.getenv("FABRIQUE_SANDBOX_REQUIRED", "").strip().lower(),
    }
    if list_tree:
        tree = [r[3] for r in _ls_tree(root, commit)]
        answer["tree"], answer["tree_truncated"] = tree[:MAX_TREE], len(tree) > MAX_TREE
    return answer


async def handle_read_at(nc, msg, reply, request_body) -> None:
    """``workspace.git.read_at`` → the files at that commit, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(
            read_at, str(data.get("workspace_root") or ""), str(data.get("base") or ""),
            str(data.get("commit") or ""), data.get("paths") if isinstance(data.get("paths"), list) else [],
            data.get("folders") if isinstance(data.get("folders"), list) else [], bool(data.get("list")),
        )
    except ValueError as e:
        body = {"error": {"status": 400, "message": str(e)}}
    except FileNotFoundError as e:
        body = {"error": {"status": 404, "message": str(e)}}
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)


HANDLERS = ((SUBJECT, handle_read_at),)
