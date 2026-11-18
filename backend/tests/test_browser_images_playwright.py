"""Browser images per Playwright release (issue #315).

A project pins its own Playwright, and a browser build that does not match
that version fails or behaves differently. So:

- the daemon reads the version the project pins from its ``package.json``
  and lockfile and puts it in ``workspace.inspect``'s snapshot as its
  observation (``test_playwright_pin.py``);
- ``services/workspace/browser_images.py``: ``DELIVERY_BROWSER_IMAGES`` maps
  each Playwright release to an image pinned by digest (a tag is refused);
  the application check runs a browser e2e command in the image of the
  project's version, falls back to ``DELIVERY_BROWSER_IMAGE`` with the
  mismatch recorded, and with a version nobody could tell does not run the
  e2e command: it is recorded as not run with the reason (``runtime_missing``),
  which ``e2e_run`` reads as unverifiable, never a pass.
- ``vscode-daemon/images/browser/playwright_versions.json``: the releases
  ``images.yml`` builds, each with its lockfile and its headless shell's
  sha256 per architecture; ``playwright_matrix.py`` checks it and turns it
  into the workflow's matrix.

The version comes from the snapshot the daemon took just before the check,
never from what the producer reported."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from config import settings
from services.loop import delivery_checks
from services.workspace import browser_images
from test_browser_smoke import _ctx, _evidence

REPO = Path(__file__).resolve().parents[2]
IMAGE_DIR = REPO / "vscode-daemon" / "images" / "browser"
VERSIONS = IMAGE_DIR / "playwright_versions.json"
MATRIX = IMAGE_DIR / "playwright_matrix.py"
REF_63 = "ghcr.io/acme/app/browser-sandbox@sha256:" + "6" * 64
REF_64 = "ghcr.io/acme/app/browser-sandbox@sha256:" + "4" * 64


# ---------------------------------------------------------------------------
# The backend's list of images, and the choice
# ---------------------------------------------------------------------------


def test_the_list_maps_each_release_to_an_image_pinned_by_digest():
    images, refused = browser_images.parse(f"1.63.0={REF_63}, 1.64.1={REF_64}")
    assert images == {"1.63.0": REF_63, "1.64.1": REF_64} and refused == []
    local = "sha256:" + "b" * 64
    assert browser_images.parse(f"1.63.0={local}")[0] == {"1.63.0": local}
    assert browser_images.parse("") == ({}, [])


@pytest.mark.parametrize(
    "entry, words",
    [
        ("1.63.0=ghcr.io/acme/app/browser-sandbox:1.4.0", "digest"),
        ("1.63.0=ghcr.io/acme/app/browser-sandbox:latest", "digest"),
        ("1.63.0=ghcr.io/acme/app@sha256:abc", "digest"),
        (f"^1.63={REF_63}", "exact"),
        (f"{REF_63}", "version=image"),
    ],
)
def test_an_entry_without_a_digest_or_an_exact_version_is_refused(entry, words):
    images, refused = browser_images.parse(entry)
    assert images == {} and len(refused) == 1 and words in refused[0]["reason"]


def test_a_release_listed_twice_with_two_images_is_refused():
    images, refused = browser_images.parse(f"1.63.0={REF_63} 1.63.0={REF_64}")
    assert images == {} and "twice" in refused[0]["reason"]


def _pin(version="1.63.0", **extra):
    return {
        "declared": True,
        "package": "@playwright/test",
        "version": version,
        "from": "package-lock.json",
        "observed_by": "daemon",
        **extra,
    }


def test_without_the_list_nothing_is_chosen():
    assert browser_images.select(_pin(), "", {}) is None


def test_the_project_s_release_gets_its_image():
    choice = browser_images.select(_pin(), REF_64, {"1.63.0": REF_63, "1.64.1": REF_64})
    assert choice["match"] == "exact" and choice["image"] == REF_63
    assert choice["source"] == "DELIVERY_BROWSER_IMAGES"
    assert choice["wanted"] == "1.63.0" and choice["pin"]["from"] == "package-lock.json"


def test_a_release_not_listed_falls_back_with_the_mismatch():
    choice = browser_images.select(
        _pin("1.62.1"), REF_64, {"1.63.0": REF_63, "1.64.1": REF_64}
    )
    assert choice["match"] == "fallback" and choice["image"] == REF_64
    assert choice["source"] == "DELIVERY_BROWSER_IMAGE"
    assert choice["mismatch"] == {
        "wanted": "1.62.1",
        "image_playwright": "1.64.1",
        "available": ["1.63.0", "1.64.1"],
    }


def test_a_fallback_without_a_default_keeps_the_sandbox_image():
    choice = browser_images.select(_pin("1.62.1"), "", {"1.63.0": REF_63})
    assert choice["match"] == "fallback" and choice["image"] is None
    assert choice["mismatch"]["image_playwright"] is None


def test_a_project_without_playwright_runs_the_image_s_own():
    pin = {
        "declared": False,
        "version": None,
        "reason": "no package.json",
        "observed_by": "daemon",
    }
    choice = browser_images.select(pin, REF_64, {"1.63.0": REF_63})
    assert choice["match"] == "not_declared" and choice["image"] == REF_64


@pytest.mark.parametrize(
    "pin, words",
    [
        (
            _pin(
                None,
                reason="package.json asks for '^1.63.0' and no lockfile names a version",
            ),
            "^1.63.0",
        ),
        (None, "no Playwright observation"),
        ({**_pin(), "observed_by": "producer"}, "not the daemon's"),
        (_pin("latest"), "not an exact version"),
    ],
)
def test_a_version_nobody_could_tell_is_unknown_with_the_reason(pin, words):
    choice = browser_images.select(pin, REF_64, {"1.63.0": REF_63})
    assert choice["match"] == "unknown" and choice["image"] is None
    assert words in choice["reason"]


# ---------------------------------------------------------------------------
# The application check picks the image from the daemon's snapshot
# ---------------------------------------------------------------------------


class _Daemon:
    """``workspace.inspect`` answers with *pin* in the snapshot; the
    application check with a passing smoke run."""

    def __init__(self, pin):
        self.pin = pin
        self.requests: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if subject == "workspace.inspect":
            snapshot = {"files": [], "contents": {}, "truncated": False}
            if self.pin is not None:
                snapshot["playwright"] = self.pin
            return {"path": payload["path"], "is_dir": True, "snapshot": snapshot}
        reply = {
            "started": True,
            "listening": True,
            "probe": {"url": "http://127.0.0.1:3000/", "status": 200},
            "stopped": True,
        }
        if payload.get("e2e_command"):
            reply["e2e"] = {
                "command": payload["e2e_command"],
                "exit_code": 0,
                "timed_out": False,
                "output_tail": "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows it (5ms)\n",
            }
        return reply

    def check(self) -> dict:
        (payload,) = [p for s, p in self.requests if s == "workspace.app.check"]
        return payload


@pytest.fixture()
def per_release(monkeypatch):
    from services.workspace import sandbox

    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGE", REF_64)
    monkeypatch.setattr(
        settings, "DELIVERY_BROWSER_IMAGES", f"1.63.0={REF_63},1.64.1={REF_64}"
    )
    monkeypatch.setattr(
        sandbox,
        "request_for_sprint",
        lambda *_a, **_k: {"mode": "container", "image": "python:3.12-slim"},
    )


def _check(daemon, evidence_extra=None):
    evidence = _evidence("", excused=["REQ-1.2"])
    del evidence["app_run"]
    evidence["workspace_root"] = "/project"
    evidence.update(evidence_extra or {})
    ctx = _ctx(daemon, evidence, {"start": "npm start", "port": 3000, "e2e": None})
    asyncio.run(delivery_checks.check_application(ctx))
    return evidence


def _ran_and_passed(evidence) -> bool:
    e2e = evidence["app_run"]["e2e"]
    return e2e.get("exit_code") == 0 and not e2e.get("runtime_missing")


def _not_run(evidence) -> str:
    """The reason the browser suite was not run. ``runtime_missing`` is what
    the Verifier's ``e2e_run`` reads as unverifiable, never a pass
    (``test_browser_smoke.py``, a protected test, holds that reading)."""
    e2e = evidence["app_run"]["e2e"]
    assert e2e["ran"] is False and "exit_code" not in e2e
    assert e2e["decided_by"] == "backend"
    return e2e["runtime_missing"]


def test_the_smoke_suite_runs_in_the_image_of_the_project_s_release(per_release):
    daemon = _Daemon(_pin("1.63.0"))
    evidence = _check(daemon)
    assert daemon.check()["sandbox"]["image"] == REF_63
    app = evidence["app_run"]
    assert app["image_source"] == "DELIVERY_BROWSER_IMAGES"
    assert app["browser_image"]["match"] == "exact"
    assert app["browser_image"]["pin"]["observed_by"] == "daemon"
    assert _ran_and_passed(evidence)


def test_the_inspection_is_of_the_folder_the_check_runs_in(per_release):
    daemon = _Daemon(_pin("1.63.0"))
    _check(daemon)
    (inspect,) = [p for s, p in daemon.requests if s == "workspace.inspect"]
    assert inspect == {"path": "/project", "snapshot": True}


def test_a_release_not_built_falls_back_and_records_the_mismatch(per_release):
    daemon = _Daemon(_pin("1.62.1"))
    evidence = _check(daemon)
    assert daemon.check()["sandbox"]["image"] == REF_64
    assert "playwright" in daemon.check()["e2e_command"]
    app = evidence["app_run"]
    assert app["image_source"] == "DELIVERY_BROWSER_IMAGE"
    assert app["browser_image"]["match"] == "fallback"
    assert app["browser_image"]["mismatch"]["wanted"] == "1.62.1"


def test_an_unknown_version_is_unverifiable_and_never_a_pass(per_release):
    daemon = _Daemon(
        _pin(
            None,
            reason="package.json asks for '^1.63.0' and no lockfile names a version",
        )
    )
    evidence = _check(daemon)
    payload = daemon.check()
    # The application still starts and is probed; the browser suite does not run.
    assert payload["e2e_command"] is None and "browser_probe" not in payload
    assert payload["sandbox"]["image"] == "python:3.12-slim"
    assert evidence["app_run"]["listening"] is True
    assert "playwright" in evidence["app_run"]["e2e"]["command"]
    assert "^1.63.0" in _not_run(evidence)


def test_an_older_daemon_without_the_observation_is_unverifiable(per_release):
    evidence = _check(_Daemon(None))
    assert "no Playwright observation" in _not_run(evidence)


def test_the_producer_s_report_never_names_the_version(per_release):
    """A deliverable saying it pinned 1.63.0 changes nothing: the daemon's
    snapshot says the version is unknown."""
    claims = {
        "deliverable": {"claims": {"playwright": "1.63.0"}},
        "test_run": {
            "command": "pytest -v",
            "exit_code": 0,
            "criteria": {},
            "output_tail": "@playwright/test@1.63.0",
        },
    }
    daemon = _Daemon(_pin(None, reason="no lockfile"))
    evidence = _check(daemon, claims)
    assert daemon.check()["e2e_command"] is None
    assert "no lockfile" in _not_run(evidence)


def test_a_tag_in_the_list_is_never_run_and_is_recorded(per_release, monkeypatch):
    monkeypatch.setattr(
        settings,
        "DELIVERY_BROWSER_IMAGES",
        "1.63.0=ghcr.io/acme/app/browser-sandbox:1.4.0",
    )
    daemon = _Daemon(_pin("1.63.0"))
    evidence = _check(daemon)
    assert daemon.check()["sandbox"]["image"] == REF_64
    app = evidence["app_run"]
    assert app["browser_image"]["match"] == "fallback"
    assert "digest" in app["browser_image"]["refused"][0]["reason"]


def test_without_the_list_the_check_is_today_s(per_release, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGES", "")
    daemon = _Daemon(_pin(None, reason="no lockfile"))
    evidence = _check(daemon)
    assert [s for s, _p in daemon.requests] == ["workspace.app.check"]
    assert daemon.check()["sandbox"]["image"] == REF_64
    assert "browser_image" not in evidence["app_run"]
    assert _ran_and_passed(evidence)


def test_on_the_host_no_image_is_chosen(per_release, monkeypatch):
    from services.workspace import sandbox

    monkeypatch.setattr(
        sandbox, "request_for_sprint", lambda *_a, **_k: {"mode": "host"}
    )
    daemon = _Daemon(_pin(None, reason="no lockfile"))
    evidence = _check(daemon)
    assert [s for s, _p in daemon.requests] == ["workspace.app.check"]
    assert "browser_image" not in evidence["app_run"]


# ---------------------------------------------------------------------------
# The releases images.yml builds
# ---------------------------------------------------------------------------


def _data() -> dict:
    return json.loads(VERSIONS.read_text())


def test_each_listed_release_has_its_lockfile_and_its_checked_browser():
    data = _data()
    releases = data["releases"]
    versions = [r["playwright"] for r in releases]
    assert len(versions) == len(set(versions)) and data["default"] in versions
    for release in releases:
        version = release["playwright"]
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        shell = release["headless_shell"]
        assert re.fullmatch(r"\d+", shell["revision"])
        assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", shell["version"])
        sums = shell["sha256"]
        assert set(sums) == {"amd64", "arm64"} and sums["amd64"] != sums["arm64"]
        assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in sums.values())
        folder = IMAGE_DIR / release["lockfile_dir"]
        lock = json.loads((folder / "package-lock.json").read_text())
        for name in ("@playwright/test", "playwright", "playwright-core"):
            entry = lock["packages"][f"node_modules/{name}"]
            assert entry["version"] == version
            assert entry["integrity"].startswith("sha512-")
        package = json.loads((folder / "package.json").read_text())
        assert package["dependencies"] == {"@playwright/test": version}


def test_the_default_release_is_the_one_the_dockerfile_names():
    data = _data()
    (default,) = [r for r in data["releases"] if r["playwright"] == data["default"]]
    text = (IMAGE_DIR / "Dockerfile").read_text()

    def arg(name):
        return re.search(rf"^ARG {name}=(\S+)$", text, re.MULTILINE).group(1)

    assert default["lockfile_dir"] == "."
    assert arg("PLAYWRIGHT_VERSION") == default["playwright"]
    assert arg("PLAYWRIGHT_LOCK_DIR") == "."
    shell = default["headless_shell"]
    assert arg("HEADLESS_SHELL_REVISION") == shell["revision"]
    assert arg("HEADLESS_SHELL_VERSION") == shell["version"]
    assert arg("HEADLESS_SHELL_SHA256_AMD64") == shell["sha256"]["amd64"]
    assert arg("HEADLESS_SHELL_SHA256_ARM64") == shell["sha256"]["arm64"]
    # The lockfile is copied from the folder the build names.
    assert (
        "COPY ${PLAYWRIGHT_LOCK_DIR}/package.json ${PLAYWRIGHT_LOCK_DIR}/package-lock.json"
        in text
    )


def _matrix(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(MATRIX), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_matrix_is_the_list_with_the_default_marked():
    run = _matrix(VERSIONS)
    assert run.returncode == 0, run.stderr
    matrix = json.loads(run.stdout)
    data = _data()
    assert [m["playwright"] for m in matrix] == [
        r["playwright"] for r in data["releases"]
    ]
    assert [m["playwright"] for m in matrix if m["default"]] == [data["default"]]
    for entry in matrix:
        assert set(entry) == {
            "playwright",
            "lockfile_dir",
            "revision",
            "browser_version",
            "sha256_amd64",
            "default",
        }


@pytest.mark.parametrize(
    "mutate, words",
    [
        (lambda d: d["releases"][0].update(playwright="1.63"), "exact"),
        (lambda d: d["releases"][0].update(lockfile_dir="../../etc"), "lockfile_dir"),
        (
            lambda d: d["releases"][0]["headless_shell"]["sha256"].update(amd64="abc"),
            "sha256",
        ),
        (
            lambda d: d["releases"][0]["headless_shell"].update(
                revision="12; rm -rf /"
            ),
            "revision",
        ),
        (lambda d: d.update(default="9.9.9"), "default"),
        (lambda d: d["releases"].append(dict(d["releases"][0])), "twice"),
        (
            lambda d: d["releases"][0].update(lockfile_dir="playwright/1.0.0"),
            "lockfile",
        ),
    ],
)
def test_the_matrix_refuses_a_list_that_could_build_something_else(
    tmp_path, mutate, words
):
    data = _data()
    mutate(data)
    bad = tmp_path / "playwright_versions.json"
    bad.write_text(json.dumps(data))
    # The lockfile folders are read next to the list.
    for release in _data()["releases"]:
        if release["lockfile_dir"] != ".":
            src = IMAGE_DIR / release["lockfile_dir"]
            dst = tmp_path / release["lockfile_dir"]
            dst.mkdir(parents=True, exist_ok=True)
            for name in ("package.json", "package-lock.json"):
                (dst / name).write_text((src / name).read_text())
    for name in ("package.json", "package-lock.json"):
        (tmp_path / name).write_text((IMAGE_DIR / name).read_text())
    run = _matrix(bad)
    assert run.returncode != 0 and words in run.stderr
