"""A ``workspace.execute`` reply always fits one NATS message (``shell_exec.fit_result``).

A verbose test suite (``pytest -v`` over a large backend) printed more than
the server's payload limit: ``nc.publish`` raised ``MaxPayloadError``, the
reply was never sent, and the backend recorded "the workspace daemon did not
reply (TimeoutError)" instead of the exit it waited for (sprint 10, tasks 42
and 43). The fitted reply keeps each stream's start and end and names the
streams it cut.
"""

from __future__ import annotations

import asyncio
import json

import daemon
import shell_exec


def _size(result):
    return len(json.dumps(result).encode())


def test_a_reply_that_fits_is_unchanged():
    result = {"success": True, "stdout": "ok\n", "stderr": "", "exit_code": 0}
    assert shell_exec.fit_result(result, 1024) is result
    assert shell_exec.fit_result(result, 0) is result  # no limit known


def test_a_long_output_is_cut_to_fit_keeping_its_start_and_end():
    stdout = "collected 9000 items\n" + "tests/test_x.py::test_a PASSED\n" * 60000 + "== 1 failed, 8999 passed ==\n"
    result = {"success": False, "stdout": stdout, "stderr": "warning\n" * 20000, "exit_code": 1}
    fitted = shell_exec.fit_result(result, 1024 * 1024)
    assert _size(fitted) <= 1024 * 1024
    assert fitted["stdout"].startswith("collected 9000 items")
    assert fitted["stdout"].endswith("== 1 failed, 8999 passed ==\n")
    assert "characters left out" in fitted["stdout"]
    assert fitted["output_truncated"] == ["stdout", "stderr"]
    assert (fitted["success"], fitted["exit_code"]) == (False, 1)
    assert len(fitted["stdout"]) > 300_000  # the room is used, not thrown away
    assert result["stdout"] == stdout  # the caller's result is not changed


def test_a_short_stream_beside_a_long_one_is_kept_whole():
    result = {"success": False, "stdout": "x" * 2_000_000, "stderr": "Traceback: boom\n", "exit_code": 2}
    fitted = shell_exec.fit_result(result, 1024 * 1024)
    assert _size(fitted) <= 1024 * 1024
    assert fitted["stderr"] == "Traceback: boom\n"


def test_the_execute_handler_publishes_a_reply_under_the_limit(monkeypatch, tmp_path):
    big = {"success": True, "stdout": "y" * 3_000_000, "stderr": "", "exit_code": 0}
    monkeypatch.setattr(daemon.sprint_integration, "while_active", lambda root, fn, *a: dict(big))

    class Msg:
        subject = "workspace.execute"
        reply = "_INBOX.1"
        data = json.dumps({"command": "pytest -v", "workspace_root": str(tmp_path)}).encode()

    class Nc:
        max_payload = 1024 * 1024
        sent = []

        async def publish(self, subject, payload):
            if len(payload) > self.max_payload:
                raise AssertionError("over the payload limit")
            self.sent.append((subject, json.loads(payload)))

    nc = Nc()
    asyncio.run(daemon.handle_msg(nc, Msg()))
    subject, body = nc.sent[-1]
    assert subject == "_INBOX.1" and body["exit_code"] == 0
    assert body["output_truncated"] == ["stdout"]
