"""No agent turn runs blind: without a workspace daemon the sprint pauses
(``services/loop/daemon_gate.py``). Sprint 6, 2026-11-24: the daemon had
died, a QA task ran anyway, every workspace call failed with "no
responders", the review sent the blind report back and a rework was spent."""

import asyncio
import time
from uuid import uuid4

import pytest
import worker_stages
from models import BacklogItem, Company, Sprint
from models.audit import AuditLog
from services.contracts.tool_calls import WORKSPACE_SERVER
from services.loop import daemon_gate, sprint_control
from services.loop.worker import intake, task_run
from services.tasks import task_projection
from services.tools import developer_tools
from services.workspace import daemon_liveness
from sqlmodel import select


class NoRespondersError(Exception):
    """Named as nats-py names it; the message is what NATS says."""

    def __init__(self):
        super().__init__("nats: no responders available for request")


class _Nats:
    """NATS with no daemon behind it, or with one that answers the status."""

    jetstream_enabled = False

    def __init__(self, daemon=False):
        self.daemon = daemon
        self.published = []
        self.requests = []

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))

    async def request(self, subject, payload, timeout=None):
        self.requests.append(subject)
        if not self.daemon:
            raise NoRespondersError()
        return {"daemon_id": "d1", "protocol_version": 1}

    def events(self):
        return [p for _, p in self.published if isinstance(p, dict)]


@pytest.fixture(autouse=True)
def no_daemon_heard():
    daemon_liveness.reset()
    yield
    daemon_liveness.reset()


def _task(session, status="todo", assignee="QA"):
    company = Company(company_name=f"Daemon {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="S", status="active"
    )
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="T", status=status, assignee=assignee)
    session.add(item)
    session.commit()
    return sprint, item


def _incidents(session, sprint):
    return session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"sprint:{sprint.id}:daemon",
            AuditLog.action == daemon_gate.ACTION,
        )
    ).all()


def _assert_paused_and_waiting(session, sprint, item, nats):
    session.expire_all()
    stored = session.get(Sprint, sprint.id)
    assert stored.status == sprint_control.PAUSED
    assert stored.pause_reason == daemon_gate.REASON
    assert "./scripts/start-daemon.sh" in stored.pause_reason
    task = session.get(BacklogItem, item.id)
    assert task.status == "todo"
    record = task_projection.record_of(session, task)
    assert record.rework_request_count() == 0 and record.reworks() == 0
    assert len(_incidents(session, sprint)) == 1
    [event] = [
        e
        for e in nats.events()
        if e.get("semantic_type") == "sprint.paused_daemon_absent"
    ]
    assert "start-daemon.sh" in event["message"] and "resume" in event["message"]


def test_a_daemon_heard_lately_is_not_asked_again():
    daemon_liveness.observe_heartbeat({"daemon_id": "d1", "sequence": 1})
    nats = _Nats(daemon=False)
    assert asyncio.run(daemon_gate.absent(nats)) is False
    assert nats.requests == []


def test_only_no_responders_counts_as_absent(monkeypatch):
    assert asyncio.run(daemon_gate.absent(_Nats(daemon=False))) is True
    assert asyncio.run(daemon_gate.absent(_Nats(daemon=True))) is False
    assert asyncio.run(daemon_gate.absent(None)) is False

    class _Slow(_Nats):
        async def request(self, subject, payload, timeout=None):
            raise TimeoutError()

    # A timeout is unknown, not absent: the task runs.
    assert asyncio.run(daemon_gate.absent(_Slow())) is False
    # A heartbeat past the offline window no longer vouches for the daemon.
    daemon_liveness.observe_heartbeat({"daemon_id": "d1"}, now=time.monotonic() - 1e4)
    assert asyncio.run(daemon_gate.absent(_Nats(daemon=False))) is True


def test_the_worker_does_not_claim_a_task_without_a_daemon(sqlite_session, monkeypatch):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    ran = []

    async def _run(nats_client, backlog_item_id, ctx):
        ran.append(backlog_item_id)

    monkeypatch.setattr(task_run, "_run_claimed_task", _run)
    sprint, item = _task(sqlite_session)
    assignment = {"backlog_item_id": item.id, "sprint_id": sprint.id}

    nats = _Nats(daemon=False)
    asyncio.run(intake._on_task_assign(nats, None, assignment))

    assert ran == []
    _assert_paused_and_waiting(sqlite_session, sprint, item, nats)

    # Resumed with the daemon running: the task is claimed and runs.
    stored = sqlite_session.get(Sprint, sprint.id)
    stored.status, stored.pause_reason = sprint_control.ACTIVE, None
    sqlite_session.add(stored)
    sqlite_session.commit()
    asyncio.run(intake._on_task_assign(_Nats(daemon=True), None, assignment))
    assert ran == [item.id]


def _call(daemon_absent=True, server=WORKSPACE_SERVER):
    call = {"server": server, "tool": "read", "is_error": daemon_absent}
    if daemon_absent:
        call["daemon_absent"] = True
    return call


def test_a_run_is_blind_only_when_every_workspace_call_found_no_daemon():
    assert daemon_gate.blind([_call(), _call()]) == 2
    assert daemon_gate.blind([_call(), _call(daemon_absent=False)]) == 0
    assert daemon_gate.blind([]) == 0 and daemon_gate.blind(None) == 0
    # A call to another server says nothing about the daemon.
    other = {"server": "research", "tool": "fetch", "is_error": False}
    assert daemon_gate.blind([_call(), other]) == 1


def test_a_blind_run_is_not_delivered_and_spends_no_rework(sqlite_session, monkeypatch):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    sprint, item = _task(sqlite_session, status="in_progress")
    handed = []

    async def _nothing(*args, **kwargs):
        return {"memory": "", "wiki": "", "lessons": "", "completed": ""}

    async def _produce(nats_client, item, agent, sprint, context):
        agent["tool_calls"] = [_call() for _ in range(7)]
        return "QA report written without reading a file."

    async def _hand(*args, **kwargs):
        handed.append(args)

    monkeypatch.setattr(task_run, "_announce_claim", _nothing)
    monkeypatch.setattr(task_run, "_gather_context", _nothing)
    monkeypatch.setattr(task_run, "_announce_context", _nothing)
    monkeypatch.setattr(task_run, "_produce", _produce)
    monkeypatch.setattr(task_run, "_hand_to_qa", _hand)
    ctx = {
        "item": {"id": item.id, "title": "T"},
        "agent": {"role": "QA", "name": "QA"},
        "sprint": {"id": sprint.id, "name": "S", "company_id": sprint.company_id},
    }
    nats = _Nats(daemon=False)
    asyncio.run(task_run._run_claimed_task(nats, item.id, ctx))

    assert handed == []
    _assert_paused_and_waiting(sqlite_session, sprint, item, nats)


def test_a_workspace_call_nats_found_no_daemon_for_is_marked():
    def _absent(subject, payload, timeout):
        raise NoRespondersError()

    def _broken(subject, payload, timeout):
        raise RuntimeError("disk full")

    call = {"function": {"name": "workspace__read", "arguments": '{"path": "a"}'}}
    box = developer_tools.WorkspaceToolbox(_absent, "/project")
    observation, text = box.call(call, 1)
    assert observation["is_error"] and observation["daemon_absent"] is True
    assert "no responders" in text

    box = developer_tools.WorkspaceToolbox(_broken, "/project")
    observation, _ = box.call(call, 1)
    assert observation["is_error"] and "daemon_absent" not in observation
