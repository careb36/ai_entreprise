"""What the tests of the Developer's workspace tools share.

The real daemon loaded from ``vscode-daemon/daemon.py``, a small project in
``tmp_path``, a synchronous NATS fake that hands each request to the daemon's
function, a fake model proxy that answers with tool calls and then with a
deliverable, and a fake MCP client. The tests are ``test_developer_tools*.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import workspace_read  # the daemon's module, on sys.path via conftest
from services.tools import developer_tools

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_tools", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "greet.py").write_text(
        "def greet(name):\n    return f'Hello, {name}!'\n"
    )
    (root / "src" / "count.py").write_text("COUNT = 0\n")
    (root / "tests" / "test_greet.py").write_text(
        "from src.greet import greet\n\n\ndef test_greet():\n    assert greet('a') == 'Hello, a!'\n"
    )
    (root / "README.md").write_text("# Proj\n")
    (root / "node_modules" / "x" / "index.js").write_text("module.exports = 1\n")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    return root


class _Requester:
    """NATS, synchronously: each subject handed to the daemon's function."""

    def __init__(self, daemon, commands=None):
        self.daemon = daemon
        self.calls = []
        self.commands = commands or {}

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        try:
            if subject == "workspace.list":
                return workspace_read.list_tree(
                    root, payload.get("path"), payload.get("depth"), resolve
                )
            if subject == "workspace.read":
                return workspace_read.read_file(
                    root, payload.get("path"), payload.get("start_line"), resolve
                )
            if subject == "workspace.search":
                return workspace_read.search(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
        except workspace_read.ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        if subject == "workspace.execute":
            return self.commands.get(
                payload["command"],
                {"exit_code": 1, "stdout": "no such command\n", "stderr": ""},
            )
        raise AssertionError(subject)


def _call(name, **arguments):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class _Model:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def chat(self, messages, model, role, bu_id=None, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _answer_with_calls(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                }
            }
        ]
    }


def _answer(text):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "served_model": "m",
    }


class _FakeMcp:
    """An MCP toolbox in shape only."""

    def __init__(self):
        self.unavailable = {"gone": "unreachable"}
        self.calls = []
        self.closed = False

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "tracker__search",
                    "description": "x",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def call(self, tool_call, round_no):
        self.calls.append(tool_call)
        return {
            "server": "tracker",
            "tool": "search",
            "is_error": False,
            "read_only": True,
        }, "found"

    def close(self):
        self.closed = True
