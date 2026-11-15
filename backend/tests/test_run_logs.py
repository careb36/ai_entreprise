"""Full logs as evidence (issue #315): the daemon keeps each stream of an
install, a test run or an application check whole (up to its cap), uploads it
to a JetStream object store, and the backend reads it back and computes the
sha256 itself (rule 6 of ``docs/POSITIONING.md``: never a hash from the party
recorded). An older daemon, the setting off or no JetStream leave
``full_log: {observed: false, reason}``.

The daemon's side runs real commands; the object store is a fake with the
nats-py interface (``put``, ``get_info``, ``get(writeinto=...)``). A live run
against a real ``nats-server`` with JetStream and the generated permissions
is at the end, when a binary is available."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import app_run
import pytest
import run_logs as daemon_logs  # the daemon's module, on sys.path via conftest
import sandbox
import shell_exec
from config import settings
from services.workspace import run_logs

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable

_spec = importlib.util.spec_from_file_location(
    "nats_config", REPO / "scripts" / "nats_config.py"
)
nats_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nats_config)


# ---------------------------------------------------------------------------
# A fake object store with nats-py's interface
# ---------------------------------------------------------------------------


class DigestMismatchError(Exception):
    pass


class FakeStore:
    def __init__(self, fail_put: Exception | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_put = fail_put
        self.corrupt: set[str] = set()

    async def put(self, name, data, **_kwargs):
        if self.fail_put:
            raise self.fail_put
        self.objects[name] = data.read() if hasattr(data, "read") else bytes(data)

    async def get_info(self, name):
        if name not in self.objects:
            raise KeyError(name)
        return SimpleNamespace(size=len(self.objects[name]))

    async def get(self, name, writeinto=None):
        data = self.objects[name]
        for start in range(0, len(data), 7):
            writeinto.write(data[start : start + 7])
        if name in self.corrupt:
            raise DigestMismatchError()


class FakeJS:
    def __init__(self, store):
        self.store = store
        self.buckets: list[str] = []

    async def object_store(self, bucket):
        self.buckets.append(bucket)
        return self.store


class DaemonNC:
    def __init__(self, store):
        self.js = FakeJS(store)

    def jetstream(self):
        return self.js


class BackendNC:
    """The backend's NATS client as ``run_logs`` reads it."""

    def __init__(self, store, bucket="FABRIQUE_RUN_LOGS"):
        self.js = FakeJS(store)
        self.run_logs_bucket = bucket


SPEC = {"bucket": "FABRIQUE_RUN_LOGS", "name": "run-abc"}


# ---------------------------------------------------------------------------
# The daemon keeps every byte up to its cap
# ---------------------------------------------------------------------------


def test_a_spool_keeps_up_to_its_cap_and_counts_everything():
    spool = daemon_logs.Spool(limit=10)
    spool.write(b"0123456")
    spool.write(b"789abcdef")
    assert (spool.kept, spool.total, spool.truncated) == (10, 16, True)
    spool.file.seek(0)
    assert spool.file.read() == b"0123456789"
    spool.close()


def test_the_cap_follows_the_daemon_s_setting_within_bounds(monkeypatch):
    monkeypatch.setenv("FABRIQUE_FULL_LOG_MAX_MB", "2")
    assert daemon_logs.max_bytes() == 2 * daemon_logs.MIB
    monkeypatch.setenv("FABRIQUE_FULL_LOG_MAX_MB", "0")
    assert daemon_logs.max_bytes() == daemon_logs.MIB
    monkeypatch.setenv("FABRIQUE_FULL_LOG_MAX_MB", "nope")
    assert daemon_logs.max_bytes() == 50 * daemon_logs.MIB


@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"full_log": "yes"},
        {"full_log": {"bucket": "FABRIQUE_RUN_LOGS"}},
        {"full_log": {"bucket": "a.b", "name": "run-1"}},
        {"full_log": {"bucket": "B", "name": "../other"}},
        {"full_log": {"bucket": "B", "name": "x" * 65}},
    ],
)
def test_only_a_well_formed_request_asks_for_a_full_log(request_body):
    assert daemon_logs.wanted(request_body) is None


def test_the_tail_stays_bounded_while_the_full_log_keeps_everything(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OUTPUT_BYTES", 100)
    script = (
        "import sys; sys.stdout.write('x' * 5000 + 'END'); sys.stderr.write('e' * 10)"
    )
    run = sandbox.run_bounded([PY, "-c", script], 30, keep_full=True)
    assert len(run["stdout"]) == 100 and run["output_truncated"]
    spools = run[daemon_logs.KEY]
    spools["stdout"].file.seek(0)
    whole = spools["stdout"].file.read()
    assert whole == b"x" * 5000 + b"END" and not spools["stdout"].truncated
    assert spools["stderr"].kept == 10
    daemon_logs.discard(run)
    plain = sandbox.run_bounded([PY, "-c", "print(1)"], 30)
    assert daemon_logs.KEY not in plain


def test_a_host_run_uploads_both_streams_and_never_a_hash(tmp_path):
    store = FakeStore()
    result = shell_exec.execute_shell_command(
        f"{PY} -c \"import sys; print('out'); sys.stderr.write('err')\"",
        str(tmp_path),
        keep_full=True,
    )
    asyncio.run(daemon_logs.upload(DaemonNC(store), SPEC, result))
    assert daemon_logs.KEY not in result
    assert store.objects == {"run-abc/stdout": b"out\n", "run-abc/stderr": b"err"}
    full = result["full_log"]
    assert full["bucket"] == "FABRIQUE_RUN_LOGS"
    assert [o["object"] for o in full["objects"]] == [
        "run-abc/stdout",
        "run-abc/stderr",
    ]
    assert full["objects"][0] == {
        "stream": "stdout",
        "object": "run-abc/stdout",
        "bytes": 4,
        "total_bytes": 4,
        "truncated": False,
    }
    assert "sha256" not in str(full) and "digest" not in str(full)


def test_a_sandboxed_run_keeps_its_streams_for_the_upload(tmp_path, monkeypatch):
    calls = []

    def fake_bounded(argv, timeout, keep_full=False, **_kw):
        calls.append(keep_full)
        result = {
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "timed_out": False,
            "output_truncated": False,
        }
        daemon_logs.keep(result, "stdout", daemon_logs.spool_of("ok"))
        return result

    monkeypatch.setattr(sandbox, "run_bounded", fake_bounded)
    monkeypatch.setattr(sandbox, "bwrap_command", lambda box, command, cwd: ["true"])
    result = shell_exec.execute_shell_command(
        "true", str(tmp_path), sandbox={"mode": "bwrap"}, keep_full=True
    )
    assert calls == [True] and set(result[daemon_logs.KEY]) == {"stdout"}
    daemon_logs.discard(result)


def test_an_upload_that_fails_is_said_in_the_reply(tmp_path):
    result = shell_exec.execute_shell_command("echo hi", str(tmp_path), keep_full=True)
    store = FakeStore(fail_put=RuntimeError("bucket full"))
    asyncio.run(daemon_logs.upload(DaemonNC(store), SPEC, result))
    assert result["full_log"]["error"] == "RuntimeError: bucket full"
    assert result["full_log"]["objects"] == []
    assert daemon_logs.KEY not in result


def test_a_refused_command_has_nothing_to_upload(tmp_path):
    result = shell_exec.execute_shell_command(
        "rm -rf /",
        str(tmp_path),
        policy={"mode": "deny", "deny": ["rm"]},
        keep_full=True,
    )
    asyncio.run(daemon_logs.upload(DaemonNC(FakeStore()), SPEC, result))
    assert result["refused"] and result["full_log"]["objects"] == []
    assert "did not run" in result["full_log"]["reason"]


def test_without_a_request_nothing_is_uploaded_and_the_reply_is_unchanged(tmp_path):
    result = shell_exec.execute_shell_command("echo hi", str(tmp_path), keep_full=True)
    store = FakeStore()
    asyncio.run(daemon_logs.upload(DaemonNC(store), None, result))
    assert store.objects == {} and "full_log" not in result
    assert daemon_logs.KEY not in result


def test_the_daemon_handler_uploads_the_execute_output(tmp_path):
    import daemon

    store = FakeStore()
    nc = SimpleNamespace(published=[], js=FakeJS(store))

    async def publish(subject, data, **_kw):
        nc.published.append((subject, data))

    nc.publish = publish
    nc.jetstream = lambda: nc.js
    body = {
        "command": "echo hello",
        "workspace_root": str(tmp_path),
        "full_log": SPEC,
    }
    msg = SimpleNamespace(
        subject="workspace.execute",
        data=__import__("json").dumps(body).encode(),
        reply="_INBOX.backend.x",
    )
    asyncio.run(daemon.handle_msg(nc, msg))
    reply = __import__("json").loads(nc.published[-1][1])
    assert reply["full_log"]["objects"][0]["bytes"] == 6
    assert store.objects["run-abc/stdout"] == b"hello\n"


# ---------------------------------------------------------------------------
# The application check keeps its log and its e2e output whole
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_application_check_uploads_its_log_and_e2e_output(tmp_path):
    (tmp_path / "index.html").write_text("hi")
    store = FakeStore()
    msg = SimpleNamespace(data=b"", reply="r")
    request = {
        "workspace_root": str(tmp_path),
        "start_command": f"{PY} -m http.server $PORT --bind 127.0.0.1",
        "port": _free_port(),
        "e2e_command": f"{PY} -c \"print('e2e ran')\"",
        "ready_timeout_seconds": 20,
        "full_log": SPEC,
    }
    replies = []

    async def reply(_nc, _msg, body):
        replies.append(body)

    asyncio.run(
        app_run.handle_app_check(DaemonNC(store), msg, reply, lambda _m: request)
    )
    (body,) = replies
    assert body["listening"] and body["e2e"]["exit_code"] == 0, body
    streams = {o["stream"] for o in body["full_log"]["objects"]}
    assert streams == {"log", "e2e.stdout", "e2e.stderr"}
    assert store.objects["run-abc/e2e.stdout"] == b"e2e ran\n"
    assert b"GET / " in store.objects["run-abc/log"]
    assert daemon_logs.KEY not in body


# ---------------------------------------------------------------------------
# The backend reads each object back and hashes it itself
# ---------------------------------------------------------------------------


@pytest.fixture()
def full_logs_on(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_FULL_LOGS", True)
    monkeypatch.setattr(settings, "DELIVERY_FULL_LOG_MAX_MB", 50)


def test_the_request_asks_for_a_fresh_name_only_with_a_bucket(
    full_logs_on, monkeypatch
):
    spec = run_logs.request_spec(BackendNC(FakeStore()))
    assert spec["bucket"] == "FABRIQUE_RUN_LOGS" and spec["name"].startswith("run-")
    assert run_logs.request_field(spec) == {"full_log": spec}
    assert run_logs.request_spec(BackendNC(FakeStore()))["name"] != spec["name"]
    no_js = SimpleNamespace(js=None, run_logs_bucket=None)
    assert "JetStream" in run_logs.request_spec(no_js)["reason"]
    assert run_logs.request_field(run_logs.request_spec(no_js)) == {}
    monkeypatch.setattr(settings, "DELIVERY_FULL_LOGS", False)
    assert (
        "DELIVERY_FULL_LOGS" in run_logs.request_spec(BackendNC(FakeStore()))["reason"]
    )


def test_the_backend_computes_the_sha256_from_the_bytes_it_read(full_logs_on):
    store = FakeStore()
    store.objects = {"run-abc/stdout": b"hello\n" * 100, "run-abc/stderr": b""}
    reply = {
        "full_log": {
            "bucket": "FABRIQUE_RUN_LOGS",
            "objects": [
                # A hash in the daemon's reply is never read.
                {
                    "stream": "stdout",
                    "object": "run-abc/stdout",
                    "bytes": 600,
                    "total_bytes": 900,
                    "truncated": True,
                    "sha256": "0" * 64,
                },
                {
                    "stream": "stderr",
                    "object": "run-abc/stderr",
                    "bytes": 0,
                    "total_bytes": 0,
                    "truncated": False,
                },
            ],
        }
    }
    record = asyncio.run(run_logs.observe(BackendNC(store), SPEC, reply))
    assert record["observed"] and record["observed_by"] == "backend"
    out, err = record["objects"]
    assert out["sha256"] == hashlib.sha256(b"hello\n" * 100).hexdigest()
    assert out["size"] == 600 and out["truncated"] and out["total_bytes"] == 900
    assert out["object"] == "run-abc/stdout" and "size_differs_from_daemon" not in out
    assert err["sha256"] == hashlib.sha256(b"").hexdigest() and err["size"] == 0


def test_a_size_that_differs_from_the_daemon_s_count_is_flagged(full_logs_on):
    store = FakeStore()
    store.objects = {"run-abc/stdout": b"abc"}
    reply = {
        "full_log": {
            "bucket": "B",
            "objects": [
                {
                    "stream": "stdout",
                    "object": "run-abc/stdout",
                    "bytes": 10,
                    "total_bytes": 10,
                    "truncated": False,
                }
            ],
        }
    }
    (obj,) = asyncio.run(run_logs.observe(BackendNC(store), SPEC, reply))["objects"]
    assert obj["size"] == 3 and obj["size_differs_from_daemon"]


def test_another_run_s_object_is_never_read(full_logs_on):
    store = FakeStore()
    store.objects = {"run-other/stdout": b"secret"}
    reply = {
        "full_log": {
            "bucket": "B",
            "objects": [
                {
                    "stream": "stdout",
                    "object": "run-other/stdout",
                    "bytes": 6,
                    "total_bytes": 6,
                    "truncated": False,
                }
            ],
        }
    }
    (obj,) = asyncio.run(run_logs.observe(BackendNC(store), SPEC, reply))["objects"]
    assert obj["observed"] is False and "sha256" not in obj


def test_an_object_over_the_backend_s_cap_is_not_read(full_logs_on, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_FULL_LOG_MAX_MB", 1)
    store = FakeStore()
    store.objects = {"run-abc/stdout": b"x" * (run_logs.MIB + 1)}
    reply = {
        "full_log": {
            "bucket": "B",
            "objects": [
                {
                    "stream": "stdout",
                    "object": "run-abc/stdout",
                    "bytes": 1,
                    "total_bytes": 1,
                    "truncated": False,
                }
            ],
        }
    }
    (obj,) = asyncio.run(run_logs.observe(BackendNC(store), SPEC, reply))["objects"]
    assert obj["observed"] is False and "DELIVERY_FULL_LOG_MAX_MB" in obj["reason"]


def test_a_metadata_digest_that_disagrees_is_flagged_and_ours_kept(full_logs_on):
    store = FakeStore()
    store.objects = {"run-abc/stdout": b"data"}
    store.corrupt = {"run-abc/stdout"}
    reply = {
        "full_log": {
            "bucket": "B",
            "objects": [
                {
                    "stream": "stdout",
                    "object": "run-abc/stdout",
                    "bytes": 4,
                    "total_bytes": 4,
                    "truncated": False,
                }
            ],
        }
    }
    (obj,) = asyncio.run(run_logs.observe(BackendNC(store), SPEC, reply))["objects"]
    assert (
        obj["metadata_digest_mismatch"]
        and obj["sha256"] == hashlib.sha256(b"data").hexdigest()
    )


@pytest.mark.parametrize(
    "reply,words",
    [
        ({"success": True}, "daemon from before full logs"),
        (
            {"full_log": {"bucket": "B", "objects": [], "error": "Timeout"}},
            "could not upload it: Timeout",
        ),
        (
            {"full_log": {"bucket": "B", "objects": [], "reason": "it did not run"}},
            "it did not run",
        ),
        ("not a dict", "daemon from before full logs"),
    ],
)
def test_without_objects_the_full_log_is_not_observed_with_the_reason(
    full_logs_on, reply, words
):
    record = asyncio.run(run_logs.observe(BackendNC(FakeStore()), SPEC, reply))
    assert record["observed"] is False and words in record["reason"]


def test_a_read_that_fails_is_not_observed(full_logs_on):
    reply = {
        "full_log": {
            "bucket": "B",
            "objects": [
                {
                    "stream": "stdout",
                    "object": "run-abc/stdout",
                    "bytes": 1,
                    "total_bytes": 1,
                    "truncated": False,
                }
            ],
        }
    }
    record = asyncio.run(run_logs.observe(BackendNC(FakeStore()), SPEC, reply))
    assert record["observed"] is False and "could not read it back" in record["reason"]


def test_no_request_means_not_observed_with_why(full_logs_on):
    record = asyncio.run(
        run_logs.observe(
            BackendNC(FakeStore()), {"reason": "full logs are turned off"}, {}
        )
    )
    assert record == {"observed": False, "reason": "full logs are turned off"}


# ---------------------------------------------------------------------------
# Through the delivery checks: the test run and the install record full_log
# ---------------------------------------------------------------------------


class RecordingNC(BackendNC):
    def __init__(self, store, reply):
        super().__init__(store)
        self.reply = reply
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        name = payload["full_log"]["name"]
        self.js.store.objects[f"{name}/stdout"] = b"1 passed\n"
        return {
            **self.reply,
            "full_log": {
                "bucket": payload["full_log"]["bucket"],
                "objects": [
                    {
                        "stream": "stdout",
                        "object": f"{name}/stdout",
                        "bytes": 9,
                        "total_bytes": 9,
                        "truncated": False,
                    }
                ],
            },
        }


def test_the_delivery_test_run_records_the_full_log(full_logs_on, monkeypatch):
    from services.loop import delivery_checks
    from services.workspace import project_profile
    from services.workspace import sandbox as backend_sandbox

    monkeypatch.setattr(
        backend_sandbox, "request_for_sprint", lambda *_a, **_k: {"mode": "host"}
    )
    nats = RecordingNC(
        FakeStore(),
        {"success": True, "stdout": "1 passed\n", "stderr": "", "exit_code": 0},
    )
    evidence = {"workspace_root": "/project"}
    ctx = project_profile.Context(nats, {"id": 1, "company_id": 1}, evidence, None)

    async def command():
        return "pytest -q", None

    ctx.test_command = command
    asyncio.run(
        delivery_checks._run_tests(
            nats, {"id": 1, "company_id": 1}, evidence, None, ctx
        )
    )
    ((subject, payload),) = nats.requests
    assert (
        subject == "workspace.execute"
        and payload["full_log"]["bucket"] == "FABRIQUE_RUN_LOGS"
    )
    full = evidence["test_run"]["full_log"]
    assert (
        full["observed"]
        and full["objects"][0]["sha256"] == hashlib.sha256(b"1 passed\n").hexdigest()
    )
    assert evidence["ci"]["conclusion"] == "success"


# ---------------------------------------------------------------------------
# The broker lets the daemon write logs there and nowhere else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("namespace", ["", "e2e"])
def test_the_daemon_may_write_to_the_log_bucket_only(namespace):
    bucket = nats_config.run_log_bucket(namespace)
    daemon = nats_config.daemon_permissions(namespace)
    allowed = lambda subject: nats_config.allowed(daemon, "publish", subject)
    assert allowed(f"$O.{bucket}.C.abc123")
    assert allowed(f"$O.{bucket}.M.cnVuLWFiYy9zdGRvdXQ=")
    assert allowed(f"$JS.API.STREAM.INFO.OBJ_{bucket}")
    assert allowed(f"$JS.API.STREAM.MSG.GET.OBJ_{bucket}")
    assert not allowed(f"$JS.API.STREAM.PURGE.OBJ_{bucket}")
    assert not allowed(f"$JS.API.STREAM.DELETE.OBJ_{bucket}")
    assert not allowed(f"$JS.API.STREAM.UPDATE.OBJ_{bucket}")
    assert not allowed("$O.OTHER_BUCKET.C.abc")
    assert not allowed("$JS.API.STREAM.INFO.OBJ_OTHER_BUCKET")
    backend = nats_config.backend_permissions(namespace)
    assert nats_config.allowed(backend, "publish", "$JS.FC.OBJ_X.abc.1")


def test_the_bucket_drops_its_oldest_objects_and_expires_them():
    from nats.js import api as js_api
    from nats_client import _run_log_config

    config = _run_log_config(30, 4096)
    assert config.name == "OBJ_FABRIQUE_RUN_LOGS"
    assert config.discard == js_api.DiscardPolicy.OLD
    assert config.max_age == 30 * 24 * 3600 and config.max_bytes == 4096 * 1024 * 1024
    assert config.allow_rollup_hdrs
    forever = _run_log_config(0, 0)
    assert forever.max_age is None and forever.max_bytes == -1


# ---------------------------------------------------------------------------
# Live: a real nats-server with JetStream and the generated permissions
# ---------------------------------------------------------------------------


def _nats_server() -> str | None:
    return os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")


@pytest.mark.skipif(_nats_server() is None, reason="no nats-server binary")
def test_live_the_daemon_uploads_and_the_backend_reads_back_and_hashes(
    tmp_path, full_logs_on
):
    import json

    import nats
    import nats_client

    port = _free_port()
    conf = tmp_path / "nats.conf"
    conf.write_text(nats_config.render(nats_config.build()))
    env = {
        **os.environ,
        "NATS_BACKEND_PASSWORD": "backend-pw",
        "NATS_DAEMON_PASSWORD": "daemon-pw",
    }
    server = subprocess.Popen(
        [
            _nats_server(),
            "-p",
            str(port),
            "-js",
            "-sd",
            str(tmp_path / "js"),
            "-c",
            str(conf),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)

        async def scenario():
            url = f"nats://127.0.0.1:{port}"
            backend = nats_client.NATSClient(url)
            backend.nc = await nats.connect(
                url,
                user="backend",
                password="backend-pw",
                inbox_prefix="_INBOX.backend",
            )
            assert await backend.enable_jetstream()
            assert backend.run_logs_bucket == "FABRIQUE_RUN_LOGS"
            daemon_nc = await nats.connect(
                url, user="daemon", password="daemon-pw", inbox_prefix="_INBOX.daemon"
            )
            try:
                spec = run_logs.request_spec(backend)
                result = shell_exec.execute_shell_command(
                    f"{PY} -c \"print('line ' * 50000)\"", str(tmp_path), keep_full=True
                )
                await daemon_logs.upload(daemon_nc, spec, result)
                assert "error" not in result["full_log"], result["full_log"]
                record = await run_logs.observe(
                    backend, spec, json.loads(json.dumps(result))
                )
                expected = (("line " * 50000) + "\n").encode()
                out = record["objects"][0]
                assert record["observed"], record
                assert out["sha256"] == hashlib.sha256(expected).hexdigest() and out[
                    "size"
                ] == len(expected)
                # The daemon's user may not purge the bucket.
                with pytest.raises(nats.errors.Error):
                    await daemon_nc.jetstream().purge_stream(f"OBJ_{spec['bucket']}")
            finally:
                await daemon_nc.close()
                await backend.nc.close()

        asyncio.run(asyncio.wait_for(scenario(), 60))
    finally:
        server.terminate()
        server.wait(timeout=10)
