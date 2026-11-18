"""
``workspace.capabilities`` handler in vscode-daemon/daemon.py (issue #103).

The daemon is not a package, so it is imported by file path.  ``shutil.which``
is patched so the result does not depend on the CLIs installed on this host.

The last section is the backend's side of the abilities a daemon announces in
``workspace.status`` and its heartbeat (#315, ``daemon_abilities``): kept as
the daemon's claim, used instead of guessing from replies, and an older daemon
that announces nothing keeps the fallback. The daemon's side is in
``test_daemon_protocol.py``.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_daemon_capabilities.py -q
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


@pytest.fixture(scope="module")
def daemon():
    spec = importlib.util.spec_from_file_location("fabrique_vscode_daemon", DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _which_finding(*found):
    def fake_which(name, path=None):
        return f"/usr/local/bin/{name}" if name in found else None

    return fake_which


async def _ask(daemon, payload: dict | None):
    nc = SimpleNamespace(publish=AsyncMock())
    data = json.dumps(payload).encode() if payload is not None else b""
    msg = SimpleNamespace(subject="workspace.capabilities", data=data, reply="_INBOX.1")
    await daemon.handle_capabilities(nc, msg)
    nc.publish.assert_awaited_once()
    subject, body = nc.publish.await_args.args
    assert subject == "_INBOX.1"
    return json.loads(body.decode())


@pytest.mark.asyncio
async def test_capabilities_lists_assistants_found_on_path(daemon, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", _which_finding("claude"))

    reply = await _ask(daemon, {"commands": ["copilot", "claude"]})

    assert reply == {"assistants": ["claude"]}


@pytest.mark.asyncio
async def test_capabilities_empty_when_no_cli_installed(daemon, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", _which_finding())
    monkeypatch.setattr(daemon, "ASSISTANT_FALLBACK_PATHS", {})

    reply = await _ask(daemon, {})

    assert reply == {"assistants": []}


@pytest.mark.asyncio
async def test_capabilities_default_commands_and_empty_body(daemon, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", _which_finding("copilot", "claude"))
    monkeypatch.setattr(daemon, "ASSISTANT_COMMANDS_DEFAULT", "copilot,claude")

    reply = await _ask(daemon, None)

    assert reply == {"assistants": ["copilot", "claude"]}


@pytest.mark.asyncio
async def test_capabilities_ignores_paths_and_shell_syntax(daemon, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name, path=None: "/bin/x")

    reply = await _ask(daemon, {"commands": ["/bin/sh", "../claude", "claude"]})

    assert reply == {"assistants": ["claude"]}


# --- workspace.execute: a command the timeout killed --------------------------


def _fake_run(daemon, monkeypatch, **kwargs):
    seen = {}

    def fake(*args, **inner):
        seen.update(inner)
        seen["argv"] = args[0] if args else None
        outcome = kwargs.get("raises")
        if outcome is not None:
            raise outcome
        return SimpleNamespace(
            returncode=kwargs.get("returncode", 0), stdout="", stderr=""
        )

    monkeypatch.setattr(daemon.subprocess, "run", fake)
    return seen


def test_execute_reports_a_timeout_as_timed_out_with_its_output(daemon, monkeypatch):
    """The backend must be able to tell 'hung' from 'ran and failed'."""
    import subprocess

    seen = _fake_run(
        daemon,
        monkeypatch,
        raises=subprocess.TimeoutExpired(
            cmd="sleep 999", timeout=120, output="partial"
        ),
    )

    result = daemon.execute_shell_command("sleep 999", "/tmp")

    assert result["success"] is False
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == seen["timeout"]
    assert result["stdout"] == "partial"
    assert "timed out" in result["error"]


def test_execute_reports_a_normal_failure_without_the_timed_out_flag(
    daemon, monkeypatch
):
    _fake_run(daemon, monkeypatch, returncode=2)

    result = daemon.execute_shell_command("false", "/tmp")

    assert result["exit_code"] == 2
    assert "timed_out" not in result


def test_execute_uses_the_module_timeout_constant(daemon, monkeypatch):
    """The daemon timeout must stay under the backend's 130s NATS wait."""
    seen = _fake_run(daemon, monkeypatch, returncode=0)

    daemon.execute_shell_command("true", "/tmp")

    assert seen["timeout"] == daemon.COMMAND_TIMEOUT_SECONDS
    assert daemon.COMMAND_TIMEOUT_SECONDS < 130


def test_execute_keeps_other_errors_untyped(daemon, monkeypatch):
    _fake_run(daemon, monkeypatch, raises=OSError("no such directory"))

    result = daemon.execute_shell_command("true", "/nonexistent")

    assert result["success"] is False
    assert "timed_out" not in result
    assert "no such directory" in result["error"]


# ---------------------------------------------------------------------------
# The abilities a daemon announces, on the backend's side (#315)
# ---------------------------------------------------------------------------

ANNOUNCED = {
    "abilities_version": 1,
    "subjects": ["workspace.app.check", "workspace.execute", "workspace.status"],
    "features": {"workspace.execute": ["full_log"], "workspace.app.check": []},
}


def _beat(daemon_id="d1", abilities=ANNOUNCED, **extra):
    beat = {"daemon_id": daemon_id, "sequence": 1, "protocol_version": 1, **extra}
    if abilities is not None:
        beat["abilities"] = abilities
    return beat


def test_the_announcement_is_bounded_and_an_unknown_version_is_not_read():
    from services.workspace import daemon_abilities

    parsed = daemon_abilities.parse(
        {**ANNOUNCED, "subjects": ["workspace.read", 7, "", "x" * 65, "workspace.read"]}
    )
    assert parsed["subjects"] == ["workspace.read"]
    assert daemon_abilities.parse({**ANNOUNCED, "abilities_version": 2}) is None
    assert daemon_abilities.parse({**ANNOUNCED, "abilities_version": True}) is None
    assert daemon_abilities.parse({"abilities_version": 1}) is None
    assert daemon_abilities.parse("everything") is None


def test_the_announcement_is_kept_as_the_daemon_s_claim_never_as_observed():
    """WHEN a heartbeat announces abilities THEN they are kept under
    ``reported_by_daemon``, apart from what the backend observed."""
    from services.workspace import daemon_liveness

    daemon_liveness.observe_heartbeat(_beat())
    (entry,) = daemon_liveness.liveness()["daemons"]
    assert entry["reported_by_daemon"]["abilities"]["subjects"] == ANNOUNCED["subjects"]
    observed = {k: v for k, v in entry.items() if k != "reported_by_daemon"}
    assert "abilities" not in json.dumps(observed)


def test_announces_answers_from_the_daemons_heard_and_none_without_them():
    from services.workspace import daemon_liveness

    assert daemon_liveness.announces("workspace.execute") is None  # none heard
    daemon_liveness.observe_heartbeat(_beat())
    assert daemon_liveness.announces("workspace.execute") is True
    assert daemon_liveness.announces("workspace.execute", "full_log") is True
    assert daemon_liveness.announces("workspace.app.check", "full_log") is False
    assert daemon_liveness.announces("workspace.signatures") is False


def test_an_older_daemon_among_them_keeps_the_fallback():
    """WHEN one daemon heard announces nothing (an older one) THEN nothing is
    announced: the caller guesses as before."""
    from services.workspace import daemon_liveness

    daemon_liveness.observe_heartbeat(_beat())
    daemon_liveness.observe_heartbeat(_beat("old", abilities=None))
    assert daemon_liveness.announces("workspace.signatures") is None


def test_daemons_that_disagree_announce_nothing_and_a_gone_one_does_not_count():
    import time

    from services.workspace import daemon_liveness

    more = {**ANNOUNCED, "subjects": [*ANNOUNCED["subjects"], "workspace.signatures"]}
    daemon_liveness.observe_heartbeat(_beat())
    daemon_liveness.observe_heartbeat(_beat("d2", abilities=more))
    assert daemon_liveness.announces("workspace.signatures") is None
    assert daemon_liveness.announces("workspace.execute") is True
    daemon_liveness.reset()
    daemon_liveness.observe_heartbeat(_beat(), now=time.monotonic() - 3600)
    daemon_liveness.observe_heartbeat(_beat("d2", abilities=more))
    assert daemon_liveness.announces("workspace.signatures") is True


class _Recorder:
    def __init__(self):
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append(subject)

        class NoRespondersError(Exception):
            pass

        raise NoRespondersError("nats: no responders available for request")


def _signature_evidence(monkeypatch):
    from services.tools import signature_lookup

    monkeypatch.setattr(
        signature_lookup.signatures,
        "wanted",
        lambda _evidence, _files: (
            [{"path": "shop/price.py", "names": ["price"]}],
            False,
        ),
    )
    return {"workspace_root": "/p", "workspace_writes": []}, [
        {"path": "app.py", "content": "from shop.price import price\n"}
    ]


def test_signatures_are_not_asked_of_daemons_that_announce_they_lack_them(monkeypatch):
    """WHEN the daemons announce no ``workspace.signatures`` THEN the backend
    does not ask, and records why from their claim, not as an older daemon."""
    from services.tools import signature_lookup
    from services.workspace import daemon_liveness

    daemon_liveness.observe_heartbeat(_beat())
    evidence, files = _signature_evidence(monkeypatch)
    nats = _Recorder()
    record = asyncio.run(signature_lookup.observe(nats, evidence, files))
    assert nats.requests == []
    assert record["observed"] is False
    assert "does not announce workspace.signatures" in record["reason"]
    assert "older daemon" not in record["reason"]


@pytest.mark.parametrize(
    "abilities,words",
    [
        (None, "(an older daemon)"),
        (
            {**ANNOUNCED, "subjects": ["workspace.signatures"]},
            "although the daemons announce it",
        ),
    ],
)
def test_nobody_answering_is_a_guess_only_without_an_announcement(
    monkeypatch, abilities, words
):
    from services.tools import signature_lookup
    from services.workspace import daemon_liveness

    daemon_liveness.observe_heartbeat(_beat(abilities=abilities))
    evidence, files = _signature_evidence(monkeypatch)
    nats = _Recorder()
    record = asyncio.run(signature_lookup.observe(nats, evidence, files))
    assert nats.requests == ["workspace.signatures"]
    assert words in record["reason"]


@pytest.mark.parametrize(
    "abilities,subject,words",
    [
        (None, "workspace.execute", "daemon from before full logs"),
        (ANNOUNCED, "workspace.app.check", "does not announce full_log on"),
        (ANNOUNCED, "workspace.execute", "although it announces full_log"),
    ],
)
def test_a_reply_without_its_full_log_is_worded_from_the_announcement(
    abilities, subject, words
):
    from services.workspace import daemon_liveness, run_logs

    daemon_liveness.observe_heartbeat(_beat(abilities=abilities))
    spec = {"bucket": "FABRIQUE_RUN_LOGS", "name": "run-abc"}
    record = asyncio.run(run_logs.observe(None, spec, {"success": True}, subject))
    assert record == {"observed": False, "reason": record["reason"]}
    assert words in record["reason"]


@pytest.mark.parametrize(
    "abilities,asked", [(ANNOUNCED, []), (None, ["workspace.hash"])]
)
def test_acceptance_files_are_hashed_unless_the_daemons_announce_no_hash(
    abilities, asked
):
    from services.loop import delivery_checks
    from services.workspace import daemon_liveness

    daemon_liveness.observe_heartbeat(_beat(abilities=abilities))
    evidence = {
        "workspace_root": "/p",
        "acceptance": {"test_hashes": {"tests/test_a.py": "0" * 64}},
    }
    nats = _Recorder()
    asyncio.run(delivery_checks.guard_test_files(nats, None, evidence, 1.0))
    assert nats.requests == asked
    assert "tampered" not in evidence["acceptance"]
