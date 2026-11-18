"""Which Playwright the project pins, as the daemon reads it on disk (#315).

A browser build matches one Playwright release: a project on another version
looks for its own browser revision and fails, or behaves differently. So the
backend picks the browser sandbox image of the project's version, and needs
that version from the project's files, read here, never from what the
producer reported. ``workspace.inspect``'s snapshot carries it as
``snapshot["playwright"]`` (``host_folders.py``):

- ``declared``: whether ``package.json`` lists ``@playwright/test``,
  ``playwright`` or ``playwright-core`` (the first found, as ``package``),
  ``None`` when ``package.json`` could not be read;
- ``spec``: what ``package.json`` asks for;
- ``version`` and ``from``: the exact version and the file that names it: the
  lockfile (``package-lock.json``, ``npm-shrinkwrap.json``,
  ``pnpm-lock.yaml``, ``yarn.lock``, ``bun.lock``), else an exact version in
  ``package.json``;
- ``reason`` when there is no version: a range no lockfile resolves, a
  lockfile with several versions, ``package.json`` and the lockfile
  disagreeing, a file that does not parse, is larger than ``MAX_BYTES`` or is
  not a regular file (a symlink is never followed);
- ``observed_by: daemon``.

Only the project's root is read, with the standard library; a version is
reported only when it is one (``X.Y.Z`` or ``X.Y.Z-pre``), so nothing else of
a file leaves the machine.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PACKAGES = ("@playwright/test", "playwright", "playwright-core")
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")
DEPENDENCY_FIELDS = ("devDependencies", "dependencies", "optionalDependencies")
# A real project's lockfile can run to several MiB; past this it is not read.
MAX_BYTES = 16 * 1024 * 1024
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
_EXACT_SPEC = re.compile(r"^(?:=|v)?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)$")
_VERSION_IN = r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)"


class Unreadable(Exception):
    """A file that is there but could not be read as asked."""


def _read(path: Path) -> Optional[str]:
    """The text of *path*, ``None`` when it is absent; a symlink, a folder or
    a file over ``MAX_BYTES`` raises ``Unreadable``."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unreadable(f"{path.name} could not be read ({type(e).__name__})") from e
    if not stat.S_ISREG(info.st_mode):
        raise Unreadable(f"{path.name} is not a regular file (a symlink is not followed)")
    if info.st_size > MAX_BYTES:
        raise Unreadable(f"{path.name} is larger than {MAX_BYTES} bytes")
    try:
        with open(path, "rb") as fh:
            return fh.read(MAX_BYTES + 1).decode("utf-8", errors="replace")
    except OSError as e:
        raise Unreadable(f"{path.name} could not be read ({type(e).__name__})") from e


def _declared(package: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """The first Playwright package ``package.json`` lists, and its spec."""
    for name in PACKAGES:
        for field in DEPENDENCY_FIELDS:
            deps = package.get(field)
            if isinstance(deps, dict) and isinstance(deps.get(name), str):
                return name, deps[name].strip()
    return None, None


def _npm(text: str, name: str, file: str) -> List[str]:
    try:
        lock = json.loads(text)
    except ValueError as e:
        raise Unreadable(f"{file} does not parse as JSON") from e
    if not isinstance(lock, dict):
        raise Unreadable(f"{file} is not a lockfile")
    packages = lock.get("packages")
    if isinstance(packages, dict):
        entry = packages.get(f"node_modules/{name}")
        if isinstance(entry, dict) and isinstance(entry.get("version"), str):
            return [entry["version"]]
    deps = lock.get("dependencies")
    if isinstance(deps, dict):
        entry = deps.get(name)
        if isinstance(entry, dict) and isinstance(entry.get("version"), str):
            return [entry["version"]]
    return []


def _pnpm(text: str, name: str) -> List[str]:
    # The packages' keys: '@playwright/test@1.63.0' (9), /@playwright/test@1.63.0 (6-8),
    # /@playwright/test/1.63.0 (5); a peer suffix "(…)" may follow.
    key = re.compile(r"^[ \t]+['\"]?/?" + re.escape(name) + r"[@/]" + _VERSION_IN + r"[('\":]", re.M)
    return key.findall(text)


def _yarn(text: str, name: str) -> List[str]:
    """Each entry whose header names *name* (``"@playwright/test@^1.63.0"``,
    ``"@playwright/test@npm:^1.63.0"``), read for its ``version`` line."""
    found: List[str] = []
    header = re.compile(r"(?:^|[\s,\"'])" + re.escape(name) + r"@")
    inside = False
    for line in text.splitlines():
        if line and not line[0].isspace():
            inside = line.rstrip().endswith(":") and bool(header.search(line))
            continue
        if inside:
            match = re.match(r"^\s+version:?\s+[\"']?" + _VERSION_IN + r"[\"']?\s*$", line)
            if match:
                found.append(match.group(1))
                inside = False
    return found


def _bun(text: str, name: str) -> List[str]:
    entry = re.compile(r"\"" + re.escape(name) + r"\":\s*\[\s*\"" + re.escape(name) + "@" + _VERSION_IN + "\"")
    return entry.findall(text)


def _from_lockfile(root: Path, name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(version, file, reason)`` from the first lockfile that names *name*."""
    for file in LOCKFILES:
        text = _read(root / file)
        if text is None:
            continue
        if file in ("package-lock.json", "npm-shrinkwrap.json"):
            versions = _npm(text, name, file)
        elif file == "pnpm-lock.yaml":
            versions = _pnpm(text, name)
        elif file == "yarn.lock":
            versions = _yarn(text, name)
        else:
            versions = _bun(text, name)
        distinct = sorted(set(versions))
        if not distinct:
            continue
        if any(not VERSION.match(v) for v in distinct):
            return None, file, f"{file} names a {name} version that is not an exact version"
        if len(distinct) > 1:
            return None, file, f"{file} holds several {name} versions ({', '.join(distinct)})"
        return distinct[0], file, None
    return None, None, None


def observe(root: Path) -> Dict[str, Any]:
    """The Playwright the project at *root* pins (the module's docstring)."""
    out: Dict[str, Any] = {"declared": None, "version": None, "observed_by": "daemon"}
    try:
        text = _read(root / "package.json")
        if text is None:
            return {**out, "declared": False, "reason": "no package.json in the project's folder"}
        try:
            package = json.loads(text)
        except ValueError:
            return {**out, "reason": "package.json does not parse as JSON"}
        name, spec = _declared(package if isinstance(package, dict) else {})
        if name is None:
            return {**out, "declared": False, "reason": "package.json lists no Playwright package"}
        out.update(declared=True, package=name, spec=spec[:100])
        exact = _EXACT_SPEC.match(spec)
        locked, lockfile, reason = _from_lockfile(root, name)
    except Unreadable as e:
        return {**out, "reason": str(e)}
    if reason:
        return {**out, "reason": reason}
    if locked and exact and exact.group(1) != locked:
        return {**out, "reason": f"package.json pins {name} {exact.group(1)} but {lockfile} names {locked}"}
    if locked:
        return {**out, "version": locked, "from": lockfile}
    if exact:
        return {**out, "version": exact.group(1), "from": "package.json"}
    return {**out, "reason": f"package.json asks for {name} {spec[:100]!r} and no lockfile names a version"}
