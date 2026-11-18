"""Check playwright_versions.json and print images.yml's browser matrix (#315).

    python3 -I vscode-daemon/images/browser/playwright_matrix.py \\
        vscode-daemon/images/browser/playwright_versions.json

Prints one JSON list, an entry per release: ``playwright``, ``lockfile_dir``,
``revision``, ``browser_version``, ``sha256_amd64`` and ``default``. The
workflow passes each value to ``docker buildx build`` as a build argument
through the environment, never pasted into a script, and these checks keep
every value to the shape it must have: an exact version, a lockfile folder
next to this file that pins that version (``.`` or ``playwright/<version>``),
digits for the revision, a four-part browser version and a sha256. Exits 1
naming what is wrong, so a list that could build something else stops the
release before anything is pushed. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
REVISION = re.compile(r"^\d{1,6}$")
BROWSER = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PACKAGES = ("@playwright/test", "playwright", "playwright-core")


class Invalid(Exception):
    pass


def _lockfile_pins(folder: Path, version: str) -> None:
    try:
        package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Invalid(f"the lockfile of {version} in {folder.name!r} could not be read ({type(e).__name__})")
    if package.get("dependencies") != {"@playwright/test": version}:
        raise Invalid(f"the lockfile folder of {version} does not pin @playwright/test {version}")
    for name in PACKAGES:
        entry = (lock.get("packages") or {}).get(f"node_modules/{name}") or {}
        if entry.get("version") != version or not str(entry.get("integrity", "")).startswith("sha512-"):
            raise Invalid(f"the lockfile of {version} does not hold {name} {version} with its integrity hash")


def matrix(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise Invalid("no releases listed")
    default = data.get("default")
    out: List[Dict[str, Any]] = []
    seen = set()
    for release in releases:
        version = str(release.get("playwright", ""))
        if not VERSION.match(version):
            raise Invalid(f"{version!r} is not an exact Playwright version")
        if version in seen:
            raise Invalid(f"Playwright {version} is listed twice")
        seen.add(version)
        lockfile_dir = str(release.get("lockfile_dir", ""))
        if lockfile_dir not in (".", f"playwright/{version}"):
            raise Invalid(f"lockfile_dir of {version} must be '.' or 'playwright/{version}', not {lockfile_dir!r}")
        if (lockfile_dir == ".") != (version == default):
            raise Invalid(f"lockfile_dir '.' is the default release's only ({version})")
        _lockfile_pins(path.parent / lockfile_dir, version)
        shell = release.get("headless_shell") or {}
        revision, browser = str(shell.get("revision", "")), str(shell.get("version", ""))
        sums = shell.get("sha256") or {}
        if not REVISION.match(revision):
            raise Invalid(f"the headless shell revision of {version} is not a number: {revision[:40]!r}")
        if not BROWSER.match(browser):
            raise Invalid(f"the headless shell version of {version} is not a browser version: {browser[:40]!r}")
        for arch in ("amd64", "arm64"):
            if not SHA256.match(str(sums.get(arch, ""))):
                raise Invalid(f"the {arch} sha256 of {version}'s headless shell is not a sha256")
        out.append({
            "playwright": version,
            "lockfile_dir": lockfile_dir,
            "revision": revision,
            "browser_version": browser,
            "sha256_amd64": sums["amd64"],
            "default": version == default,
        })
    if default not in seen:
        raise Invalid(f"the default release {default!r} is not listed")
    return out


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: playwright_matrix.py playwright_versions.json", file=sys.stderr)
        return 2
    try:
        print(json.dumps(matrix(Path(argv[1])), separators=(",", ":")))
    except (Invalid, OSError, ValueError, AttributeError, TypeError) as e:
        print(f"playwright_versions.json: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
