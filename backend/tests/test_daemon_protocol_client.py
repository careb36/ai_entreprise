"""
The workspace daemon's protocol, from the backend's side (issue #511, item 4).

- Every conformance fixture's request and reply are valid against the
  subject's schema, and the backend code that reads each reply reads the
  recorded one as intended.
- Version negotiation: a daemon outside the backend's supported range is
  incompatible with a reason, in ``GET /v1/workspace/status``, in
  ``daemon_request`` and in a delivery's evidence; one that reports no version
  is an older daemon, still served.
- Liveness from the heartbeat, timed on the backend's clock, with the daemon's
  own report kept apart; the request-based check as the fallback.

The daemon's side of the same fixtures is ``test_daemon_protocol.py``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
import worker_stages
from models.company import ProjectDiscovery
from services.contracts.manifest import expected_hashes
from services.evidence import signatures as evidence_signatures
from services.evidence.verification import evaluate_workspace_writes
from services.loop.worker import deliver as worker_deliver
from services.tools import code_graph, developer_tool_replies, repo_map
from services.workspace import daemon_liveness, workspace_daemon
from test_daemon_protocol import fixtures, schemas, substitute, validator
from test_task_delivery import (
    TWO_FILE_ANSWER,
    _DaemonOverNATS,
    _latest,
    _load_daemon,
    _task_fixture,
)

EXAMPLE_ROOT = "/home/dev/app"


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    """As in test_task_delivery: the task worker on the in-memory database."""
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    return sqlite_session


@pytest.fixture(autouse=True)
def fresh_observations():
    daemon_liveness.reset()
    yield
    daemon_liveness.reset()


def _fixture(subject: str, scenario: str) -> dict:
    return dict(fixtures())[f"{subject}/{scenario}"]


def _reply(subject: str, scenario: str) -> dict:
    return substitute(_fixture(subject, scenario)["reply"], EXAMPLE_ROOT)


class _Answers:
    """A NATS client whose requests get a recorded reply."""

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc = reply, exc
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append(subject)
        if self.exc is not None:
            raise self.exc
        return self.reply

    async def close(self):
        pass


def _heartbeat(**overrides) -> dict:
    return {**_fixture("workspace.heartbeat", "first-beat")["reply"], **overrides}


# ---------------------------------------------------------------------------
# The fixtures against the schemas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,doc", fixtures(), ids=[n for n, _ in fixtures()])
def test_each_fixture_is_valid_against_its_schema(name, doc):
    schema = schemas()[doc["subject"]]
    if "request" in doc:
        request = substitute(doc["request"], EXAMPLE_ROOT)
        assert not list(validator(schema, "request").iter_errors(request)), name
    part = "reply" if schema["x-kind"] == "request" else "message"
    errors = list(
        validator(schema, part).iter_errors(_reply(doc["subject"], name.split("/")[1]))
    )
    assert not errors, [e.message for e in errors]


def test_a_reply_that_breaks_the_schema_is_caught():
    """The schemas are not vacuous: a reply missing what the backend reads fails."""
    status = schemas()["workspace.status"]
    broken = {**_reply("workspace.status", "reports-the-protocol")}
    del broken["protocol_version"]
    assert list(validator(status, "reply").iter_errors(broken))
    read = schemas()["workspace.read"]
    assert list(validator(read, "reply").iter_errors({"path": "a.py", "sha256": "no"}))


# ---------------------------------------------------------------------------
# The fixtures against the code that reads them
# ---------------------------------------------------------------------------


def test_status_reply_is_connected_and_compatible():
    nats = _Answers(_reply("workspace.status", "reports-the-protocol"))
    presence = asyncio.run(workspace_daemon.daemon_presence(nats))
    assert presence["state"] == "connected"
    assert presence["protocol"]["state"] == "compatible"
    assert presence["protocol"]["negotiated"] == 1


def test_heartbeat_fixture_makes_the_daemon_online():
    daemon_liveness.observe_heartbeat(_reply("workspace.heartbeat", "first-beat"))
    beat = daemon_liveness.liveness()
    assert beat["state"] == "online"
    assert beat["daemons"][0]["reported_by_daemon"]["protocol_version"] == 1


def test_generate_reply_verifies_the_written_file():
    request = _fixture("workspace.generate", "writes-a-file")["request"]
    evidence = {
        "expected": expected_hashes(request["files"]),
        "workspace_writes": _reply("workspace.generate", "writes-a-file")["files"],
    }
    outcome, _ = evaluate_workspace_writes(["src/greeting.py"], evidence)
    assert outcome == "pass"
    rejected = {
        "expected": {},
        "workspace_writes": _reply(
            "workspace.generate", "rejects-a-path-outside-the-project"
        )["files"],
    }
    outcome, observation = evaluate_workspace_writes(["../outside.txt"], rejected)
    assert outcome != "pass" and "rejected" in observation


def test_execute_reply_is_recorded_as_a_passing_test_run():
    evidence: dict = {}
    workspace_daemon.record_test_run(
        evidence, "echo hello", _reply("workspace.execute", "runs-a-command")
    )
    assert evidence["test_run"]["exit_code"] == 0
    assert evidence["test_run"]["output_tail"] == "hello\n"
    assert evidence["ci"]["conclusion"] == "success"


def test_execute_full_log_reply_is_read_back_and_hashed_by_the_backend(monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from config import settings
    from services.workspace import run_logs

    monkeypatch.setattr(settings, "DELIVERY_FULL_LOGS", True)
    reply = _reply("workspace.execute", "uploads-the-full-log")
    stored = {
        "run-0123456789abcdef/stdout": b"hello\n",
        "run-0123456789abcdef/stderr": b"",
    }

    class Store:
        async def get_info(self, name):
            return SimpleNamespace(size=len(stored[name]))

        async def get(self, name, writeinto=None):
            if stored[name]:
                writeinto.write(stored[name])

    async def object_store(_bucket):
        return Store()

    nats = SimpleNamespace(
        js=SimpleNamespace(object_store=object_store),
        run_logs_bucket="FABRIQUE_RUN_LOGS",
    )
    spec = {"bucket": "FABRIQUE_RUN_LOGS", "name": "run-0123456789abcdef"}
    record = asyncio.run(run_logs.observe(nats, spec, reply))
    out, err = record["objects"]
    assert record["observed_by"] == "backend"
    assert out["sha256"] == hashlib.sha256(b"hello\n").hexdigest() and out["size"] == 6
    assert err["sha256"] == hashlib.sha256(b"").hexdigest()


def test_read_tool_replies_are_rendered_for_the_model():
    listing = developer_tool_replies.list_text(
        _reply("workspace.list", "lists-the-tree")
    )
    assert "src/greeting.py" in listing
    read = developer_tool_replies.read_text(
        "src/greeting.py", _reply("workspace.read", "reads-a-file")
    )
    assert "def greet(name):" in read
    found = developer_tool_replies.search_text(
        "def greet", _reply("workspace.search", "finds-a-line")
    )
    assert "src/greeting.py:1" in found
    files = developer_tool_replies.find_text(
        "*.py", _reply("workspace.find", "finds-a-file")
    )
    assert "src/main.py" in files


def test_a_refusal_is_a_daemon_error_with_its_status():
    for subject, scenario, status in (
        ("workspace.read", "refuses-a-missing-file", 404),
        ("workspace.sandbox.reset", "refuses-a-folder-not-on-this-machine", 400),
        ("workspace.browse", "refuses-a-relative-path", 400),
    ):
        nats = _Answers(_reply(subject, scenario))
        with pytest.raises(workspace_daemon.DaemonError) as refused:
            asyncio.run(workspace_daemon.daemon_request(nats, subject, {}, 1.0))
        assert refused.value.status == status


def test_map_related_and_signatures_replies_are_read():
    assert "def greet(name)" in repo_map.map_text(
        _reply("workspace.map", "maps-the-definitions")
    )
    section = code_graph.render(
        _reply("workspace.related", "the-files-around-a-seed"), [], 4000
    )
    assert "src/greeting.py" in section and "greet" in section
    row = _reply("workspace.signatures", "describes-a-function")["files"][0]
    greet = row["names"]["greet"]
    assert evidence_signatures.render("greet", greet) == "greet(name)"
    assert evidence_signatures.mismatch(greet, 1, []) == ""
    assert evidence_signatures.mismatch(greet, 2, []) != ""
    assert row["names"]["farewell"]["status"] == "missing"


def test_inspect_reply_is_a_folder_without_git():
    nats = _Answers(_reply("workspace.inspect", "a-folder-that-is-not-a-repository"))
    project = asyncio.run(workspace_daemon.inspect_project(nats, EXAMPLE_ROOT))
    assert project["is_dir"] is True and project["git"]["is_repo"] is False


# ---------------------------------------------------------------------------
# Version negotiation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reported,state,words",
    [
        ({"protocol_version": 1, "protocol_min": 1}, "compatible", None),
        ({"protocol_version": 3, "protocol_min": 1}, "compatible", None),
        ({"protocol_version": 1}, "compatible", None),
        ({}, "unknown", "older daemon"),
        (
            {"protocol_version": 2, "protocol_min": 2},
            "incompatible",
            "update the backend",
        ),
        ({"protocol_version": 0}, "incompatible", "not a version number"),
        ({"protocol_version": "1"}, "incompatible", "not a version number"),
        ({"protocol_version": True}, "incompatible", "not a version number"),
    ],
)
def test_compatibility(reported, state, words):
    verdict = daemon_liveness.compatibility(reported)
    assert verdict["state"] == state
    assert verdict["backend_supports"] == {"min": 1, "max": 1}
    if words:
        assert words in verdict["reason"]
    else:
        assert verdict["reason"] is None and verdict["negotiated"] == 1


def test_an_older_backend_range_names_the_daemon_to_update(monkeypatch):
    monkeypatch.setattr(daemon_liveness, "SUPPORTED_PROTOCOL", (2, 3))
    verdict = daemon_liveness.compatibility({"protocol_version": 1, "protocol_min": 1})
    assert verdict["state"] == "incompatible"
    assert "update the daemon" in verdict["reason"]
    assert "speaks protocol 1; this backend supports 2 to 3" in verdict["reason"]


def test_an_incompatible_daemon_is_refused_before_any_request():
    daemon_liveness.observe_heartbeat(_heartbeat(protocol_version=2, protocol_min=2))
    nats = _Answers({"files": []})
    with pytest.raises(workspace_daemon.DaemonIncompatible) as refused:
        asyncio.run(workspace_daemon.daemon_request(nats, "workspace.read", {}, 1.0))
    assert "speaks protocol 2" in str(refused.value)
    assert not refused.value.absent
    assert isinstance(refused.value, workspace_daemon.DaemonUnavailable)
    assert nats.requests == []
    # workspace.status is still asked: it is how the backend sees an update.
    asyncio.run(workspace_daemon.daemon_request(nats, "workspace.status", {}, 1.0))
    assert nats.requests == ["workspace.status"]


def test_one_compatible_daemon_among_them_keeps_serving():
    daemon_liveness.observe_heartbeat(
        _heartbeat(daemon_id="new", protocol_version=2, protocol_min=2)
    )
    daemon_liveness.observe_heartbeat(_heartbeat(daemon_id="old"))
    assert daemon_liveness.incompatibility() is None
    nats = _Answers({"files": []})
    assert asyncio.run(
        workspace_daemon.daemon_request(nats, "workspace.read", {}, 1.0)
    ) == {"files": []}


def test_an_incompatible_daemon_long_gone_does_not_block():
    daemon_liveness.observe_heartbeat(
        _heartbeat(protocol_version=2, protocol_min=2), now=time.monotonic() - 3600
    )
    assert daemon_liveness.incompatibility() is None


def test_an_older_daemon_without_a_version_is_served():
    older = {
        "host": _reply("workspace.status", "reports-the-protocol")["host"],
        "version": "2026.09",
        "jetstream": True,
    }
    presence = asyncio.run(workspace_daemon.daemon_presence(_Answers(older)))
    assert presence["state"] == "connected"
    assert presence["protocol"] == {
        "state": "unknown",
        "reason": "unknown protocol version (older daemon)",
        "negotiated": None,
        "backend_supports": {"min": 1, "max": 1},
    }
    assert daemon_liveness.incompatibility() is None
    asyncio.run(
        workspace_daemon.daemon_request(
            _Answers({"ok": True}), "workspace.read", {}, 1.0
        )
    )


# ---------------------------------------------------------------------------
# GET /v1/workspace/status
# ---------------------------------------------------------------------------


def _status(test_client, nats):
    test_client.app.state.nats_client = nats
    try:
        return test_client.get("/v1/workspace/status").json()
    finally:
        test_client.app.state.nats_client = _Answers(exc=TimeoutError("nats: timeout"))


def test_status_reports_an_incompatible_daemon_with_its_reason(test_client):
    newer = {
        **_reply("workspace.status", "reports-the-protocol"),
        "protocol_version": 2,
        "protocol_min": 2,
    }
    body = _status(test_client, _Answers(newer))
    assert body["state"] == "incompatible"
    assert body["connected"] is False
    assert (
        "speaks protocol 2; this backend supports 1: update the backend"
        in body["reason"]
    )
    assert body["protocol"]["state"] == "incompatible"
    assert body["reported_by_daemon"]["protocol_version"] == 2
    # It answered: it runs. Liveness is not the problem; the protocol is.
    assert body["liveness"] == {"state": "online", "source": "request"}


def test_status_keeps_the_daemons_claims_apart_from_the_observation(test_client):
    seen = datetime(2026, 11, 14, 12, 0, 5, tzinfo=UTC)
    daemon_liveness.observe_heartbeat(_heartbeat(sequence=4), wall=seen)
    body = _status(
        test_client, _Answers(_reply("workspace.status", "reports-the-protocol"))
    )
    assert body["state"] == "connected"
    assert body["protocol"]["state"] == "compatible"
    assert body["liveness"] == {"state": "online", "source": "heartbeat"}
    beat = body["heartbeat"]
    assert beat["observed_by"] == "backend"
    # The backend's own receive time, not the daemon's sent_at.
    assert beat["last_observed_at"] == seen.isoformat()
    daemon = beat["daemons"][0]
    assert daemon["reported_by_daemon"]["sent_at"] == "2026-11-14T12:00:00+00:00"
    assert daemon["reported_by_daemon"]["sequence"] == 4
    assert "sent_at" not in beat and "sequence" not in beat
    assert body["reported_by_daemon"]["daemon_id"] == "0123456789abcdef"


@pytest.mark.parametrize("age,state", [(10, "online"), (70, "stale"), (120, "offline")])
def test_liveness_follows_the_last_observed_heartbeat(test_client, age, state):
    daemon_liveness.observe_heartbeat(_heartbeat(), now=time.monotonic() - age)
    body = _status(test_client, _Answers(exc=TimeoutError("nats: timeout")))
    assert body["state"] == "unknown"  # the request-based check, unchanged
    assert body["liveness"] == {"state": state, "source": "heartbeat"}
    assert body["heartbeat"]["state"] == state
    assert body["heartbeat"]["offline_after_seconds"] == 90.0
    if state != "online":
        assert body["heartbeat"]["reason"].startswith("no heartbeat observed for")
    # Without an answer, the protocol is the one the last heartbeat reported.
    assert body["protocol"]["state"] == "compatible"


def test_liveness_falls_back_to_the_request_without_a_heartbeat(test_client):
    body = _status(test_client, _Answers(exc=TimeoutError("nats: timeout")))
    assert body["heartbeat"]["state"] == "not_observed"
    assert body["liveness"] == {"state": "unknown", "source": "request"}
    assert body["protocol"] is None and body["reported_by_daemon"] is None


def test_browse_relays_the_incompatible_reason(test_client):
    daemon_liveness.observe_heartbeat(_heartbeat(protocol_version=2, protocol_min=2))
    test_client.app.state.nats_client = _Answers({"entries": []})
    response = test_client.get("/v1/workspace/browse")
    assert response.status_code == 503
    assert "speaks protocol 2" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The heartbeat listener
# ---------------------------------------------------------------------------


def test_the_listener_records_beats_counts_gaps_and_never_answers():
    published = []

    class _Bus:
        async def subscribe(self, subject, callback, queue=None):
            self.subject, self.callback, self.queue = subject, callback, queue

        async def publish(self, *args, **kwargs):  # pragma: no cover - must not run
            published.append(args)

    class _Msg:
        subject = "workspace.heartbeat"
        reply = "workspace.execute"  # a forged reply subject is ignored

    bus = _Bus()
    asyncio.run(daemon_liveness.listen(bus))
    assert (bus.subject, bus.queue) == ("workspace.heartbeat", None)
    for sequence in (1, 2, 5):
        asyncio.run(bus.callback(_Msg(), _heartbeat(sequence=sequence)))
    asyncio.run(bus.callback(_Msg(), "not a heartbeat"))
    daemon = daemon_liveness.liveness()["daemons"][0]
    assert daemon["heartbeats_observed"] == 3
    assert daemon["missed"] == 2
    assert published == []


def test_only_a_bounded_number_of_daemons_is_remembered():
    for n in range(daemon_liveness.MAX_DAEMONS + 5):
        daemon_liveness.observe_heartbeat(_heartbeat(daemon_id=f"d{n}"), now=float(n))
    assert (
        len(daemon_liveness.liveness(now=50.0)["daemons"])
        == daemon_liveness.MAX_DAEMONS
    )


# ---------------------------------------------------------------------------
# A delivery to an incompatible daemon
# ---------------------------------------------------------------------------


def test_a_delivery_to_an_incompatible_daemon_is_not_sent_and_says_why(
    db, tmp_path, monkeypatch
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = tmp_path / "project"
    project.mkdir()
    item, agent, sprint = _task_fixture(db, str(project))
    nats = _DaemonOverNATS(_load_daemon())
    daemon_liveness.observe_heartbeat(_heartbeat(protocol_version=2, protocol_min=2))

    asyncio.run(
        worker_deliver._deliver_artifacts(nats, item, agent, sprint, TWO_FILE_ANSWER)
    )

    assert nats.requests == []
    assert not (project / "src").exists()
    _, verification, _ = _latest(db, item["id"])
    assert verification.verdict == "unverifiable"
    checks = {c["name"]: c for c in verification.checks}
    assert (
        "the workspace daemon is incompatible" in checks["files_written"]["observation"]
    )
    assert "update the backend" in checks["files_written"]["observation"]
    assert "timeout" not in checks["files_written"]["observation"].lower()


def test_a_delivery_that_times_out_names_the_missing_heartbeat(
    db, tmp_path, monkeypatch
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = tmp_path / "project-quiet"
    project.mkdir()
    item, agent, sprint = _task_fixture(db, str(project))
    daemon_liveness.observe_heartbeat(_heartbeat(), now=time.monotonic() - 300)

    asyncio.run(
        worker_deliver._deliver_artifacts(
            _DaemonOverNATS(_load_daemon(), fail=True),
            item,
            agent,
            sprint,
            TWO_FILE_ANSWER,
        )
    )

    _, verification, _ = _latest(db, item["id"])
    observation = {c["name"]: c for c in verification.checks}["files_written"][
        "observation"
    ]
    assert "did not reply (TimeoutError); no heartbeat observed for" in observation
