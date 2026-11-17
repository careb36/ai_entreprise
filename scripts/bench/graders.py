#!/usr/bin/env python3
"""The bench's graders, taken from a commit (``run.sh --graders <sha>``;
REQ-4 of ``docs/design/rsi-loop.md``).

    graders.py checkout --repo DIR --rev REV --dest DIR --out FILE

``checkout`` reads the grading paths (``GRADER_PATHS``: the specifications,
their person-written acceptance tests and the fake's answers under
``bench/``, and the grader itself under ``scripts/bench/``) from the
commit's objects (``git ls-tree`` and ``git cat-file``), never from a working
tree, so nothing the candidate's checkout holds or changed reaches the grade.
They are written into DEST, emptied first. A symbolic link or a submodule
under those paths is refused: it would point outside what the commit holds.

FILE gets the record the scorecard carries (``graders``, schema v3, optional):

- ``sha``: the full commit sha REV resolved to;
- ``trees``: per grading path, git's tree id at that commit, so the runner
  can check that another commit's copy is the same (``git rev-parse
  <commit>:<path>``);
- ``sha256``: a digest of the files written into DEST, computed here from
  their bytes: every file's path and sha256, in path order.

Every hash is the runner's own; none is read from the build under test
(rule 6 of ``docs/POSITIONING.md``).

    graders.py daemon --repo DIR --rev REV --dest DIR --out FILE

``daemon`` takes the workspace daemon's sandbox and the daemon modules it
imports (``DAEMON_FILES``) from the same commit's objects into DEST, flat,
for ``sandbox_grading.py`` to load before any container starts: the bench run
from a candidate's checkout never imports the candidate's daemon on the host
(review of PR #634). FILE gets ``{"sha", "files": {name: blob id},
"sha256"}``, the last ``daemon_digest`` of DEST, which the grader checks again
before importing.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

GRADER_PATHS = ("bench", "scripts/bench")
# What sandbox_grading.py imports from the daemon on the host: the sandbox and
# every daemon module it loads (all protected paths).
DAEMON_FILES = ("sandbox.py", "sandbox_image.py", "sandbox_cache.py", "run_logs.py")
DAEMON_FOLDER = "vscode-daemon"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GradersError(Exception):
    """The graders could not be taken from the commit."""


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        run = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GradersError(f"git could not run: {exc}") from exc
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", "replace").strip()
        raise GradersError(f"git {' '.join(args[:2])} failed: {detail}")
    return run.stdout


def resolve(repo: Path, rev: str) -> str:
    """The full sha of the commit *rev* names."""
    if not rev or rev.startswith("-"):
        raise GradersError(f"not a revision: {rev!r}")
    try:
        sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except GradersError as exc:
        raise GradersError(f"{rev} is not a commit of {repo}") from exc
    return sha.decode().strip()


def tree_ids(repo: Path, sha: str) -> dict[str, str]:
    """Git's tree id of each grading path at *sha*."""
    out = {}
    for path in GRADER_PATHS:
        try:
            out[path] = _git(repo, "rev-parse", f"{sha}:{path}").decode().strip()
        except GradersError as exc:
            raise GradersError(f"{path} is not in commit {sha[:12]}") from exc
    return out


def _entries(
    repo: Path, sha: str, paths: tuple[str, ...] = GRADER_PATHS
) -> list[tuple[str, str, str]]:
    """(mode, object id, path) of every file under *paths* (the grading paths)."""
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", sha, "--", *paths)
    entries = []
    for record in listing.decode("utf-8").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise GradersError(f"{path} is not a regular file in {sha[:12]} ({mode})")
        parts = PurePosixPath(path).parts
        if PurePosixPath(path).is_absolute() or ".." in parts:
            raise GradersError(f"{path} leaves the checkout")
        entries.append((mode, oid, path))
    return entries


def _blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    """The contents of *oids*, in one ``git cat-file --batch``."""
    out = _git(
        repo, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in oids).encode()
    )
    blobs, at = {}, 0
    while at < len(out):
        end = out.index(b"\n", at)
        oid, kind, size = out[at:end].decode().split()
        if kind != "blob":
            raise GradersError(f"object {oid} is a {kind}, not a blob")
        start = end + 1
        blobs[oid] = out[start : start + int(size)]
        at = start + int(size) + 1
    return blobs


def extract(repo: Path, sha: str, dest: Path) -> int:
    """Write the grading paths of *sha* into *dest* (emptied first); the
    number of files written."""
    entries = _entries(repo, sha)
    blobs = _blobs(repo, sorted({oid for _, oid, _ in entries}))
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for mode, oid, path in entries:
        target = dest.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs[oid])
        if mode == "100755":
            target.chmod(0o755)
    return len(entries)


def content_digest(root: Path) -> str:
    """sha256 over every file under *root*: its path and its own sha256, in
    path order. Computed from the bytes on disk."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        name = path.relative_to(root).as_posix()
        own = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{name}\0{own}\n".encode())
    return digest.hexdigest()


def checkout(repo: Path, rev: str, dest: Path) -> dict[str, Any]:
    """Take the graders of *rev* into *dest*; the record for the scorecard."""
    sha = resolve(repo, rev)
    trees = tree_ids(repo, sha)
    extract(repo, sha, dest)
    return {"sha": sha, "trees": trees, "sha256": content_digest(dest)}


def daemon_digest(folder: Path) -> str:
    """sha256 over ``DAEMON_FILES`` in *folder*: each name and its own sha256,
    in that order; a missing file counts as empty bytes under its name."""
    digest = hashlib.sha256()
    for name in DAEMON_FILES:
        path = folder / name
        own = hashlib.sha256(path.read_bytes() if path.is_file() else b"").hexdigest()
        digest.update(f"{name}\0{own}\n".encode())
    return digest.hexdigest()


def daemon(repo: Path, rev: str, dest: Path) -> dict[str, Any]:
    """Take ``DAEMON_FILES`` of *rev* from its objects into *dest* (emptied
    first, flat); the record with the runner's digest."""
    sha = resolve(repo, rev)
    wanted = tuple(f"{DAEMON_FOLDER}/{name}" for name in DAEMON_FILES)
    entries = _entries(repo, sha, wanted)
    found = {PurePosixPath(path).name: (mode, oid) for mode, oid, path in entries}
    missing = [name for name in DAEMON_FILES if name not in found]
    if missing:
        raise GradersError(f"{', '.join(missing)} not in {DAEMON_FOLDER}/ of {sha[:12]}")
    blobs = _blobs(repo, sorted(oid for _, oid in found.values()))
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in DAEMON_FILES:
        (dest / name).write_bytes(blobs[found[name][1]])
    return {
        "sha": sha,
        "files": {name: found[name][1] for name in DAEMON_FILES},
        "sha256": daemon_digest(dest),
    }


def problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the optional ``graders`` block of *card*."""
    if "graders" not in card:
        return []
    block = card["graders"]
    if not isinstance(block, dict):
        return ["graders must be an object"]
    found = []
    if not isinstance(block.get("sha"), str) or not _SHA1.match(block["sha"]):
        found.append("graders.sha must be a full commit sha")
    trees = block.get("trees")
    if (
        not isinstance(trees, dict)
        or not trees
        or not all(
            isinstance(k, str) and isinstance(v, str) and _SHA1.match(v)
            for k, v in trees.items()
        )
    ):
        found.append("graders.trees must map each grading path to a tree id")
    if not isinstance(block.get("sha256"), str) or not _SHA256.match(block["sha256"]):
        found.append("graders.sha256 must be a sha256 hex digest")
    return found


def render_lines(card: dict[str, Any]) -> list[str]:
    """Where the card's graders came from; nothing for a card without them."""
    block = card.get("graders")
    if not block:
        return []
    sha, digest = block["sha"], block["sha256"]
    return [f"Graded by the tests of commit `{sha}`; their files' sha256 `{digest}`."]


def label(card: dict[str, Any]) -> str:
    """The graders' commit for a diff's header: ``the build's own`` without a block."""
    block = card.get("graders")
    return f"`{block['sha'][:12]}`" if block else "the build's own"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("checkout")
    c.add_argument("--repo", required=True, type=Path)
    c.add_argument("--rev", required=True)
    c.add_argument("--dest", required=True, type=Path)
    c.add_argument("--out", required=True, type=Path)
    d = sub.add_parser("daemon")
    for flag in ("--repo", "--dest", "--out"):
        d.add_argument(flag, required=True, type=Path)
    d.add_argument("--rev", required=True)
    args = parser.parse_args(argv)
    take = daemon if args.command == "daemon" else checkout
    try:
        record = take(args.repo, args.rev, args.dest)
    except GradersError as exc:
        print(f"the graders could not be taken: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
