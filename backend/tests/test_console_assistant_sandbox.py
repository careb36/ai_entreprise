"""
Where the console assistants run (issue #525): the company's sandbox.

- The request carries the company's sandbox with purpose ``assistant``, no
  cache, the network setting and only the variable names of ``sandbox_env``
  and ``console_env``; a host company runs as before.
- A contained sandbox without network, or a company whose sandbox cannot be
  read, asks no assistant; the reason reaches the Glassroom.
- A reply showing the daemon's sandbox stopped the CLI (its runtime missing,
  the CLI not in the image, the network off) is skipped with the reason and a
  cooldown; the task goes on.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_console_assistant_sandbox.py -q
"""

# The fixtures below are imported from the assistants' own tests and named as
# test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811

import pytest
from services.producers import console_assistants
from test_console_assistants import (  # noqa: F401 - fixtures
    ARCHITECT,
    DEV,
    _collect,
    _Daemon,
    _state,
    configured,
    daemon_with_both,
    db,
)

FORCED_CONTAINER = {
    "mode": "container",
    "forced_by_daemon": True,
    "isolated": True,
    "network": "off",
    "env": ["ASSISTANT_PROVIDER_KEY"],
}


def _with_sandbox(session, company, mode, sandbox_env=None, console_env=None):
    company.delivery_sandbox = mode
    company.sandbox_env = sandbox_env
    company.console_env = console_env
    session.add(company)
    session.commit()


def _decisions(nats):
    return [
        (p["assistant_label"], p["assistant_decision"], p["assistant_detail"])
        for s, p in nats.published
        if s == "glassroom.5"
    ]


def test_a_host_company_sends_its_sandbox_and_runs_as_before(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "host", ["CI_FLAG"], ["ASSISTANT_PROVIDER_KEY"])
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    applied = {"mode": "host", "forced_by_daemon": False, "isolated": False}
    nats = _Daemon(
        {"copilot": {"success": True, "stdout": "Advice.", "sandbox": applied}}
    )

    result = _collect(nats, {"id": 21, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nAdvice."
    box = nats.executed[0]["sandbox"]
    assert box["mode"] == "host" and box["purpose"] == "assistant"
    assert box["cache"] is False and box["network"] is False
    # Names only, never values: the daemon reads them from its own environment.
    assert box["env"] == ["CI_FLAG", "ASSISTANT_PROVIDER_KEY"]
    assert "command_policy" not in nats.executed[0]
    used = [p for s, p in nats.published if p["assistant_decision"] == "used"]
    assert used[0]["assistant_sandbox"] == applied


def test_a_contained_company_without_network_skips_with_the_reason(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "container")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    nats = _Daemon({})

    item = {"id": 22, "title": "Design the storage layer"}
    result = _collect(nats, item, ARCHITECT, company.id)

    assert result == {"combined": "", "sources": []}, "the task goes on"
    assert nats.executed == [], "nothing is sent, nothing runs on the host"
    decisions = _decisions(nats)
    assert [d[:2] for d in decisions] == [
        ("GitHub Copilot CLI", "skipped_sandbox"),
        ("Claude CLI", "skipped_sandbox"),
    ]
    assert "DELIVERY_SANDBOX_NETWORK" in decisions[0][2]
    assert _state(session, company.id, "copilot") is None, "no cooldown: a setting"


def test_a_contained_company_with_network_sends_the_container(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "container", None, ["ASSISTANT_PROVIDER_KEY"])
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    applied = {**FORCED_CONTAINER, "forced_by_daemon": False, "network": "on"}
    nats = _Daemon(
        {"copilot": {"success": True, "stdout": "Advice.", "sandbox": applied}}
    )

    result = _collect(nats, {"id": 23, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nAdvice."
    box = nats.executed[0]["sandbox"]
    assert box["mode"] == "container" and box["network"] is True
    assert box["purpose"] == "assistant"
    assert box["env"] == ["ASSISTANT_PROVIDER_KEY"]


@pytest.mark.parametrize(
    "answer,reason",
    [
        (
            {
                "success": False,
                "exit_code": 127,
                "stderr": "copilot: not found",
                "sandbox": {**FORCED_CONTAINER, "network": "on"},
            },
            "the CLI is not installed in the container sandbox the daemon requires",
        ),
        (
            {
                "success": False,
                "exit_code": 1,
                "stderr": "connection refused",
                "sandbox": FORCED_CONTAINER,
            },
            "the container sandbox the daemon requires has the network off",
        ),
        (
            {
                "success": False,
                "error": "sandbox unavailable: container mode needs Docker or Podman",
            },
            "the daemon could not apply its sandbox: container mode needs Docker",
        ),
    ],
)
def test_what_the_daemon_s_sandbox_stopped_is_skipped_with_the_reason(
    configured, db, daemon_with_both, answer, reason
):
    session, company = db
    _with_sandbox(session, company, "host")
    nats = _Daemon({"copilot": answer})

    result = _collect(nats, {"id": 24, "title": "Rename a field"}, DEV, company.id)

    assert result == {"combined": "", "sources": []}
    decisions = _decisions(nats)
    assert [d[1] for d in decisions] == ["requested", "skipped_sandbox"]
    assert decisions[1][2].startswith(reason)
    skipped = [
        p for s, p in nats.published if p["assistant_decision"] == "skipped_sandbox"
    ]
    assert skipped[0].get("assistant_sandbox") == answer.get("sandbox")
    state = _state(session, company.id, "copilot")
    assert state.cooldown_until is not None and state.last_error.startswith(reason)


def test_a_company_whose_sandbox_cannot_be_read_asks_no_assistant(
    configured, db, daemon_with_both, monkeypatch
):
    from services.workspace.sandbox import PolicyUnavailable

    _session, company = db

    def _unreadable(sprint, engine=None):
        raise PolicyUnavailable("the company's sandbox could not be read (Error)")

    monkeypatch.setattr(
        console_assistants.assistant_sandbox, "request_for", _unreadable
    )
    nats = _Daemon({})

    result = _collect(nats, {"id": 25, "title": "Rename a field"}, DEV, company.id)

    assert result == {"combined": "", "sources": []} and nats.executed == []
    assert [d[1] for d in _decisions(nats)] == ["skipped_sandbox"]
    assert "known sandbox" in _decisions(nats)[0][2]


def test_the_protocol_fixture_s_assistant_reply_is_read_as_advice(
    configured, db, daemon_with_both
):
    import json
    from pathlib import Path

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "vscode-daemon/protocol/fixtures/v1/workspace.execute"
            / "assistant-on-the-host.json"
        ).read_text()
    )
    session, company = db
    _with_sandbox(session, company, "host")
    nats = _Daemon({"copilot": fixture["reply"]})

    result = _collect(nats, {"id": 26, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nadvice"
