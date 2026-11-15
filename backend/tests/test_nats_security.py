"""
The bus is authenticated and encrypted when the deployment asks (issue #327).

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands, so the backend and the daemon each connect as their own user
(``NATS_USER``/``NATS_PASSWORD``, ``NATS_CREDS_FILE`` or ``NATS_NKEY_SEED``),
optionally over TLS (``NATS_TLS``), and the broker's configuration
(``scripts/nats_config.py`` → ``nats/nats-server.conf``) lets each do only
what it must. These tests pin the connection options of both components, that
no secret reaches a log or ``/health``, the warning for an exposed broker
without credentials, the generated configuration and what it refuses the
daemon, and ``nats/start.sh``. The live tests run only with a ``nats-server``
binary (on ``PATH`` or ``NATS_SERVER_BIN``).

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_nats_security.py -q
"""

import asyncio
import datetime
import importlib.util
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nats_auth
import nats_connect
import pytest

REPO = Path(__file__).resolve().parents[2]
SECRET = "s3cret-pass-word"

_spec = importlib.util.spec_from_file_location(
    "nats_config", REPO / "scripts" / "nats_config.py"
)
nats_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nats_config)

# Both components read the same variables the same way.
MODULES = pytest.mark.parametrize(
    "module", [nats_auth, nats_connect], ids=["backend", "daemon"]
)
ALL_VARS = (
    "NATS_USER",
    "NATS_PASSWORD",
    "NATS_CREDS_FILE",
    "NATS_NKEY_SEED",
    "NATS_TLS",
    "NATS_TLS_CA",
    "NATS_TLS_CERT",
    "NATS_TLS_KEY",
    "NATS_BIND",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Certificates for the TLS cases: a CA, a server and a client certificate.
# ---------------------------------------------------------------------------


def _write_certs(folder: Path) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)

    def key():
        return ec.generate_private_key(ec.SECP256R1())

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def write_key(k, path):
        path.write_bytes(
            k.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    ca_key = key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("fabrique-test-ca"))
        .issuer_name(name("fabrique-test-ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = {"ca": folder / "ca.crt"}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    import ipaddress

    for role, usage in (
        ("server", x509.ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", x509.ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        k = key()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name(f"fabrique-{role}"))
            .issuer_name(ca.subject)
            .public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        paths[role] = folder / f"{role}.crt"
        paths[f"{role}_key"] = folder / f"{role}.key"
        paths[role].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        write_key(k, paths[f"{role}_key"])
    return paths


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    return _write_certs(tmp_path_factory.mktemp("certs"))


# ---------------------------------------------------------------------------
# Connection options from the environment
# ---------------------------------------------------------------------------


@MODULES
def test_no_settings_keep_the_unauthenticated_connection(module):
    options = module.connect_options({})
    assert set(options) == {"inbox_prefix"}
    assert module.describe({}) == {"authenticated": False, "auth": "none", "tls": False}


@MODULES
def test_user_and_password(module):
    options = module.connect_options({"NATS_USER": "daemon", "NATS_PASSWORD": SECRET})
    assert options["user"] == "daemon"
    assert options["password"] == SECRET
    assert module.describe({"NATS_USER": "daemon", "NATS_PASSWORD": SECRET}) == {
        "authenticated": True,
        "auth": "password",
        "tls": False,
    }


@MODULES
def test_a_user_without_a_password_is_refused(module):
    with pytest.raises(module.NATSConfigError, match="NATS_PASSWORD"):
        module.connect_options({"NATS_USER": "daemon"})


@MODULES
def test_a_creds_file_wins_over_the_rest(module, tmp_path):
    creds = tmp_path / "daemon.creds"
    creds.write_text("-----BEGIN NATS USER JWT-----\n")
    seed = tmp_path / "daemon.nk"
    seed.write_text("SUAEXAMPLE")
    env = {
        "NATS_CREDS_FILE": str(creds),
        "NATS_NKEY_SEED": str(seed),
        "NATS_USER": "daemon",
        "NATS_PASSWORD": SECRET,
    }
    options = module.connect_options(env)
    assert options["user_credentials"] == str(creds)
    assert not {"nkeys_seed", "user", "password"} & set(options)
    assert module.describe(env)["auth"] == "creds"


@MODULES
def test_an_nkey_seed_file(module, tmp_path):
    seed = tmp_path / "daemon.nk"
    seed.write_text("SUAEXAMPLE")
    env = {"NATS_NKEY_SEED": str(seed), "NATS_USER": "daemon", "NATS_PASSWORD": SECRET}
    options = module.connect_options(env)
    assert options["nkeys_seed"] == str(seed)
    assert "user" not in options


@MODULES
@pytest.mark.parametrize("variable", ["NATS_CREDS_FILE", "NATS_NKEY_SEED"])
def test_a_missing_file_is_named(module, variable, tmp_path):
    missing = tmp_path / "nowhere.creds"
    with pytest.raises(module.NATSConfigError, match=re.escape(str(missing))) as error:
        module.connect_options({variable: str(missing)})
    assert variable in str(error.value)


@MODULES
def test_tls_with_a_pinned_ca_and_a_client_certificate(module, certs):
    env = {
        "NATS_TLS": "true",
        "NATS_TLS_CA": str(certs["ca"]),
        "NATS_TLS_CERT": str(certs["client"]),
        "NATS_TLS_KEY": str(certs["client_key"]),
    }
    context = module.connect_options(env)["tls"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # Only the pinned CA is trusted, not the system store.
    assert [c["subject"] for c in context.get_ca_certs()] == [
        ((("commonName", "fabrique-test-ca"),),)
    ]
    assert module.describe(env)["tls"] is True


@MODULES
def test_tls_settings_are_checked(module, certs, tmp_path):
    assert "tls" not in module.connect_options({"NATS_TLS_CA": str(certs["ca"])})
    with pytest.raises(module.NATSConfigError, match="go together"):
        module.connect_options({"NATS_TLS": "1", "NATS_TLS_CERT": str(certs["client"])})
    with pytest.raises(module.NATSConfigError, match="NATS_TLS_CA"):
        module.connect_options({"NATS_TLS": "1", "NATS_TLS_CA": str(tmp_path / "x")})


@MODULES
def test_each_component_gets_its_own_reply_inbox(module):
    prefix = module.connect_options({})["inbox_prefix"]
    owner = "backend" if module is nats_auth else "daemon"
    assert prefix == f"_INBOX.{owner}"
    rules = {
        "backend": nats_config.backend_permissions(),
        "daemon": nats_config.daemon_permissions(),
    }
    assert nats_config.allowed(rules[owner], "subscribe", f"{prefix}.abc.1")
    other = "daemon" if owner == "backend" else "backend"
    assert not nats_config.allowed(rules[other], "subscribe", f"{prefix}.abc.1")


@MODULES
def test_safe_url_drops_the_userinfo(module):
    assert (
        module.safe_url(f"nats://daemon:{SECRET}@broker:4222") == "nats://broker:4222"
    )
    assert module.safe_url("tls://broker:4222") == "tls://broker:4222"


# ---------------------------------------------------------------------------
# The exposed-broker warning
# ---------------------------------------------------------------------------


def test_the_backend_warns_when_the_broker_is_exposed_without_credentials():
    assert nats_auth.exposed_bind_warning({}) is None
    assert nats_auth.exposed_bind_warning({"NATS_BIND": "127.0.0.1"}) is None
    warning = nats_auth.exposed_bind_warning({"NATS_BIND": "0.0.0.0"})
    assert "0.0.0.0" in warning and "NATS_BACKEND_PASSWORD" in warning
    assert (
        nats_auth.exposed_bind_warning(
            {"NATS_BIND": "0.0.0.0", "NATS_USER": "backend", "NATS_PASSWORD": SECRET}
        )
        is None
    )


def test_the_daemon_warns_for_a_remote_broker_without_credentials():
    assert nats_connect.exposed_warning("nats://localhost:4222", {}) is None
    assert "broker.example" in nats_connect.exposed_warning(
        "nats://broker.example:4222", {}
    )
    assert nats_connect.exposed_warning(
        "nats://localhost:4222", {"NATS_BIND": "0.0.0.0"}
    )
    assert (
        nats_connect.exposed_warning(
            "nats://broker.example:4222",
            {"NATS_USER": "daemon", "NATS_PASSWORD": SECRET},
        )
        is None
    )


def test_startup_logs_the_warning(monkeypatch):
    import lifecycle

    monkeypatch.setenv("NATS_BIND", "0.0.0.0")
    started = []

    async def _never(*_args):
        started.append(True)

    app = type("App", (), {"state": type("State", (), {})()})()
    with (
        patch("lifecycle.prepare_database"),
        patch("lifecycle._connect_nats_and_start_workers", new=_never),
        patch("lifecycle.get_neo4j_driver", side_effect=Exception("stubbed")),
        patch.object(lifecycle.logger, "warning") as warn,
    ):

        async def _run():
            await lifecycle.startup(app)
            for task in list(lifecycle._background_tasks):
                task.cancel()

        asyncio.run(_run())
    assert any("NATS_BIND is 0.0.0.0" in str(c.args[0]) for c in warn.call_args_list)


# ---------------------------------------------------------------------------
# No secret in the logs or /health
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# The daemon waits for the backend's streams and never creates one
# ---------------------------------------------------------------------------


class _NotFound(Exception):
    pass


class _FakeJS:
    def __init__(self, missing_rounds: int):
        self.missing_rounds = missing_rounds
        self.info_calls = []
        self.subscribed = None

    async def stream_info(self, name):
        self.info_calls.append(name)
        if self.missing_rounds:
            self.missing_rounds -= 1
            raise _NotFound(name)
        return object()

    async def add_stream(self, *_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the daemon created a stream")

    async def pull_subscribe(self, subject, **kwargs):
        self.subscribed = {"subject": subject, **kwargs}
        raise asyncio.CancelledError  # stop the consumer once subscribed


def test_the_daemon_waits_for_the_streams_and_passes_its_inbox():
    import daemon
    import nats_scope

    js = _FakeJS(missing_rounds=2)
    state = {"jetstream": False}
    with (
        patch("nats_connect.asyncio.sleep", new=AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(daemon.consume_queued_deliveries(object(), js, state))
    assert set(js.info_calls) == set(nats_scope.DELIVERY_STREAMS)
    assert js.subscribed["inbox_prefix"] == b"_INBOX.daemon."
    assert js.subscribed["stream"] == nats_scope.DELIVERY_STREAM
    assert not hasattr(daemon, "ensure_delivery_streams")


def test_daemon_status_reads_the_live_jetstream_state():
    import daemon

    replies = []

    async def _reply(nc, msg, body):
        replies.append(body)

    state = {"jetstream": False}
    with patch.object(daemon, "_reply", new=_reply):
        asyncio.run(daemon.handle_status(None, None, lambda: state["jetstream"]))
        state["jetstream"] = True
        asyncio.run(daemon.handle_status(None, None, lambda: state["jetstream"]))
    assert [r["jetstream"] for r in replies] == [False, True]


def test_the_daemon_image_copies_every_module():
    dockerfile = (REPO / "vscode-daemon" / "Dockerfile").read_text()
    copied = set(
        re.search(r"^COPY (.+) /app/$", dockerfile, re.MULTILINE).group(1).split()
    )
    modules = {p.name for p in (REPO / "vscode-daemon").glob("*.py")}
    assert modules <= copied
    # nkeys comes from the hash-pinned lock (test_daemon_lock.py).
    assert "--require-hashes -r /app/requirements.txt" in dockerfile
    lock = (REPO / "vscode-daemon" / "requirements.txt").read_text()
    assert re.search(r"^nkeys==", lock, re.MULTILINE)


# ---------------------------------------------------------------------------
# The broker's configuration
# ---------------------------------------------------------------------------


def test_the_committed_configuration_matches_the_generator():
    committed = (REPO / "nats" / "nats-server.conf").read_text()
    assert committed == nats_config.render(nats_config.build())
    assert nats_config.parse(committed) == nats_config.build()
    assert nats_config.main(["--check"]) == 0


def test_check_fails_on_a_stale_file(tmp_path, capsys):
    stale = tmp_path / "nats-server.conf"
    stale.write_text("port: 4222\n")
    assert nats_config.main(["--check", "--output", str(stale)]) == 1
    assert "stale" in capsys.readouterr().err
    assert nats_config.main(["--output", str(stale)]) == 0
    assert nats_config.main(["--check", "--output", str(stale)]) == 0


def test_no_password_is_written_into_the_file():
    text = (REPO / "nats" / "nats-server.conf").read_text()
    assert "password: $NATS_BACKEND_PASSWORD" in text.replace('"password"', "password")
    users = nats_config.build()["authorization"]["users"]
    assert [u["password"] for u in users] == [
        "$NATS_BACKEND_PASSWORD",
        "$NATS_DAEMON_PASSWORD",
    ]


def _rules(user, namespace=""):
    users = nats_config.build(namespace)["authorization"]["users"]
    return next(u["permissions"] for u in users if u["user"] == user)


@pytest.mark.parametrize(
    "subject",
    [
        "workspace.execute",
        "workspace.generate",
        "workspace.deliver",
        "task.assign",
        "audit.write",
        "$JS.API.STREAM.CREATE.FABRIQUE_DELIVERIES",
        "$JS.API.STREAM.UPDATE.FABRIQUE_DELIVERIES",
        "$JS.API.STREAM.DELETE.FABRIQUE_TASKS",
        "$JS.API.STREAM.PURGE.FABRIQUE_DELIVERIES",
        "$JS.API.CONSUMER.DELETE.FABRIQUE_DELIVERIES.workspace-daemon",
        "$JS.API.CONSUMER.MSG.NEXT.FABRIQUE_TASKS.task-worker",
        "$JS.ACK.FABRIQUE_TASKS.task-worker.1.1.1.1.1",
        "glassroom.1.extra",
    ],
)
def test_the_daemon_may_not_publish(subject):
    assert not nats_config.allowed(_rules("daemon"), "publish", subject)


@pytest.mark.parametrize(
    "subject",
    [
        "workspace.delivered",
        "workspace.heartbeat",
        "glassroom.42",
        "$JS.API.STREAM.INFO.FABRIQUE_DELIVERIES",
        "$JS.API.STREAM.INFO.FABRIQUE_DELIVERY_RESULTS",
        "$JS.API.CONSUMER.INFO.FABRIQUE_DELIVERIES.workspace-daemon",
        "$JS.API.CONSUMER.DURABLE.CREATE.FABRIQUE_DELIVERIES.workspace-daemon",
        "$JS.API.CONSUMER.CREATE.FABRIQUE_DELIVERIES.workspace-daemon.workspace.deliver",
        "$JS.API.CONSUMER.MSG.NEXT.FABRIQUE_DELIVERIES.workspace-daemon",
        "$JS.ACK.FABRIQUE_DELIVERIES.workspace-daemon.1.5.5.1700000000.0",
    ],
)
def test_the_daemon_may_publish(subject):
    assert nats_config.allowed(_rules("daemon"), "publish", subject)


def test_the_daemon_answers_only_under_the_backends_inbox():
    rules = _rules("daemon")
    # No dynamic reply grant: it would let the daemon answer a reply subject
    # it set itself, such as workspace.execute (Copilot review on #382).
    assert "allow_responses" not in rules
    assert nats_config.allowed(rules, "publish", "_INBOX.backend.Xk3.17")
    for subject in ("workspace.execute", "workspace.generate", "_INBOX.daemon.x.1"):
        assert not nats_config.allowed(rules, "publish", subject)
    assert "allow_responses" not in (REPO / "nats" / "nats-server.conf").read_text()
    for subject in ("workspace.execute", "workspace.generate", "_INBOX.daemon.x.1"):
        assert nats_config.allowed(rules, "subscribe", subject)
    for subject in ("task.assign", "glassroom.1", "_INBOX.backend.x.1", "audit.write"):
        assert not nats_config.allowed(rules, "subscribe", subject)


def test_the_backend_sends_the_work_and_owns_jetstream():
    rules = _rules("backend")
    for subject in (
        "workspace.execute",
        "task.assign",
        "glassroom.3",
        "audit.write",
        "system.maintenance.ticket",
        "$JS.API.STREAM.CREATE.FABRIQUE_TASKS",
        "$JS.ACK.FABRIQUE_TASKS.task-worker.1.1.1.1.1",
    ):
        assert nats_config.allowed(rules, "publish", subject)
    assert not nats_config.allowed(rules, "subscribe", "_INBOX.daemon.x.1")


def test_a_namespace_scopes_the_daemon_to_its_own_stream():
    rules = _rules("daemon", "e2e")
    assert nats_config.allowed(rules, "publish", "e2e.workspace.delivered")
    assert not nats_config.allowed(rules, "publish", "workspace.delivered")
    assert nats_config.allowed(
        rules,
        "publish",
        "$JS.API.CONSUMER.MSG.NEXT.e2e_FABRIQUE_DELIVERIES.e2e_workspace-daemon",
    )
    assert not nats_config.allowed(
        rules,
        "publish",
        "$JS.API.CONSUMER.MSG.NEXT.FABRIQUE_DELIVERIES.workspace-daemon",
    )
    backend = _rules("backend", "e2e")
    assert nats_config.allowed(backend, "publish", "e2e.task.assign")
    assert nats_config.allowed(backend, "subscribe", "e2e.workspace.delivered")


def test_the_generator_follows_the_real_names(monkeypatch):
    """The stream, consumer and subject names come from the code that uses them."""
    import importlib

    import nats_client
    import nats_scope

    monkeypatch.setenv("NATS_NAMESPACE", "e2e")
    scope = importlib.reload(nats_scope)
    try:
        rules = _rules("daemon", "e2e")
        assert nats_config.allowed(rules, "publish", scope.DELIVERY_RESULT_SUBJECT)
        assert nats_config.allowed(
            rules,
            "publish",
            f"$JS.API.CONSUMER.MSG.NEXT.{scope.DELIVERY_STREAM}.{scope.DELIVERY_CONSUMER}",
        )
        for stream in scope.DELIVERY_STREAMS:
            assert nats_config.allowed(
                rules, "publish", f"$JS.API.STREAM.INFO.{stream}"
            )
        backend = _rules("backend", "e2e")
        for stream in nats_client.DURABLE_STREAMS:
            for subject in stream.subjects:
                assert nats_config.allowed(
                    backend, "publish", nats_client._scoped_subject(subject)
                )
    finally:
        monkeypatch.delenv("NATS_NAMESPACE")
        importlib.reload(nats_scope)


# ---------------------------------------------------------------------------
# nats/start.sh
# ---------------------------------------------------------------------------


def _start(env: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    fake = tmp_path / "fake-nats-server"
    fake.write_text('#!/bin/sh\necho "ARGS $*"\n')
    fake.chmod(0o755)
    base = {"PATH": os.environ.get("PATH", ""), "NATS_SERVER_BIN": str(fake)}
    base.update(
        {"NATS_CERTS_DIR": str(tmp_path / "certs"), "NATS_STORE_DIR": str(tmp_path)}
    )
    return subprocess.run(
        ["/bin/sh", str(REPO / "nats" / "start.sh")],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_start_sh_runs_unauthenticated_without_passwords(tmp_path):
    run = _start({}, tmp_path)
    assert run.returncode == 0
    assert "-c " not in run.stdout and "-js" in run.stdout
    assert "no credentials" in run.stderr


def test_start_sh_loads_the_users_with_both_passwords(tmp_path):
    run = _start(
        {"NATS_BACKEND_PASSWORD": SECRET, "NATS_DAEMON_PASSWORD": "x"}, tmp_path
    )
    assert run.returncode == 0
    assert "nats-server.conf" in run.stdout
    assert SECRET not in run.stdout + run.stderr


def test_start_sh_refuses_only_one_password(tmp_path):
    run = _start({"NATS_DAEMON_PASSWORD": SECRET}, tmp_path)
    assert run.returncode == 1
    assert "both" in run.stderr and "ARGS" not in run.stdout


def test_start_sh_turns_tls_on_when_certificates_exist(tmp_path):
    (tmp_path / "certs").mkdir()
    for name in ("server.crt", "server.key", "ca.crt"):
        (tmp_path / "certs" / name).write_text("x")
    run = _start({"NATS_TLS_VERIFY": "true"}, tmp_path)
    assert "--tls --tlscert" in run.stdout and "--tlsverify" in run.stdout


def _certs(tmp_path: Path, *names: str) -> None:
    (tmp_path / "certs").mkdir(exist_ok=True)
    for name in names:
        (tmp_path / "certs" / name).write_text("x")


def test_start_sh_without_certificates_or_a_request_runs_in_the_clear(tmp_path):
    run = _start({}, tmp_path)
    assert run.returncode == 0
    assert "--tls" not in run.stdout


def test_start_sh_turns_tls_on_from_the_files_without_verifying(tmp_path):
    _certs(tmp_path, "server.crt", "server.key", "ca.crt")
    run = _start({}, tmp_path)
    assert run.returncode == 0
    assert "--tls --tlscert" in run.stdout and "--tlsverify" not in run.stdout


@pytest.mark.parametrize(
    ("env", "files", "missing"),
    [
        ({"NATS_TLS": "true"}, (), "server.crt"),
        ({"NATS_TLS": "TRUE"}, ("ca.crt",), "server.crt"),
        ({"NATS_TLS_VERIFY": "true"}, (), "server.crt"),
        ({"NATS_TLS_VERIFY": "true"}, ("server.crt", "server.key"), "ca.crt"),
        (
            {"NATS_TLS": "true", "NATS_TLS_VERIFY": "true"},
            ("server.crt", "server.key"),
            "ca.crt",
        ),
        ({}, ("server.crt",), "only one"),
        ({"NATS_TLS": "true"}, ("server.key", "ca.crt"), "only one"),
    ],
)
def test_start_sh_refuses_tls_it_cannot_provide(tmp_path, env, files, missing):
    """TLS or client verification asked for and a file missing: no broker at all."""
    _certs(tmp_path, *files)
    run = _start(env, tmp_path)
    assert run.returncode == 1
    assert "ARGS" not in run.stdout
    assert missing in run.stderr and "refusing" in run.stderr


# ---------------------------------------------------------------------------
# Live: a real nats-server with the generated configuration
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# docker-compose.yml hands each container its client certificate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "owner"), [("backend", "BACKEND"), ("vscode-daemon", "DAEMON")]
)
def test_compose_forwards_the_client_certificate_inside_the_mounted_folder(
    service, owner
):
    import yaml

    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"][
        service
    ]
    env = compose["environment"]
    folder = "/etc/fabrique-nats/certs"
    assert f"./nats/certs:{folder}:ro" in compose["volumes"]
    assert env["NATS_TLS_CA"] == f"{folder}/ca.crt"
    for variable, suffix in (("NATS_TLS_CERT", "CERT"), ("NATS_TLS_KEY", "KEY")):
        name = f"NATS_{owner}_TLS_{suffix}"
        # Empty unless the file is named, then a path in the mounted folder.
        assert env[variable] == f"${{{name}:+{folder}/${{{name}}}}}"


def test_empty_forwarded_certificate_settings_mean_no_client_certificate(certs):
    env = {
        "NATS_TLS": "true",
        "NATS_TLS_CA": str(certs["ca"]),
        "NATS_TLS_CERT": "",
        "NATS_TLS_KEY": "",
    }
    for module in (nats_auth, nats_connect):
        assert isinstance(module.connect_options(env)["tls"], ssl.SSLContext)
