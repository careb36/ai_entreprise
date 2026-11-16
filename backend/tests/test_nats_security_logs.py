"""No secret of the bus in the logs or ``/health`` (issue #327).

The backend and the daemon connect with the options and log no password, and
``/health`` reports the bus without one.

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands, so the backend and the daemon each connect as their own user
(``NATS_USER``/``NATS_PASSWORD``, ``NATS_CREDS_FILE`` or ``NATS_NKEY_SEED``),
optionally over TLS (``NATS_TLS``), and the broker's configuration
(``scripts/nats_config.py`` → ``nats/nats-server.conf``) lets each do only
what it must. The shared fixtures are in ``nats_security_helpers``.
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import nats_auth
import nats_connect
import nats_security_helpers
from nats_security_helpers import SECRET

# The shared fixtures.
_clean_env = nats_security_helpers._clean_env


class _Captured(logging.Handler):
    """Collects a logger's records directly: other tests may disable or detach loggers."""

    def __init__(self, name: str):
        super().__init__(logging.DEBUG)
        self.logger = logging.getLogger(name)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def __enter__(self):
        self.level, self.disabled = self.logger.level, self.logger.disabled
        self.global_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.logger.disabled = False
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self)
        return self

    def __exit__(self, *_exc):
        self.logger.removeHandler(self)
        self.logger.setLevel(self.level)
        self.logger.disabled = self.disabled
        logging.disable(self.global_disable)


def test_the_backend_connects_with_the_options_and_logs_no_secret(monkeypatch):
    from nats_client import NATSClient

    monkeypatch.setenv("NATS_USER", "backend")
    monkeypatch.setenv("NATS_PASSWORD", SECRET)
    connect = AsyncNATSConnect()
    client = NATSClient(nats_url=f"nats://backend:{SECRET}@broker:4222")
    with (
        patch("nats_client.nats.connect", new=connect),
        _Captured("fabrique-nats") as caplog,
    ):
        asyncio.run(client.connect())
    assert connect.kwargs["user"] == "backend"
    assert connect.kwargs["password"] == SECRET
    assert connect.kwargs["inbox_prefix"] == "_INBOX.backend"
    assert SECRET not in caplog.text
    assert "auth: password" in caplog.text
    assert client.security == {"authenticated": True, "auth": "password", "tls": False}


def test_the_daemon_connects_with_the_options_and_logs_no_secret(monkeypatch):
    monkeypatch.setenv("NATS_USER", "daemon")
    monkeypatch.setenv("NATS_PASSWORD", SECRET)
    connect = AsyncNATSConnect(failures=1)
    with (
        patch("nats.connect", new=connect),
        patch("nats_connect.asyncio.sleep", new=AsyncMock()),
        _Captured("fabrique-daemon") as caplog,
    ):
        asyncio.run(
            nats_connect.connect_with_retry(f"nats://daemon:{SECRET}@broker:4222")
        )
    assert connect.kwargs["user"] == "daemon"
    assert connect.kwargs["inbox_prefix"] == "_INBOX.daemon"
    assert connect.kwargs["max_reconnect_attempts"] == -1
    assert SECRET not in caplog.text
    assert "retrying" in caplog.text and "auth: password" in caplog.text


def test_health_reports_the_bus_without_a_secret(monkeypatch, test_client):
    from app.main import app

    monkeypatch.setenv("NATS_USER", "backend")
    monkeypatch.setenv("NATS_PASSWORD", SECRET)
    monkeypatch.setenv("NATS_TLS", "true")
    deadline = time.monotonic() + 2.0
    while getattr(app.state, "nats_state", None) != "connected":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    previous = app.state.nats_client
    app.state.nats_client = type(
        "Client",
        (),
        {
            "nc": type("NC", (), {"is_connected": True})(),
            "security": nats_auth.describe(),
        },
    )()
    try:
        response = test_client.get("/health")
    finally:
        app.state.nats_client = previous
    assert response.json()["nats"] == {
        "state": "connected",
        "connected": True,
        "jetstream": response.json()["jetstream"],
        "authenticated": True,
        "tls": True,
    }
    assert SECRET not in response.text


class AsyncNATSConnect:
    """Stands in for ``nats.connect``: fails *failures* times, then records the call."""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.kwargs = None

    async def __call__(self, url, **kwargs):
        if self.failures:
            self.failures -= 1
            raise ConnectionRefusedError(f"refused {kwargs.get('user')}")
        self.kwargs = kwargs
        return object()
