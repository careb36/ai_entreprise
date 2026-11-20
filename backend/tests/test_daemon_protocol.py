"""
The workspace daemon's protocol, from the daemon's side (issue #511, item 4).

- Every subject the daemon serves, consumes or publishes has a JSON Schema
  under ``vscode-daemon/protocol/schemas/v1/``, and every schema names one of
  them: the spec cannot drift from the code in either direction.
- Every conformance fixture (``vscode-daemon/protocol/fixtures/v1/``) is
  replayed against the real handlers with a temporary workspace, and the reply
  must be the recorded one (values that vary by machine or run are listed in
  the fixture's ``volatile`` and checked by type).
- ``workspace.status`` and the heartbeat report the protocol version.

The backend's side of the same fixtures is ``test_daemon_protocol_client.py``.
Design credit: AixleHQ/flow's ``runner/protocol/`` (Apache-2.0; design only).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "vscode-daemon" / "protocol"
SCHEMAS = PROTOCOL / "schemas" / "v1"
FIXTURES = PROTOCOL / "fixtures" / "v1"
WORKSPACE = "$WORKSPACE"


# ---------------------------------------------------------------------------
# Shared with test_daemon_protocol_client.py
# ---------------------------------------------------------------------------


def schemas() -> dict[str, dict[str, Any]]:
    """subject -> schema document."""
    out = {}
    for path in sorted(SCHEMAS.glob("*.json")):
        doc = json.loads(path.read_text())
        out[doc["x-subject"]] = doc
    return out


def validator(doc: dict[str, Any], part: str) -> Draft202012Validator:
    """A validator for ``$defs/<part>`` of a subject's schema document."""
    return Draft202012Validator({**doc, "$ref": f"#/$defs/{part}"})


def fixtures() -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{p.parent.name}/{p.stem}", json.loads(p.read_text()))
        for p in sorted(FIXTURES.glob("*/*.json"))
    ]


def substitute(value: Any, root: str) -> Any:
    """The fixture's ``$WORKSPACE`` replaced by *root*, everywhere in *value*."""
    if isinstance(value, str):
        return value.replace(WORKSPACE, root)
    if isinstance(value, list):
        return [substitute(v, root) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, root) for k, v in value.items()}
    return value


def _walk(doc: Any, pointer: str) -> tuple[Any, Any]:
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]
    parent = doc
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    last = parts[-1]
    return parent, int(last) if isinstance(parent, list) else last


def blank_volatile(reply: Any, pointers: list[str]) -> Any:
    """*reply* with each volatile value replaced by its JSON type name."""
    reply = json.loads(json.dumps(reply))
    for pointer in pointers:
        parent, key = _walk(reply, pointer)
        parent[key] = f"<{type(parent[key]).__name__}>"
    return reply


# ---------------------------------------------------------------------------
# The daemon, driven in-process
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, subject: str, body: Any, reply: str = "_INBOX.backend.fixture"):
        self.subject = subject
        self.data = json.dumps(body).encode()
        self.reply = reply


class _ObjectStore:
    """The JetStream object store the daemon uploads full logs to (``run_logs``)."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put(self, name, data, **_kwargs):
        self.objects[name] = data.read()


class _JetStream:
    def __init__(self, store: _ObjectStore):
        self.store = store
        self.buckets: list[str] = []

    async def object_store(self, bucket):
        self.buckets.append(bucket)
        return self.store


class _NC:
    def __init__(self):
        self.published: list[tuple[str, Any]] = []
        self.store = _ObjectStore()

    async def publish(self, subject, data, **_kwargs):
        self.published.append((subject, json.loads(data)))

    def jetstream(self):
        return _JetStream(self.store)


def prepare_workspace(doc: dict[str, Any], root: Path) -> None:
    for relative, text in (doc.get("workspace") or {}).get("files", {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def replay(doc: dict[str, Any], root: Path) -> Any:
    """What the daemon answers (or publishes) for the fixture, in *root*,
    with the fixture's ``daemon_env`` (the daemon machine's own settings,
    such as ``FABRIQUE_SANDBOX_REQUIRED``) and none of those otherwise."""
    with pytest.MonkeyPatch.context() as patch:
        for name in DAEMON_ENV:
            patch.delenv(name, raising=False)
        # The daemon keeps state (the repository map's cache, the delivery
        # journal) under the home directory by default. A runner reuses
        # pytest's numbered temp paths across runs, so a fixture replayed
        # in a path an earlier run used would find that run's cache and
        # answer ``cached: true``. Each replay gets its own state.
        state = root.parent / "daemon-state"
        patch.setenv("FABRIQUE_MAP_STATE", str(state / "maps"))
        patch.setenv("FABRIQUE_DAEMON_STATE", str(state))
        for name, value in (doc.get("daemon_env") or {}).items():
            assert name in DAEMON_ENV, f"not a daemon setting a fixture sets: {name}"
            patch.setenv(name, value)
        return _replay(doc, root)


# The daemon machine's settings a fixture may set (``daemon_env``).
DAEMON_ENV = ("FABRIQUE_SANDBOX_REQUIRED", "FABRIQUE_SANDBOX_RUNTIME")


def _replay(doc: dict[str, Any], root: Path) -> Any:
    import daemon
    import daemon_status

    prepare_workspace(doc, root)
    nc = _NC()
    if doc["subject"] == daemon_status.HEARTBEAT_SUBJECT:
        asyncio.run(daemon_status.heartbeat_loop(nc, interval=20.0, beats=1))
        return nc.published[0][1]
    handlers = dict(daemon.subscriptions(nc, lambda: True))
    msg = _Msg(doc["subject"], substitute(doc["request"], str(root)))
    asyncio.run(handlers[doc["subject"]](msg))
    replies = [body for subject, body in nc.published if subject == msg.reply]
    assert len(replies) == 1, nc.published
    return replies[0]


def daemon_subjects() -> set[str]:
    """Every subject the daemon serves, consumes from JetStream or publishes."""
    import daemon
    import daemon_status
    import nats_scope

    served = {subject for subject, _ in daemon.subscriptions(_NC())}
    return served | {
        nats_scope.DELIVER_SUBJECT,
        nats_scope.DELIVERED_SUBJECT,
        daemon_status.HEARTBEAT_SUBJECT,
        "glassroom.*",
    }


# ---------------------------------------------------------------------------
# The schemas and the subjects
# ---------------------------------------------------------------------------


def test_every_subject_has_a_schema_and_every_schema_a_subject():
    subjects = daemon_subjects()
    documented = set(schemas())
    assert subjects - documented == set(), "subjects without a schema"
    assert documented - subjects == set(), (
        "schemas for subjects the daemon does not use"
    )
    # Each handler module's HANDLERS are in the subscription table.
    import code_graph
    import console_run
    import host_folders
    import repo_map
    import sandbox_cache
    import signatures
    import workspace_edit
    import workspace_read

    for module in (
        code_graph,
        console_run,
        host_folders,
        repo_map,
        sandbox_cache,
        signatures,
        workspace_edit,
        workspace_read,
    ):
        assert {s for s, _ in module.HANDLERS} <= subjects, module.__name__


def test_no_subject_is_subscribed_twice():
    import daemon

    served = [subject for subject, _ in daemon.subscriptions(_NC())]
    assert len(served) == len(set(served))


@pytest.mark.parametrize("subject", sorted(schemas()))
def test_each_schema_is_valid_and_names_its_parts(subject):
    doc = schemas()[subject]
    Draft202012Validator.check_schema(doc)
    assert doc["x-protocol-version"] == 1
    assert doc["x-kind"] in {"request", "publish", "stream"}
    parts = set(doc["$defs"])
    if doc["x-kind"] == "request":
        assert {"request", "reply"} <= parts
    else:
        assert "message" in parts
    if "refusal" in parts:
        refusal = validator(doc, "reply")
        assert refusal.is_valid(
            {"error": {"status": 404, "message": "No such file: x"}}
        )


def test_the_spec_names_every_subject_and_the_version():
    import daemon_status

    spec = (PROTOCOL / "PROTOCOL.md").read_text()
    assert (
        f"The current protocol version is `{daemon_status.PROTOCOL_VERSION}`." in spec
    )
    for subject in daemon_subjects():
        assert f"`{subject}`" in spec, subject


# ---------------------------------------------------------------------------
# The fixtures, replayed against the handlers
# ---------------------------------------------------------------------------


def test_the_important_subjects_have_fixtures():
    covered = {doc["subject"] for _, doc in fixtures()}
    assert {
        "workspace.status",
        "workspace.heartbeat",
        "workspace.generate",
        "workspace.execute",
        "workspace.read",
        "workspace.list",
        "workspace.map",
        "workspace.related",
        "workspace.signatures",
        "workspace.sandbox.reset",
        "workspace.sandbox.image",
    } <= covered


@pytest.mark.parametrize("name,doc", fixtures(), ids=[n for n, _ in fixtures()])
def test_the_daemon_answers_each_fixture_as_recorded(name, doc, tmp_path):
    root = (tmp_path / "project").resolve()
    root.mkdir()
    actual = replay(doc, root)
    expected = substitute(doc["reply"], str(root))
    volatile = doc.get("volatile", [])
    assert blank_volatile(actual, volatile) == blank_volatile(expected, volatile), (
        f"{name}: the daemon's reply changed; actual:\n{json.dumps(actual, indent=2)}"
    )
    part = "reply" if doc["subject"] in _request_reply() else "message"
    errors = sorted(
        validator(schemas()[doc["subject"]], part).iter_errors(actual), key=str
    )
    assert not errors, [e.message for e in errors]


def _request_reply() -> set[str]:
    return {s for s, d in schemas().items() if d["x-kind"] == "request"}


# ---------------------------------------------------------------------------
# The version and the heartbeat
# ---------------------------------------------------------------------------


def test_status_reports_the_protocol_version_and_range():
    import daemon
    import daemon_status

    nc = _NC()
    handlers = dict(daemon.subscriptions(nc, lambda: False))
    asyncio.run(handlers["workspace.status"](_Msg("workspace.status", {})))
    body = nc.published[0][1]
    assert body["protocol_version"] == daemon_status.PROTOCOL_VERSION == 1
    assert body["protocol_min"] <= body["protocol_version"]
    assert body["daemon_id"] == daemon_status.DAEMON_ID
    assert body["jetstream"] is False
    assert body["version"] == daemon.DAEMON_VERSION


def test_the_heartbeat_counts_up_and_survives_a_failed_publish():
    import daemon_status

    sent: list[dict[str, Any]] = []
    calls = {"n": 0}

    class _Flaky:
        async def publish(self, subject, data):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ConnectionError("reconnecting")
            sent.append((subject, json.loads(data)))

    slept: list[float] = []

    async def _sleep(seconds):
        slept.append(seconds)

    asyncio.run(
        daemon_status.heartbeat_loop(_Flaky(), interval=5.0, beats=3, sleep=_sleep)
    )
    assert [s for s, _ in sent] == [daemon_status.HEARTBEAT_SUBJECT] * 2
    # The beat that failed keeps its number: the backend sees the gap.
    assert [b["sequence"] for _, b in sent] == [1, 3]
    assert {b["daemon_id"] for _, b in sent} == {daemon_status.DAEMON_ID}
    assert slept == [5.0, 5.0]


@pytest.mark.parametrize(
    "raw,expected", [("", 20.0), ("3", 3.0), ("0", 1.0), ("90", 25.0), ("soon", 20.0)]
)
def test_the_heartbeat_interval_is_bounded(monkeypatch, raw, expected):
    import daemon_status

    monkeypatch.setenv("FABRIQUE_HEARTBEAT_SECONDS", raw)
    assert daemon_status.heartbeat_seconds() == expected


def test_main_starts_the_heartbeat_and_cancels_it(monkeypatch):
    """``main`` subscribes every subject, then beats until it stops."""
    import daemon

    subscribed: list[str] = []
    beats: list[str] = []

    class _Conn:
        def jetstream(self):
            return object()

        async def subscribe(self, subject, queue=None, cb=None):
            subscribed.append(subject)

        async def publish(self, subject, data):
            beats.append(subject)

        async def drain(self):
            pass

    async def _connect():
        return _Conn()

    async def _consume(*_args):
        await asyncio.Event().wait()

    class _Stop(Exception):
        pass

    real_sleep = asyncio.sleep

    async def _sleep(seconds):
        await real_sleep(0)  # the heartbeat task runs its first beat
        if seconds == 3600:  # main's idle loop: stop here
            raise _Stop

    monkeypatch.setattr(daemon, "connect_with_retry", _connect)
    monkeypatch.setattr(daemon, "consume_queued_deliveries", _consume)
    monkeypatch.setattr(daemon.asyncio, "sleep", _sleep)
    with pytest.raises(_Stop):
        asyncio.run(daemon.main())
    assert set(subscribed) == {s for s, _ in daemon.subscriptions(_NC())}
    assert beats and set(beats) == {"workspace.heartbeat"}


# ---------------------------------------------------------------------------
# The abilities the daemon announces (#315, PROTOCOL.md §2.1)
# ---------------------------------------------------------------------------


def test_the_announced_subjects_are_the_subjects_the_daemon_serves():
    """WHEN the daemon announces its abilities THEN the subjects it names are
    exactly the request subjects it subscribes to: a claim kept true by a test."""
    import abilities
    import daemon

    served = {subject for subject, _ in daemon.subscriptions(_NC())}
    assert set(abilities.SUBJECTS) == served
    assert set(abilities.FEATURES) <= served


def test_status_and_every_heartbeat_announce_the_abilities():
    """WHEN the daemon connects THEN its first heartbeat and its status carry
    the versioned announcement."""
    import abilities
    import daemon
    import daemon_status

    nc = _NC()
    handlers = dict(daemon.subscriptions(nc, lambda: False))
    asyncio.run(handlers["workspace.status"](_Msg("workspace.status", {})))
    asyncio.run(daemon_status.heartbeat_loop(nc, interval=5.0, beats=1))
    status, beat = nc.published[0][1], nc.published[1][1]
    for body in (status, beat):
        announced = body["abilities"]
        assert announced == abilities.announcement()
        assert announced["abilities_version"] == abilities.ABILITIES_VERSION == 1
        assert "full_log" in announced["features"]["workspace.execute"]
        assert {"browser_probe", "security_probe"} <= set(
            announced["features"]["workspace.app.check"]
        )


def test_the_announcement_never_carries_a_hash_or_a_run():
    """The announcement is about the daemon, never about a run: no hash, no
    exit code, no output (rule 6 of docs/POSITIONING.md)."""
    import abilities

    text = json.dumps(abilities.announcement())
    for word in ("sha256", "digest", "exit_code", "stdout", "observed"):
        assert word not in text
