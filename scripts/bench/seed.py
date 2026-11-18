#!/usr/bin/env python3
"""The produced project's starting point, per specification (issue #316).

    seed.py --spec DIR --project DIR

A specification with a ``seed/`` folder starts from an existing repository,
not from an empty folder: the factory is measured on code it did not write
(the self-hosting findings of #555). The project folder is emptied, the
seed's files are copied into it and committed as the base of a new Git
repository, so the workspace daemon observes a repository with code (no
foundation sprint) and ``git -C <project> diff <base>`` shows what the
factory changed. The commit is made with a fixed author, committer and date
and without the machine's Git configuration, so the same seed always gives
the same base sha.

A specification without ``seed/`` starts as before: an empty folder holding
only ``README.md`` with ``# <name>``, and no repository.

A symbolic link, a ``.git`` entry or anything other than a file or a folder
under ``seed/`` is refused: the seed is the bench's own files, nothing more.
Python's caches (``__pycache__``, ``.pytest_cache``, ``*.pyc``) left by a
local run of the seed's tests are not copied.

Prints one JSON line: ``{"seeded": bool, "base": sha or null, "files": n}``.
Exit 0, or 2 with the reason on stderr.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SEED = "seed"
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}
# The base commit's identity and date: fixed, so the base sha is reproducible.
IDENTITY = "La Fabrique bench"
IDENTITY_EMAIL = "bench@example.invalid"
DATE = "2026-01-01T00:00:00+00:00"
MESSAGE = "Seed of the reference specification {name}"


class SeedError(Exception):
    """The seed could not be copied or committed."""


def seed_files(seed: Path) -> list[Path]:
    """The seed's files, relative to it and in path order; SeedError for what
    a seed may not hold."""
    files = []
    for root, dirs, names in os.walk(seed, followlinks=False):
        here = Path(root)
        for name in list(dirs):
            path = here / name
            if path.is_symlink():
                raise SeedError(
                    f"a symbolic link in the seed: {path.relative_to(seed)}"
                )
            if name == ".git":
                raise SeedError(f"a Git folder in the seed: {path.relative_to(seed)}")
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
        for name in names:
            path = here / name
            relative = path.relative_to(seed)
            if path.is_symlink():
                raise SeedError(f"a symbolic link in the seed: {relative}")
            if name == ".git":
                raise SeedError(f"a Git entry in the seed: {relative}")
            if not path.is_file():
                raise SeedError(f"not a regular file in the seed: {relative}")
            if path.suffix in SKIPPED_SUFFIXES:
                continue
            files.append(relative)
    return sorted(files)


def _git(project: Path, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(project),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": IDENTITY,
        "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
        "GIT_AUTHOR_DATE": DATE,
        "GIT_COMMITTER_NAME": IDENTITY,
        "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
        "GIT_COMMITTER_DATE": DATE,
    }
    flags = ["-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false"]
    flags += ["-c", "core.autocrlf=false", "-c", "init.defaultBranch=main"]
    try:
        done = subprocess.run(
            ["git", *flags, *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SeedError(f"git {args[0]}: {exc}") from None
    if done.returncode != 0:
        raise SeedError(f"git {args[0]}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def prepare(spec_dir: Path, project: Path) -> dict:
    """Empty *project* and fill it from *spec_dir*'s seed, or with the
    README of an unseeded specification."""
    name = spec_dir.name
    seed = spec_dir / SEED
    if seed.is_symlink() or (seed.exists() and not seed.is_dir()):
        raise SeedError(f"{seed} must be a folder")
    files = seed_files(seed) if seed.is_dir() else None
    if project.is_symlink():
        raise SeedError(f"{project} is a symbolic link")
    shutil.rmtree(project, ignore_errors=True)
    if project.exists():
        raise SeedError(f"{project} could not be emptied")
    project.mkdir(parents=True)
    if files is None:
        (project / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        return {"seeded": False, "base": None, "files": 1}
    if not files:
        raise SeedError(f"{seed} holds no file")
    for relative in files:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed / relative, target)
    _git(project, "init", "--quiet")
    _git(project, "add", "--all", "--", ".")
    _git(project, "commit", "--quiet", "--no-verify", "-m", MESSAGE.format(name=name))
    base = _git(project, "rev-parse", "HEAD")
    return {"seeded": True, "base": base, "files": len(files)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--spec", required=True, help="the specification's folder")
    parser.add_argument(
        "--project", required=True, help="the produced project's folder"
    )
    args = parser.parse_args(argv)
    try:
        result = prepare(Path(args.spec), Path(args.project))
    except (SeedError, OSError) as exc:
        print(f"seed of {Path(args.spec).name}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
