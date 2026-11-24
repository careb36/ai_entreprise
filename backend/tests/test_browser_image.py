"""The browser sandbox image and the browser probe (issue #315), and the
dependency cache in the application check's container.

- ``vscode-daemon/images/browser``: Node, the Playwright of its lockfile and
  the headless shell that Playwright launches, each download checked by
  sha256 per architecture.
- ``vscode-daemon/browser_probe.py``: before a browser e2e command the daemon
  runs ``probe-browser.js`` where the command runs, and ``e2e_run`` says
  "browser present: yes / no / not observed" from that observation.
- The application check's container mounts the project's dependency cache
  as a test run does (``sandbox_cache``).

The backend's side (``DELIVERY_BROWSER_IMAGE``, the presence said in
``e2e_run``) and install state per sandbox mode are in
``test_browser_image_backend.py``.

The daemon's side runs with a fake ``node`` and the fake ``docker`` of
``test_app_check``. ``FABRIQUE_BROWSER_IMAGE_TEST`` naming a built image runs
the real thing: a Playwright smoke test against a real application in a
real container with the network off."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import app_run
import browser_probe
import pytest
import sandbox_cache
import shell_exec
from config import settings
from test_app_check import fake_docker  # noqa: F401 - the fixture

REPO = Path(__file__).resolve().parents[2]
IMAGE_DIR = REPO / "vscode-daemon" / "images" / "browser"
PY = sys.executable
YES = {
    "from": "image",
    "available": True,
    "package": "playwright",
    "playwright": "1.64.1",
    "browser": "chromium 156.0.8078.4",
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "FABRIQUE_COMMAND_DENY",
        "FABRIQUE_SANDBOX_REQUIRED",
        "FABRIQUE_SANDBOX_RUNTIME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGE", "")


@pytest.fixture()
def fake_node(tmp_path, monkeypatch):
    """A ``node`` that prints what ``FAKE_PROBE`` holds and exits with
    ``FAKE_PROBE_EXIT``: the probe's line, without a browser."""
    script = tmp_path / "nodebin" / "node"
    script.parent.mkdir()
    script.write_text(
        f"#!{PY}\n"
        "import os, sys\n"
        "open(os.path.join(os.path.dirname(__file__), 'calls'), 'a').write(' '.join(sys.argv[1:2]) + '\\n')\n"
        "sys.stdout.write(os.environ.get('FAKE_PROBE', ''))\n"
        "sys.exit(int(os.environ.get('FAKE_PROBE_EXIT', '0')))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    # The host check's environment puts ~/.nvm, ~/.local/bin and /usr/local/bin
    # before PATH (shell_exec.build_command_env), so a machine with a real node
    # there would run it instead of this one: keep the fake first.
    real_env = shell_exec.build_command_env

    def env_with_fake_node_first():
        env = real_env()
        env["PATH"] = f"{script.parent}{os.pathsep}{env['PATH']}"
        return env

    monkeypatch.setattr(shell_exec, "build_command_env", env_with_fake_node_first)
    return script.parent / "calls"


def _line(outcome: dict) -> str:
    return browser_probe.MARK + json.dumps(outcome) + "\n"


# ---------------------------------------------------------------------------
# The image: pinned, checked per architecture, attributed
# ---------------------------------------------------------------------------


def test_the_image_pins_each_download_by_sha256_per_architecture():
    text = (IMAGE_DIR / "Dockerfile").read_text()
    assert re.search(
        r"^ARG NODE_IMAGE=node:[^@\s]+@sha256:[0-9a-f]{64}$", text, re.MULTILINE
    )
    sums = dict(
        re.findall(
            r"^ARG HEADLESS_SHELL_SHA256_(AMD64|ARM64)=([0-9a-f]{64})$",
            text,
            re.MULTILINE,
        )
    )
    assert set(sums) == {"AMD64", "ARM64"} and sums["AMD64"] != sums["ARM64"]
    assert "sha256sum -c -" in text and "TARGETARCH" in text
    assert "npm ci" in text and "--ignore-scripts" in text
    assert re.search(r"^USER node$", text, re.MULTILINE)
    # The Playwright of the lockfile is the one the image names.
    version = re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", text, re.MULTILINE).group(1)
    lock = json.loads((IMAGE_DIR / "package-lock.json").read_text())
    for name in ("@playwright/test", "playwright", "playwright-core"):
        entry = lock["packages"][f"node_modules/{name}"]
        assert entry["version"] == version and entry["integrity"].startswith("sha512-")
    assert json.loads((IMAGE_DIR / "package.json").read_text())["dependencies"] == {
        "@playwright/test": version
    }


def test_the_adapted_flow_code_keeps_its_notice():
    for name in ("Dockerfile", "probe-browser.js"):
        text = (IMAGE_DIR / name).read_text()
        assert "AixleHQ/flow" in text and "f9efa22" in text and "Apache License" in text
    notice = (REPO / "NOTICE").read_text()
    assert "vscode-daemon/images/browser" in notice


def test_the_daemon_image_ships_the_probe_and_the_new_modules():
    text = (REPO / "vscode-daemon" / "Dockerfile").read_text()
    for name in ("browser_probe.py", "run_logs.py", "images/browser/probe-browser.js"):
        assert name in text


# ---------------------------------------------------------------------------
# The probe's report, as the daemon reads it
# ---------------------------------------------------------------------------


def test_a_browser_that_launched_is_reported_with_its_versions():
    seen = browser_probe.read({"stdout": "noise\n" + _line(YES), "exit_code": 0})
    assert seen == {**YES, "exit_code": 0, "observed_by": "daemon"}


def test_no_browser_is_reported_with_the_reason():
    no = {
        "from": "project",
        "available": False,
        "reason": "Executable doesn't exist at /x",
    }
    seen = browser_probe.read({"stdout": _line(no), "exit_code": 1})
    assert seen["available"] is False and seen["reason"].startswith("Executable")
    assert seen["from"] == "project" and seen["observed_by"] == "daemon"


def test_a_yes_with_a_failing_exit_is_a_no():
    seen = browser_probe.read({"stdout": _line(YES), "exit_code": 3})
    assert seen["available"] is False and "exited 3" in seen["reason"]


@pytest.mark.parametrize(
    "run,words",
    [
        (
            {"stdout": "", "stderr": "sh: node: not found", "exit_code": 127},
            "node is not installed",
        ),
        (
            {"stdout": "", "stderr": "", "exit_code": None, "timed_out": True},
            "did not finish",
        ),
        (
            {"stdout": "garbage", "stderr": "", "exit_code": 1},
            "gave no answer (exit 1)",
        ),
        (
            {"stdout": browser_probe.MARK + "{not json", "exit_code": 1},
            "gave no answer",
        ),
    ],
)
def test_a_probe_without_an_answer_is_a_no_with_why(run, words):
    seen = browser_probe.read(run)
    assert seen["available"] is False and words in seen["reason"]


def test_the_host_probe_runs_node_in_the_project(tmp_path, fake_node, monkeypatch):
    monkeypatch.setenv("FAKE_PROBE", _line({**YES, "from": "project"}))
    seen = browser_probe.on_host(str(tmp_path), dict(os.environ))
    assert seen["available"] and seen["from"] == "project"
    assert fake_node.read_text().split() == ["-e"]


def test_without_node_the_host_probe_says_so(tmp_path):
    seen = browser_probe.on_host(str(tmp_path), {"PATH": str(tmp_path)})
    assert seen == {
        "available": False,
        "reason": "node is not installed on the daemon's machine",
        "observed_by": "daemon",
        "exit_code": None,
    }


def test_a_daemon_without_the_script_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_probe, "SCRIPT", tmp_path / "missing.js")
    assert (
        "missing from this daemon" in browser_probe.on_host(str(tmp_path), {})["reason"]
    )


# ---------------------------------------------------------------------------
# workspace.app.check probes before a browser e2e command
# ---------------------------------------------------------------------------


def _check(root, **extra):
    (root / "index.html").write_text("hello")
    return app_run.check_application(
        {
            "workspace_root": str(root),
            "start_command": f"{PY} -m http.server $PORT --bind 127.0.0.1",
            "port": _free_port(),
            "ready_timeout_seconds": 20,
            **extra,
        }
    )


def test_the_host_check_probes_before_the_e2e_command(tmp_path, fake_node, monkeypatch):
    monkeypatch.setenv("FAKE_PROBE", _line({**YES, "from": "project"}))
    reply = _check(tmp_path, e2e_command=f"{PY} -c \"print('ok')\"", browser_probe=True)
    assert reply["listening"] and reply["e2e"]["exit_code"] == 0
    assert (
        reply["browser_probe"]["available"]
        and reply["browser_probe"]["from"] == "project"
    )


def test_no_probe_without_the_request_or_without_an_e2e_command(tmp_path, fake_node):
    assert "browser_probe" not in _check(tmp_path, e2e_command="true")
    assert "browser_probe" not in _check(tmp_path, browser_probe=True)
    assert not fake_node.exists()


def test_the_container_check_probes_inside_and_mounts_the_cache(
    tmp_path,
    fake_docker,  # noqa: F811
    fake_node,
    monkeypatch,
):
    monkeypatch.setenv("FAKE_PROBE", _line(YES))
    plans = []

    def prepare(runtime, image, cwd):
        plans.append(("prepare", image))
        return {
            "volume": "fabrique-cache-k",
            "mount": "/fabrique-cache",
            "mounted": True,
            "project_key": "k",
            "state": "reused",
            "args": [
                "-v",
                "fabrique-cache-k:/fabrique-cache",
                "-e",
                "PIP_CACHE_DIR=/fabrique-cache/pip",
            ],
        }

    def finish(runtime, plan, purpose, succeeded, cwd):
        plans.append(("finish", purpose, succeeded))
        return {**{k: v for k, v in plan.items() if k != "args"}, "after": "kept"}

    monkeypatch.setattr(sandbox_cache, "prepare", prepare)
    monkeypatch.setattr(sandbox_cache, "finish", finish)
    work = tmp_path / "work"
    work.mkdir()
    reply = _check(
        work,
        e2e_command=f"{PY} -c \"print('ok')\"",
        browser_probe=True,
        sandbox={
            "mode": "container",
            "image": "fabrique-browser-sandbox:test",
            "cache": True,
        },
    )
    assert reply["listening"] and reply["e2e"]["exit_code"] == 0, reply
    assert (
        reply["browser_probe"]["available"]
        and reply["browser_probe"]["from"] == "image"
    )
    calls = fake_docker.read_text().splitlines()
    run = next(line for line in calls if line.startswith("run "))
    assert "fabrique-cache-k:/fabrique-cache" in run
    assert any(line.startswith("exec ") and " node -e " in line for line in calls)
    assert plans[0][0] == "prepare" and plans[-1] == ("finish", "check", True)
    assert reply["sandbox"]["cache"]["after"] == "kept"


def test_without_the_cache_the_container_check_reports_it_unused(
    tmp_path,
    fake_docker,  # noqa: F811
):
    work = tmp_path / "work"
    work.mkdir()
    reply = _check(work, sandbox={"mode": "container", "image": "python:3.12-slim"})
    assert reply["listening"] and "cache" not in reply["sandbox"]
    assert "fabrique-cache" not in fake_docker.read_text()


# ---------------------------------------------------------------------------
# For real: the built image, a real application, a real Playwright test
# ---------------------------------------------------------------------------

REAL_IMAGE = os.environ.get("FABRIQUE_BROWSER_IMAGE_TEST", "").strip()


@pytest.mark.skipif(
    not REAL_IMAGE or not shutil.which("docker"), reason="no built browser image named"
)
def test_real_the_smoke_test_runs_in_the_browser_image_with_the_network_off(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    work.chmod(0o777)
    (work / "server.js").write_text(
        textwrap.dedent("""
        require('http').createServer((req, res) => {
          res.writeHead(200, {'content-type': 'text/html'});
          res.end('<h1>Hello REQ-1.1</h1>');
        }).listen(Number(process.env.PORT), '127.0.0.1');
    """)
    )
    (work / "e2e").mkdir()
    (work / "e2e" / "req_1_1.spec.js").write_text(
        textwrap.dedent("""
        const { test, expect } = require('@playwright/test');
        test('REQ-1.1 shows the greeting', async ({ page }) => {
          await page.goto(process.env.APP_URL + '/');
          await expect(page.locator('h1')).toHaveText('Hello REQ-1.1');
        });
    """)
    )
    reply = app_run.check_application(
        {
            "workspace_root": str(work),
            "start_command": "node server.js",
            "port": _free_port(),
            "e2e_command": "playwright test e2e --reporter=list --retries=0 --workers=1",
            "ready_timeout_seconds": 60,
            "e2e_timeout_seconds": 300,
            "browser_probe": True,
            "sandbox": {"mode": "container", "image": REAL_IMAGE},
        }
    )
    assert reply["sandbox"]["network"] == "off"
    assert reply["sandbox"]["image_identity"]["id"].startswith("sha256:")
    assert reply["browser_probe"]["available"] is True, reply["browser_probe"]
    assert reply["browser_probe"]["from"] == "image"
    assert reply["e2e"]["exit_code"] == 0, reply["e2e"]
    assert "REQ-1.1 shows the greeting" in reply["e2e"]["output_tail"]
    subprocess.run(["docker", "ps", "-a"], check=False, capture_output=True)
