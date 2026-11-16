"""The bus's connection options (issue #327).

Both components' connect options from the environment (none, user and
password, a creds file winning, an nkey seed, a missing file named, TLS with a
pinned CA and a client certificate), each component's reply inbox, the
warning for an exposed broker without credentials, the daemon waiting for the
backend's streams, the daemon image's modules, and compose handing each
container its client certificate.

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands, so the backend and the daemon each connect as their own user
(``NATS_USER``/``NATS_PASSWORD``, ``NATS_CREDS_FILE`` or ``NATS_NKEY_SEED``),
optionally over TLS (``NATS_TLS``), and the broker's configuration
(``scripts/nats_config.py`` → ``nats/nats-server.conf``) lets each do only
what it must. The shared fixtures are in ``nats_security_helpers``.
"""

import asyncio
import re
import ssl
from unittest.mock import AsyncMock, patch

import nats_auth
import nats_connect
import nats_security_helpers
import pytest
from nats_security_helpers import MODULES, REPO, SECRET, nats_config

# The shared fixtures.
_clean_env = nats_security_helpers._clean_env
certs = nats_security_helpers.certs


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
