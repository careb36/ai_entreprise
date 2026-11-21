"""The browser sandbox image and the browser probe (issue #315), the
dependency cache in the application check's container, and install state
kept per sandbox mode.

- ``vscode-daemon/images/browser``: Node, the Playwright of its lockfile and
  the headless shell that Playwright launches, each download checked by
  sha256 per architecture; ``DELIVERY_BROWSER_IMAGE`` makes the application
  check run a browser e2e command in it (container mode only).
- ``vscode-daemon/browser_probe.py``: before a browser e2e command the daemon
  runs ``probe-browser.js`` where the command runs, and ``e2e_run`` says
  "browser present: yes / no / not observed" from that observation.
- The application check's container mounts the project's dependency cache
  as a test run does (``sandbox_cache``).
- ``Company.dependency_installs`` is kept per folder and sandbox mode: moving
  from host to container installs again.

The daemon's side runs with a fake ``node`` and the fake ``docker`` of
``test_app_check``. ``FABRIQUE_BROWSER_IMAGE_TEST`` naming a built image runs
the real thing: a Playwright smoke test against a real application in a
real container with the network off."""

from __future__ import annotations

import asyncio
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
from factories import company as _company
from models.company import Company
from services.evidence import app_check, browser_smoke
from services.loop import delivery_checks
from services.workspace import dependency_install, project_profile
from test_app_check import fake_docker  # noqa: F401 - the fixture
from test_browser_smoke import _ctx, _evidence, _Recorder

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
# The backend: the image chosen, the probe asked for, the presence said
# ---------------------------------------------------------------------------


@pytest.fixture()
def container_box(monkeypatch):
    from services.workspace import sandbox

    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(
        sandbox,
        "request_for_sprint",
        lambda *_a, **_k: {"mode": "container", "image": "python:3.12-slim"},
    )


def _smoke_reply(
    probe=None,
    exit_code=0,
    tail="  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows it (5ms)\n",
):
    reply = {
        "started": True,
        "listening": True,
        "probe": {"url": "http://127.0.0.1:3000/", "status": 200},
        "e2e": {
            "command": browser_smoke.SMOKE_COMMAND,
            "exit_code": exit_code,
            "timed_out": False,
            "output_tail": tail,
        },
        "stopped": True,
    }
    if probe is not None:
        reply["browser_probe"] = probe
    return reply


def _run_check(nats, profile):
    evidence = _evidence("", excused=["REQ-1.2"])
    del evidence["app_run"]
    evidence["workspace_root"] = "/project"
    ctx = _ctx(nats, evidence, profile)
    asyncio.run(delivery_checks.check_application(ctx))
    return evidence


def test_the_browser_image_runs_the_smoke_suite_in_container_mode(
    container_box, monkeypatch
):
    monkeypatch.setattr(
        settings, "DELIVERY_BROWSER_IMAGE", "ghcr.io/acme/browser@sha256:" + "a" * 64
    )
    nats = _Recorder(_smoke_reply({**YES, "exit_code": 0, "observed_by": "daemon"}))
    evidence = _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    ((_subject, payload),) = nats.requests
    assert payload["sandbox"]["image"] == settings.DELIVERY_BROWSER_IMAGE
    assert payload["browser_probe"] is True
    assert evidence["app_run"]["image_source"] == "DELIVERY_BROWSER_IMAGE"
    (check,) = [c for c in app_check.checks(evidence) if c["name"] == "e2e_run"]
    assert check["outcome"] == "pass"
    assert (
        check["browser"]["present"] is True
        and check["browser"]["observed_by"] == "daemon"
    )
    assert (
        "browser present: yes (chromium 156.0.8078.4, Playwright 1.64.1, from the image)"
        in check["observation"]
    )


def test_the_browser_image_is_not_used_on_the_host_or_for_another_e2e(
    monkeypatch, container_box
):
    from services.workspace import sandbox

    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGE", "browser:1")
    nats = _Recorder(_smoke_reply())
    _run_check(nats, {"start": "npm start", "port": 3000, "e2e": "pytest e2e"})
    ((_s, payload),) = nats.requests
    assert (
        payload["sandbox"]["image"] == "python:3.12-slim"
        and "browser_probe" not in payload
    )
    monkeypatch.setattr(
        sandbox, "request_for_sprint", lambda *_a, **_k: {"mode": "host"}
    )
    nats = _Recorder(_smoke_reply())
    _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    ((_s, payload),) = nats.requests
    assert "image" not in payload["sandbox"] and payload["browser_probe"] is True


def test_a_web_profile_s_e2e_is_a_browser_e2e(container_box, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGE", "browser:1")
    nats = _Recorder(_smoke_reply())
    _run_check(
        nats,
        {"start": "npm start", "port": 3000, "e2e": "npx playwright test", "web": True},
    )
    ((_s, payload),) = nats.requests
    assert (
        payload["sandbox"]["image"] == "browser:1" and payload["browser_probe"] is True
    )


def test_without_the_setting_the_image_is_today_s(container_box):
    nats = _Recorder(_smoke_reply())
    _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    ((_s, payload),) = nats.requests
    assert payload["sandbox"]["image"] == "python:3.12-slim"


def test_an_older_daemon_s_reply_says_the_browser_was_not_observed(container_box):
    nats = _Recorder(_smoke_reply())
    evidence = _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    (check,) = [c for c in app_check.checks(evidence) if c["name"] == "e2e_run"]
    assert check["outcome"] == "pass" and check["browser"]["present"] is None
    assert (
        "browser present: not observed (the daemon did not probe"
        in check["observation"]
    )


def test_the_image_s_probe_finding_no_browser_makes_a_failure_unverifiable(
    container_box,
):
    probe = {
        "from": "image",
        "available": False,
        "reason": "the browser aborted",
        "observed_by": "daemon",
    }
    nats = _Recorder(_smoke_reply(probe, exit_code=1, tail="1 failed\n"))
    evidence = _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    (check,) = [c for c in app_check.checks(evidence) if c["name"] == "e2e_run"]
    assert check["outcome"] == "unverifiable" and check["runtime_missing"]
    assert (
        "the browser sandbox image's probe launched no browser: the browser aborted"
        in check["observation"]
    )
    assert "browser present: no (the browser aborted)" in check["observation"]


def test_the_project_s_probe_finding_no_browser_only_annotates(container_box):
    probe = {
        "from": "project",
        "available": False,
        "reason": "it said so",
        "observed_by": "daemon",
    }
    nats = _Recorder(_smoke_reply(probe, exit_code=1, tail="1 failed\n"))
    evidence = _run_check(nats, {"start": "npm start", "port": 3000, "e2e": None})
    (check,) = [c for c in app_check.checks(evidence) if c["name"] == "e2e_run"]
    assert check["outcome"] == "refuted" and "runtime_missing" not in check
    assert "browser present: no (it said so)" in check["observation"]


def test_a_non_browser_e2e_says_nothing_about_a_browser():
    evidence = _evidence("ok", exit_code=0)
    evidence["app_run"]["e2e"]["command"] = "pytest e2e"
    check = browser_smoke.check_e2e(evidence)
    assert "browser" not in check and "browser present" not in check["observation"]


# ---------------------------------------------------------------------------
# Install state per sandbox mode
# ---------------------------------------------------------------------------


def test_the_install_record_is_kept_per_mode_and_host_keeps_the_old_key():
    assert dependency_install.install_key("/p", "host") == "/p"
    assert dependency_install.install_key("/p", None) == "/p"
    assert dependency_install.install_key("/p", "container") == "container:/p"
    evidence = {
        "workspace_root": "/p",
        "workspace_writes": [
            {"path": "package.json", "status": "written", "sha256": "s1"}
        ],
    }
    installs = {"/p": {"package.json": "s1"}}
    assert dependency_install.changed_manifests(evidence, installs, "host") == []
    assert (
        dependency_install.changed_manifests(evidence, installs, "container")[0]["path"]
        == "package.json"
    )
    installs["container:/p"] = {"package.json": "s1"}
    assert dependency_install.changed_manifests(evidence, installs, "container") == []


class _InstallDaemon:
    jetstream_enabled = False

    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        if subject != "workspace.execute":
            raise TimeoutError(
                subject
            )  # No snapshot: the deployment's command applies.
        self.requests.append(payload)
        return {
            "success": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "sandbox": {"mode": payload["sandbox"]["mode"], "forced_by_daemon": False},
        }


def test_moving_from_host_to_container_installs_again(
    sqlite_session, tmp_path, monkeypatch
):
    company = _company(sqlite_session)
    monkeypatch.setattr(
        settings, "DELIVERY_INSTALL_COMMAND", "pip install -r requirements.txt"
    )
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    bind = sqlite_session.get_bind()
    root = tmp_path / "project"
    root.mkdir()
    daemon = _InstallDaemon()

    def install():
        evidence = {
            "workspace_root": str(root),
            "workspace_writes": [
                {"path": "requirements.txt", "status": "written", "sha256": "abc"}
            ],
        }
        ctx = project_profile.Context(
            daemon, {"id": None, "company_id": company.id}, evidence, bind
        )
        asyncio.run(dependency_install.install_if_needed(ctx))
        return evidence

    assert install()["dependency_install"]["sandbox_mode"] == "host"
    assert "dependency_install" not in install()
    row = sqlite_session.get(Company, company.id)
    row.delivery_sandbox = "container"
    sqlite_session.add(row)
    sqlite_session.commit()
    moved = install()["dependency_install"]
    assert moved["sandbox_mode"] == "container" and moved["exit_code"] == 0
    assert daemon.requests[-1]["sandbox"]["mode"] == "container"
    assert "dependency_install" not in install()
    sqlite_session.expire_all()
    mark = dependency_install.command_digest("pip install -r requirements.txt")
    assert sqlite_session.get(Company, company.id).dependency_installs == {
        str(root): {"requirements.txt": "abc"},
        f"container:{root}": {"requirements.txt": "abc", "./": mark},
    }
    assert len(daemon.requests) == 2


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
