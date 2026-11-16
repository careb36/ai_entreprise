"""The bus's rules against a real ``nats-server`` (issue #327).

With the generated configuration the daemon answers the backend and consumes
its stream but is refused commands and stream changes, cannot answer itself
into a command, and TLS needs the pinned CA. These tests run only with a
``nats-server`` binary (on ``PATH`` or ``NATS_SERVER_BIN``).

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands, so the backend and the daemon each connect as their own user
(``NATS_USER``/``NATS_PASSWORD``, ``NATS_CREDS_FILE`` or ``NATS_NKEY_SEED``),
optionally over TLS (``NATS_TLS``), and the broker's configuration
(``scripts/nats_config.py`` → ``nats/nats-server.conf``) lets each do only
what it must. The shared fixtures are in ``nats_security_helpers``.
"""

import asyncio
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

import nats_auth
import nats_connect
import nats_security_helpers
import pytest
from nats_security_helpers import REPO

# The shared fixtures.
_clean_env = nats_security_helpers._clean_env
certs = nats_security_helpers.certs


def _server_binary() -> str | None:
    return os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")


live = pytest.mark.skipif(not _server_binary(), reason="nats-server not available")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _broker(tmp_path: Path, extra: list[str] | None = None):
    port = _free_port()
    env = {
        **os.environ,
        "NATS_BACKEND_PASSWORD": "backend-pw",
        "NATS_DAEMON_PASSWORD": "daemon-pw",
    }
    process = subprocess.Popen(
        [
            _server_binary(),
            "-c",
            str(REPO / "nats" / "nats-server.conf"),
            "-js",
            "-sd",
            str(tmp_path / "js"),
            "-a",
            "127.0.0.1",
            "-p",
            str(port),
            *(extra or []),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return process, port
        time.sleep(0.05)
    process.kill()
    raise RuntimeError("nats-server did not start")


@live
def test_live_the_daemon_can_answer_but_not_command(tmp_path, monkeypatch):
    import nats
    from nats_client import NATSClient

    process, port = _broker(tmp_path)
    url = f"nats://127.0.0.1:{port}"

    async def scenario():
        monkeypatch.setenv("NATS_USER", "backend")
        monkeypatch.setenv("NATS_PASSWORD", "backend-pw")
        backend = NATSClient(url)
        await backend.connect()
        assert await backend.enable_jetstream()

        errors = []

        async def on_error(e):
            errors.append(str(e))

        daemon_env = {"NATS_USER": "daemon", "NATS_PASSWORD": "daemon-pw"}
        daemon = await nats.connect(
            url, error_cb=on_error, **nats_connect.connect_options(daemon_env)
        )

        async def answer(msg):
            await msg.respond(json.dumps({"ok": True}).encode())

        await daemon.subscribe("workspace.status", cb=answer)
        await daemon.flush()
        reply = await backend.request("workspace.status", {}, timeout=2)
        assert reply == {"ok": True}

        # The daemon consumes its stream through its own inbox.
        js = daemon.jetstream()
        await nats_connect.wait_for_streams(js, ["FABRIQUE_DELIVERIES"], interval=0.1)
        psub = await js.pull_subscribe(
            "workspace.deliver",
            durable="workspace-daemon",
            stream="FABRIQUE_DELIVERIES",
            inbox_prefix=nats_connect.PULL_INBOX_PREFIX,
        )
        await backend.publish("workspace.deliver", {"job_id": "j1"}, msg_id="j1")
        msgs = await psub.fetch(1, timeout=3)
        assert json.loads(msgs[0].data) == {"job_id": "j1"}
        await msgs[0].ack()
        await js.publish("workspace.delivered", b"{}")

        # What it may not do.
        await daemon.publish("workspace.execute", b'{"command": "id"}')
        await daemon.publish("task.assign", b"{}")
        await daemon.flush()
        await asyncio.sleep(0.2)
        assert any("workspace.execute" in e for e in errors), errors
        with pytest.raises(Exception):  # noqa: B017 - Refused by the broker.
            await js.add_stream(name="ROGUE", subjects=["rogue.>"])
        with pytest.raises(Exception):  # noqa: B017
            await js.delete_stream("FABRIQUE_TASKS")

        await daemon.close()
        await backend.close()

        # A wrong password does not get in.
        with pytest.raises(Exception):  # noqa: B017
            await nats.connect(
                url,
                user="daemon",
                password="wrong",
                allow_reconnect=False,
                max_reconnect_attempts=0,
            )

    try:
        asyncio.run(scenario())
    finally:
        process.kill()
        process.wait()


@live
def test_live_the_daemon_cannot_answer_itself_into_a_command(tmp_path):
    """Regression: publish with reply ``workspace.execute``, hear it, answer it.

    With ``allow_responses`` the broker let a holder of the daemon's
    credentials answer the reply subject of a message it had published
    itself, which sent a command to the real daemon. The answer must be
    refused, while the backend's requests still get their replies.
    """
    import nats

    process, port = _broker(tmp_path)
    url = f"nats://127.0.0.1:{port}"
    daemon_env = {"NATS_USER": "daemon", "NATS_PASSWORD": "daemon-pw"}
    backend_env = {"NATS_USER": "backend", "NATS_PASSWORD": "backend-pw"}

    async def scenario():
        real = await nats.connect(url, **nats_connect.connect_options(daemon_env))
        commands = []

        async def on_command(msg):
            commands.append(msg.data)
            if msg.reply:
                await msg.respond(b'{"ok": true}')

        await real.subscribe("workspace.execute", cb=on_command)
        await real.flush()

        errors = []

        async def on_error(e):
            errors.append(str(e))

        attacker = await nats.connect(
            url, error_cb=on_error, **nats_connect.connect_options(daemon_env)
        )
        heard = asyncio.get_running_loop().create_future()

        async def on_own(msg):
            if not heard.done():
                heard.set_result(msg)

        await attacker.subscribe("workspace.delivered", cb=on_own)
        await attacker.flush()
        await attacker.publish("workspace.delivered", b"{}", reply="workspace.execute")
        own = await asyncio.wait_for(heard, 3)
        assert own.reply == "workspace.execute"
        await own.respond(b'{"command": "touch /tmp/pwned"}')
        await attacker.flush()
        await asyncio.sleep(0.3)
        assert commands == []
        assert any("workspace.execute" in e for e in errors), errors

        # The backend's own request still gets the daemon's answer.
        backend = await nats.connect(url, **nats_auth.connect_options(backend_env))
        reply = await backend.request(
            "workspace.execute", b'{"command": "id"}', timeout=2
        )
        assert json.loads(reply.data) == {"ok": True}
        assert commands == [b'{"command": "id"}']
        for nc in (backend, attacker, real):
            await nc.close()

    try:
        asyncio.run(scenario())
    finally:
        process.kill()
        process.wait()


@live
def test_live_tls_with_a_pinned_ca(tmp_path, monkeypatch, certs):
    import nats

    process, port = _broker(
        tmp_path,
        [
            "--tls",
            "--tlscert",
            str(certs["server"]),
            "--tlskey",
            str(certs["server_key"]),
        ],
    )
    url = f"nats://localhost:{port}"
    env = {
        "NATS_USER": "daemon",
        "NATS_PASSWORD": "daemon-pw",
        "NATS_TLS": "true",
        "NATS_TLS_CA": str(certs["ca"]),
    }

    async def scenario():
        nc = await nats.connect(url, **nats_connect.connect_options(env))
        assert nc.is_connected
        await nc.close()
        # Without the pinned CA the self-signed server is not trusted.
        with pytest.raises(Exception):  # noqa: B017
            await nats.connect(
                url,
                user="daemon",
                password="daemon-pw",
                tls=ssl.create_default_context(),
                allow_reconnect=False,
                max_reconnect_attempts=0,
                connect_timeout=2,
            )

    try:
        asyncio.run(scenario())
    finally:
        process.kill()
        process.wait()
