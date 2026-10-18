"""
Console assistants run only when the daemon reports them (issue #103).

The backend asks the daemon once, on ``workspace.capabilities``, which
assistant CLIs it has.  When the answer is empty or the request fails, the task
path must not send any assistant ``workspace.execute`` request (each would wait
out the full timeout); when it lists ``claude``, exactly one request is sent.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_console_capabilities.py -q
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import config
import pytest
from services import console_capabilities, task_worker

ITEM = {"id": 11, "title": "Design the storage layer", "description": "Pick a schema."}
# Architect is a "high value" role, so the default strategy selects both
# GitHub Copilot CLI and Claude CLI before the capability filter applies.
AGENT = {"role": "Architect", "name": "Ada"}
SPRINT = {"id": 5, "company_id": 3, "name": "Sprint 5"}


class _FakeNATSClient:
    def __init__(self, capabilities_reply=None, capabilities_error=None):
        self.capabilities_reply = capabilities_reply
        self.capabilities_error = capabilities_error
        self.requests: list[tuple[str, dict, float]] = []
        self.publish = AsyncMock()

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload, timeout))
        if subject == "workspace.capabilities":
            if self.capabilities_error:
                raise self.capabilities_error
            return self.capabilities_reply
        return {"success": True, "stdout": "advice from the assistant"}

    def execute_requests(self):
        return [r for r in self.requests if r[0] == "workspace.execute"]


@pytest.fixture(autouse=True)
def _isolated_task_path(monkeypatch):
    console_capabilities.reset()
    monkeypatch.setattr(task_worker, "CONSOLE_ASSISTANTS_ENABLED", True)
    monkeypatch.setattr(
        task_worker, "_resolve_workspace_root", lambda company_id: "/tmp/workspace"
    )
    monkeypatch.setattr(
        task_worker,
        "_get_assistant_cooldown_until",
        lambda company_id, assistant_key: None,
    )
    monkeypatch.setattr(task_worker, "_publish_console_assistant_event", AsyncMock())
    yield
    console_capabilities.reset()


async def _run_task_path(nats):
    return await task_worker._collect_console_assistant_advice(
        nats, ITEM, AGENT, SPRINT
    )


@pytest.mark.asyncio
async def test_empty_capabilities_skip_assistants(caplog):
    nats = _FakeNATSClient(capabilities_reply={"assistants": []})
    await console_capabilities.probe_console_assistants(nats)

    with caplog.at_level(logging.INFO):
        first = await _run_task_path(nats)
        second = await _run_task_path(nats)

    assert nats.execute_requests() == []
    assert first == {"combined": "", "sources": []}
    assert second == first
    skip_logs = [
        r for r in caplog.records if "Skipping console assistants" in r.getMessage()
    ]
    assert len(skip_logs) == 1, "skip must be logged once, not per task"


@pytest.mark.asyncio
async def test_capabilities_timeout_skips_assistants():
    nats = _FakeNATSClient(capabilities_error=asyncio.TimeoutError())
    assert await console_capabilities.probe_console_assistants(nats) is None

    await _run_task_path(nats)

    assert nats.execute_requests() == []


@pytest.mark.asyncio
async def test_unprobed_capabilities_skip_assistants():
    nats = _FakeNATSClient()

    await _run_task_path(nats)

    assert nats.execute_requests() == []


@pytest.mark.asyncio
async def test_claude_capability_sends_one_request():
    nats = _FakeNATSClient(capabilities_reply={"assistants": ["claude"]})
    await console_capabilities.probe_console_assistants(nats)

    result = await _run_task_path(nats)

    executes = nats.execute_requests()
    assert len(executes) == 1
    _, payload, timeout = executes[0]
    assert "claude" in payload["command"]
    assert "copilot" not in payload["command"]
    assert timeout == task_worker.CONSOLE_ASSISTANT_TIMEOUT
    assert [source["label"] for source in result["sources"]] == ["Claude CLI"]


@pytest.mark.asyncio
async def test_probe_asks_daemon_once_with_short_timeout():
    nats = _FakeNATSClient(capabilities_reply={"assistants": ["copilot", "claude"]})

    assert await console_capabilities.probe_console_assistants(nats) == [
        "copilot",
        "claude",
    ]

    assert len(nats.requests) == 1
    subject, payload, timeout = nats.requests[0]
    assert subject == "workspace.capabilities"
    assert payload == {"commands": ["copilot", "claude"]}
    assert timeout <= 2.0


def test_console_assistant_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("CONSOLE_ASSISTANT_TIMEOUT_SECONDS", "12.5")
    assert config.Settings().CONSOLE_ASSISTANT_TIMEOUT_SECONDS == 12.5
    monkeypatch.delenv("CONSOLE_ASSISTANT_TIMEOUT_SECONDS")
    assert config.Settings().CONSOLE_ASSISTANT_TIMEOUT_SECONDS == 90.0
