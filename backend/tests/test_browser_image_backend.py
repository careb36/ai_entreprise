"""The browser sandbox image as the backend uses it (issue #315), and install
state kept per sandbox mode. Split out of ``test_browser_image.py``, which
keeps the image, the probe and the daemon's application check.

- ``DELIVERY_BROWSER_IMAGE`` makes the application check run a browser e2e
  command in the browser image (container mode only), asks the daemon to
  probe, and ``e2e_run`` says "browser present: yes / no / not observed".
- ``Company.dependency_installs`` is kept per folder and sandbox mode: moving
  from host to container installs again."""

from __future__ import annotations

import asyncio

import pytest
from config import settings
from factories import company as _company
from models.company import Company
from services.evidence import app_check, browser_smoke
from services.loop import delivery_checks
from services.workspace import dependency_install, project_profile
from test_browser_smoke import _ctx, _evidence, _Recorder

YES = {
    "from": "image",
    "available": True,
    "package": "playwright",
    "playwright": "1.64.1",
    "browser": "chromium 156.0.8078.4",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "FABRIQUE_COMMAND_DENY",
        "FABRIQUE_SANDBOX_REQUIRED",
        "FABRIQUE_SANDBOX_RUNTIME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_BROWSER_IMAGE", "")


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
