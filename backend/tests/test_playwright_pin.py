"""Which Playwright a project pins, as the daemon reads it (issue #315).

``vscode-daemon/playwright_pin.py`` reads the version from the project's
``package.json`` and lockfile (npm, pnpm, Yarn, Bun) and
``workspace.inspect``'s snapshot carries it as the daemon's observation: a
version it cannot tell is ``None`` with the reason, never a guess; a symlink
is not followed and a file over the bound is not read. The backend picks the
browser image from it (``test_browser_images_playwright.py``)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import host_folders
import playwright_pin
import pytest


def _write(root: Path, files: dict[str, object]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body)
        path.write_text(text, encoding="utf-8")
    return root


def _package(spec: str, name: str = "@playwright/test", where: str = "devDependencies"):
    return {"name": "app", "version": "1.0.0", where: {name: spec}}


def _npm_lock(version: str, name: str = "@playwright/test") -> dict:
    return {
        "name": "app",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "app", "devDependencies": {name: f"^{version}"}},
            f"node_modules/{name}": {"version": version},
            "node_modules/playwright-core": {"version": version},
        },
    }


# ---------------------------------------------------------------------------
# The daemon reads the version the project pins
# ---------------------------------------------------------------------------


def test_an_exact_version_in_package_json_is_the_pin(tmp_path):
    pin = playwright_pin.observe(_write(tmp_path, {"package.json": _package("1.63.0")}))
    assert pin["declared"] is True and pin["package"] == "@playwright/test"
    assert pin["version"] == "1.63.0" and pin["from"] == "package.json"
    assert pin["observed_by"] == "daemon"


@pytest.mark.parametrize("spec", ["=1.63.0", "v1.63.0"])
def test_an_exact_version_written_another_way_is_the_pin(tmp_path, spec):
    pin = playwright_pin.observe(_write(tmp_path, {"package.json": _package(spec)}))
    assert pin["version"] == "1.63.0"


def test_a_range_is_resolved_by_the_npm_lockfile(tmp_path):
    root = _write(
        tmp_path,
        {"package.json": _package("^1.63.0"), "package-lock.json": _npm_lock("1.63.0")},
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] == "1.63.0" and pin["from"] == "package-lock.json"
    assert pin["spec"] == "^1.63.0"


def test_an_npm_lockfile_of_version_1_is_read(tmp_path):
    lock = {
        "lockfileVersion": 1,
        "dependencies": {"@playwright/test": {"version": "1.63.0"}},
    }
    root = _write(
        tmp_path, {"package.json": _package("~1.63.0"), "npm-shrinkwrap.json": lock}
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] == "1.63.0" and pin["from"] == "npm-shrinkwrap.json"


@pytest.mark.parametrize(
    "lock",
    [
        # pnpm 9
        (
            "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    devDependencies:\n"
            "      '@playwright/test':\n        specifier: ^1.63.0\n        version: 1.63.0\n\n"
            "packages:\n\n  '@playwright/test@1.63.0':\n    resolution: {integrity: sha512-x}\n"
            "  playwright@1.63.0:\n    resolution: {integrity: sha512-y}\n"
        ),
        # pnpm 6 to 8
        (
            "lockfileVersion: '6.0'\n\ndevDependencies:\n  '@playwright/test':\n"
            "    specifier: ^1.63.0\n    version: 1.63.0\n\npackages:\n\n"
            "  /@playwright/test@1.63.0:\n    resolution: {integrity: sha512-x}\n"
        ),
        # pnpm 5
        (
            "lockfileVersion: 5.4\n\nspecifiers:\n  '@playwright/test': ^1.63.0\n\n"
            "packages:\n\n  /@playwright/test/1.63.0:\n    resolution: {integrity: sha512-x}\n"
        ),
    ],
)
def test_a_pnpm_lockfile_is_read(tmp_path, lock):
    root = _write(
        tmp_path, {"package.json": _package("^1.63.0"), "pnpm-lock.yaml": lock}
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] == "1.63.0" and pin["from"] == "pnpm-lock.yaml"


@pytest.mark.parametrize(
    "lock",
    [
        # Yarn classic
        (
            '# yarn lockfile v1\n\n"@playwright/test@^1.63.0":\n  version "1.63.0"\n'
            '  resolved "https://registry.yarnpkg.com/x.tgz"\n\n'
            'playwright@1.63.0:\n  version "1.63.0"\n'
        ),
        # Yarn berry
        (
            '__metadata:\n  version: 8\n\n"@playwright/test@npm:^1.63.0":\n'
            '  version: 1.63.0\n  resolution: "@playwright/test@npm:1.63.0"\n'
        ),
    ],
)
def test_a_yarn_lockfile_is_read(tmp_path, lock):
    root = _write(tmp_path, {"package.json": _package("^1.63.0"), "yarn.lock": lock})
    pin = playwright_pin.observe(root)
    assert pin["version"] == "1.63.0" and pin["from"] == "yarn.lock"


def test_a_bun_lockfile_is_read(tmp_path):
    lock = (
        '{\n  "lockfileVersion": 1,\n  "packages": {\n'
        '    "@playwright/test": ["@playwright/test@1.63.0", "", {}, "sha512-x"],\n'
        '    "playwright": ["playwright@1.63.0", "", {}, "sha512-y"],\n  }\n}\n'
    )
    root = _write(tmp_path, {"package.json": _package("^1.63.0"), "bun.lock": lock})
    pin = playwright_pin.observe(root)
    assert pin["version"] == "1.63.0" and pin["from"] == "bun.lock"


def test_the_playwright_package_counts_when_the_test_runner_is_not_declared(tmp_path):
    root = _write(
        tmp_path,
        {
            "package.json": _package(
                "^1.63.0", name="playwright", where="dependencies"
            ),
            "package-lock.json": _npm_lock("1.63.0", name="playwright"),
        },
    )
    pin = playwright_pin.observe(root)
    assert pin["package"] == "playwright" and pin["version"] == "1.63.0"


def test_the_package_name_is_matched_whole(tmp_path):
    """``playwright-core@1.62.0`` in the lockfile is not ``playwright``'s pin."""
    lock = 'playwright-core@1.62.0:\n  version "1.62.0"\nplaywright@1.63.0:\n  version "1.63.0"\n'
    root = _write(
        tmp_path,
        {"package.json": _package("^1.63.0", name="playwright"), "yarn.lock": lock},
    )
    assert playwright_pin.observe(root)["version"] == "1.63.0"


def test_a_range_without_a_lockfile_is_unknown_with_the_reason(tmp_path):
    pin = playwright_pin.observe(
        _write(tmp_path, {"package.json": _package("^1.63.0")})
    )
    assert pin["declared"] is True and pin["version"] is None
    assert "^1.63.0" in pin["reason"] and "lockfile" in pin["reason"]


def test_a_lockfile_with_several_versions_is_unknown(tmp_path):
    lock = (
        '"@playwright/test@^1.62.0":\n  version "1.62.1"\n\n'
        '"@playwright/test@^1.63.0":\n  version "1.63.0"\n'
    )
    root = _write(tmp_path, {"package.json": _package("^1.62.0"), "yarn.lock": lock})
    pin = playwright_pin.observe(root)
    assert (
        pin["version"] is None
        and "1.62.1" in pin["reason"]
        and "1.63.0" in pin["reason"]
    )


def test_package_json_and_the_lockfile_disagreeing_is_unknown(tmp_path):
    root = _write(
        tmp_path,
        {"package.json": _package("1.64.1"), "package-lock.json": _npm_lock("1.63.0")},
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] is None
    assert "1.64.1" in pin["reason"] and "1.63.0" in pin["reason"]


@pytest.mark.parametrize(
    "files",
    [{}, {"package.json": {"name": "app", "dependencies": {"express": "^5.0.0"}}}],
)
def test_a_project_without_playwright_declares_none(tmp_path, files):
    pin = playwright_pin.observe(_write(tmp_path, files))
    assert pin["declared"] is False and pin["version"] is None and pin["reason"]


def test_a_package_json_that_does_not_parse_is_unknown(tmp_path):
    pin = playwright_pin.observe(_write(tmp_path, {"package.json": "{not json"}))
    assert pin["declared"] is None and pin["version"] is None
    assert "package.json" in pin["reason"]


def test_a_lockfile_that_does_not_parse_is_unknown(tmp_path):
    root = _write(
        tmp_path, {"package.json": _package("^1.63.0"), "package-lock.json": "{oops"}
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] is None and "package-lock.json" in pin["reason"]


def test_a_symlinked_manifest_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, {"package-lock.json": _npm_lock("1.63.0")})
    project = _write(tmp_path / "project", {"package.json": _package("^1.63.0")})
    os.symlink(outside / "package-lock.json", project / "package-lock.json")
    pin = playwright_pin.observe(project)
    assert pin["version"] is None and "not a regular file" in pin["reason"]


def test_a_lockfile_over_the_bound_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(playwright_pin, "MAX_BYTES", 150)
    root = _write(
        tmp_path,
        {"package.json": _package("^1.63.0"), "package-lock.json": _npm_lock("1.63.0")},
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] is None and "larger than" in pin["reason"]


def test_a_version_that_is_not_one_is_never_reported(tmp_path):
    lock = _npm_lock("1.63.0")
    lock["packages"]["node_modules/@playwright/test"]["version"] = "$(curl evil)"
    root = _write(
        tmp_path, {"package.json": _package("^1.63.0"), "package-lock.json": lock}
    )
    pin = playwright_pin.observe(root)
    assert pin["version"] is None and "$(curl" not in json.dumps(pin)


def test_the_inspect_snapshot_carries_the_daemon_s_observation(tmp_path):
    _write(tmp_path, {"package.json": _package("1.63.0")})
    reply = host_folders.inspect_project(str(tmp_path), with_snapshot=True)
    assert reply["snapshot"]["playwright"]["version"] == "1.63.0"
    assert reply["snapshot"]["playwright"]["observed_by"] == "daemon"
    assert "snapshot" not in host_folders.inspect_project(str(tmp_path))


def test_a_lockfile_of_blank_lines_is_read_in_linear_time(tmp_path):
    """No pattern backtracks across lines: 4 MiB of blank lines is quick."""
    blank = "  \n" * (4 * 1024 * 1024 // 3)
    root = _write(
        tmp_path, {"package.json": _package("^1.63.0"), "pnpm-lock.yaml": blank}
    )
    start = time.monotonic()
    pin = playwright_pin.observe(root)
    assert time.monotonic() - start < 5 and pin["version"] is None
