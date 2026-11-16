"""What the tests of the Developer's agent-computer interface share (issue #359).

The real daemon loaded from ``vscode-daemon/daemon.py``, a small project in
``tmp_path``, an edit through the daemon, and synchronous NATS fakes that hand
each request to the daemon's functions. The tests are ``test_agent_tools*.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import workspace_edit  # the daemon's modules, on sys.path via conftest
import workspace_read
from services.tools import developer_tools

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
SESSION = "ab" * 16


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_aci", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon():
    return _load_daemon()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "src" / "greet.py").write_text(
        "def greet(name):\n    return f'Hello, {name}!'\n\n\nVERSION = 1\n"
    )
    (root / "src" / "config.json").write_text('{"debug": false}\n')
    (root / "src" / "notes.txt").write_text("one\ntwo\n")
    (root / ".env").write_text("TOKEN=x\n")
    (root / "src" / "big.py").write_text("".join(f"x_{i} = {i}\n" for i in range(250)))
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edit(daemon, root, **data):
    return workspace_edit.edit_file(
        str(root), {"session": SESSION, **data}, daemon._resolve_target_path
    )


class _Requester:
    """NATS, synchronously: each subject handed to the daemon's function."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.subjects = []

    def __call__(self, subject, payload, timeout):
        self.subjects.append(subject)
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        try:
            if subject == "workspace.read":
                return workspace_read.read_file(
                    root, payload.get("path"), payload.get("start_line"), resolve
                )
            if subject == "workspace.search":
                return workspace_read.search(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
            if subject == "workspace.find":
                return workspace_read.find_files(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
            if subject == "workspace.edit":
                return workspace_edit.edit_file(root, payload, resolve)
            if subject == "workspace.edit.finish":
                return workspace_edit.finish(root, payload.get("session"), resolve)
        except workspace_read.ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        raise AssertionError(subject)


def _call(name, **arguments):
    return {
        "id": f"c_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class _DaemonOverNATS:
    jetstream_enabled = False

    def __init__(self, daemon):
        self.daemon = daemon

    async def request(self, subject, payload, timeout=5.0):
        assert subject == "workspace.generate", subject
        return {
            "ok": True,
            "files": self.daemon.write_files(
                payload["workspace_root"], payload["files"]
            ),
        }

    async def publish(self, subject, payload, msg_id=None):
        pass
