"""The broker's configuration and ``nats/start.sh`` (issue #327).

The committed ``nats/nats-server.conf`` matches the generator and holds no
password; the daemon may answer but not send a request, assign a task, read
the backend's replies or touch a stream; ``nats/start.sh`` with both, one or
no passwords and with certificates, refusing TLS it cannot provide.

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands, so the backend and the daemon each connect as their own user
(``NATS_USER``/``NATS_PASSWORD``, ``NATS_CREDS_FILE`` or ``NATS_NKEY_SEED``),
optionally over TLS (``NATS_TLS``), and the broker's configuration
(``scripts/nats_config.py`` → ``nats/nats-server.conf``) lets each do only
what it must. The shared fixtures are in ``nats_security_helpers``.
"""

import os
import subprocess
from pathlib import Path

import nats_security_helpers
import pytest
from nats_security_helpers import REPO, SECRET, nats_config

# The shared fixtures.
_clean_env = nats_security_helpers._clean_env


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
