"""
How the task worker picks a model and reports LLM failures (#102).

An agent with no model uses its role's default from
``services.models.model_factory`` (the same map the LangGraph producer uses)
at every call site; a failed or
empty LLM call reaches the board as ``LLM call failed: <provider> <status>
<message>``; and the model the proxy says answered is what the AgentRun
records.  The proxy client is replaced by an in-process fake.
"""

import asyncio
import json
from uuid import uuid4

import pytest
import worker_stages
from models.agent_message import AgentMessage
from models.agent_run import AgentRun
from models.company import Agent, Company
from models.sprint import BacklogItem, Sprint
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import execute as worker_execute
from services.loop.worker import intake, prompt, qa_review, task_run
from services.models.model_factory import AgentRole, ModelFactory
from sqlmodel import Session, select


def _role_default(role: AgentRole) -> str:
    return ModelFactory.ROLE_MODEL_MAP[role].model_name


class _FakeLLM:
    """What the fake proxy answers, and the model of every call it received."""

    def __init__(self):
        self.calls: list[dict] = []
        self.response: dict = {
            "choices": [{"message": {"content": "A thorough answer."}}],
            "served_model": None,
        }


@pytest.fixture()
def llm(monkeypatch):
    state = _FakeLLM()

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            state.calls.append({"model": model, "role": role})
            return state.response

    monkeypatch.setattr(qa_review, "LLMProxyClient", _Client)  # The QA review.
    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    return state


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _board(db: Session, role: str = "Developer", agent_model: str | None = None):
    company = Company(company_name=f"Routing {uuid4().hex[:8]}", autonomy_level=2)
    db.add(company)
    db.commit()
    db.refresh(company)
    if agent_model is not None:
        db.add(
            Agent(company_id=company.id, name=f"{role} A", role=role, model=agent_model)
        )
    sprint = Sprint(
        company_id=company.id, sprint_name="Routing", scope_size="M", status="active"
    )
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Implement login",
        description="Login handler",
        status="todo",
        priority="low",
        assignee=role,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return company, sprint, item


# ---------------------------------------------------------------------------
# Role defaults come from model_factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AgentRole), ids=lambda r: r.value)
@pytest.mark.parametrize("configured", ["", None], ids=["empty", "missing"])
def test_task_with_no_agent_model_uses_the_role_default(llm, role, configured):
    agent = {"role": role.value}
    if configured is not None:
        agent["model"] = configured

    worker_execute._execute_task({"id": 1, "title": "Do it"}, agent, "Sprint 1")

    assert llm.calls[0]["model"] == _role_default(role)


def test_stored_role_names_map_to_their_agent_role(llm):
    worker_execute._execute_task(
        {"id": 1, "title": "Do it"}, {"role": "BackendDev", "model": ""}, "Sprint 1"
    )

    assert llm.calls[0]["model"] == _role_default(AgentRole.DEVELOPER)


def test_configured_model_is_kept(llm):
    worker_execute._execute_task(
        {"id": 1, "title": "Do it"}, {"role": "QA", "model": "qwen3.6-flash"}, "S"
    )

    assert llm.calls[0]["model"] == "qwen3.6-flash"


def test_qa_review_with_no_model_uses_the_qa_default(llm):
    qa_review._call_llm_for_review({"role": "QA", "model": ""}, "Review this")

    assert llm.calls[0]["model"] == _role_default(AgentRole.QA)


def test_architect_consult_with_no_model_uses_the_architect_default(
    llm, db, monkeypatch
):
    recorded = []
    monkeypatch.setattr(
        worker_execute, "record_agent_turn", lambda **kw: recorded.append(kw)
    )
    llm.response = {
        "choices": [{"message": {"content": "Use a service layer."}}],
        "served_model": "qwen3.6-flash",
    }
    company, sprint, item = _board(db, role="Architect", agent_model="")

    advice = worker_execute._consult_architect(
        {"id": item.id, "title": item.title},
        {"role": "Developer"},
        {"id": sprint.id, "company_id": company.id, "name": "Routing"},
    )

    assert advice == "Use a service layer."
    assert llm.calls[0]["model"] == _role_default(AgentRole.ARCHITECT)
    assert recorded[0]["agent"]["model"] == "qwen3.6-flash"


@pytest.mark.parametrize("agent_model", ["", None], ids=["empty-row", "no-row"])
def test_task_context_resolves_an_empty_model(db, agent_model):
    _company, _sprint, item = _board(db, role="QA", agent_model=agent_model)

    ctx = task_run._load_task_context(item.id)

    # The context keeps the stored model raw; _resolve_agent_model applies the
    # role default (and any routing policy) when the task runs.
    assert ctx["agent"]["model"] == (agent_model or "")


# ---------------------------------------------------------------------------
# LLM failures are errors, with provider, status and message
# ---------------------------------------------------------------------------

_UPSTREAM_500 = {
    "error": "HTTP 502: {...}",
    "ok": False,
    "status_code": 502,
    "detail": {
        "error": "upstream_error",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "status": 500,
        "message": "internal failure",
    },
}


def test_upstream_error_names_provider_status_and_message(llm):
    llm.response = _UPSTREAM_500

    with pytest.raises(
        RuntimeError, match=r"^LLM call failed: deepseek 500 internal failure$"
    ):
        worker_execute._execute_task(
            {"id": 1, "title": "x"}, {"role": "Developer"}, "S"
        )


def test_unknown_model_error_says_what_is_available(llm):
    llm.response = {
        "error": "HTTP 400: {...}",
        "ok": False,
        "status_code": 400,
        "detail": {
            "error": "unknown_model",
            "model": "kimi-k2.6",
            "message": "unknown model 'kimi-k2.6'; available: deepseek-v4-pro",
            "available": ["deepseek-v4-pro"],
        },
    }

    with pytest.raises(
        RuntimeError,
        match=r"^LLM call failed: llm-proxy 400 unknown model 'kimi-k2.6'; available: deepseek-v4-pro$",
    ):
        worker_execute._execute_task(
            {"id": 1, "title": "x"}, {"role": "Developer", "model": "kimi-k2.6"}, "S"
        )


def test_empty_content_without_error_is_a_failure(llm):
    llm.response = {
        "choices": [{"message": {"content": "  "}}],
        "served_model": "qwen3.7-plus",
        "provider": "qwen",
    }

    with pytest.raises(
        RuntimeError,
        match=r"^LLM call failed: qwen 200 empty response from qwen3.7-plus$",
    ):
        worker_execute._execute_task({"id": 1, "title": "x"}, {"role": "PO"}, "S")


class _StopAfterSubscribe(Exception):
    pass


class _Nats:
    jetstream_enabled = False

    def __init__(self):
        self.handler = None
        self.published = []

    async def consume(self, subject, durable, callback, **kwargs):
        self.handler = callback
        raise _StopAfterSubscribe

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError("no daemon in this test")


def _task_assign_handler(monkeypatch):
    monkeypatch.setattr(worker_execute, "ARCHITECT_CONSULT_ENABLED", False)
    monkeypatch.setattr(prompt, "_fetch_memory_context", lambda *args: "")
    monkeypatch.setattr(prompt, "_load_recent_lessons", lambda *args: "")
    monkeypatch.setattr(
        prompt, "_load_sprint_completed_tasks", lambda sprint_id, item_id: ""
    )
    nats = _Nats()
    with pytest.raises(_StopAfterSubscribe):
        asyncio.run(intake.process_backlog_tasks(nats))
    return nats


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_UPSTREAM_500, "LLM call failed: deepseek 500 internal failure"),
        (
            {
                "choices": [{"message": {"content": ""}}],
                "served_model": "deepseek-v4-pro",
                "provider": "deepseek",
            },
            "LLM call failed: deepseek 200 empty response from deepseek-v4-pro",
        ),
    ],
    ids=["upstream-500", "empty-content"],
)
def test_llm_failure_is_recorded_on_the_board(llm, db, monkeypatch, response, expected):
    llm.response = response
    _company, sprint, item = _board(db, role="Developer", agent_model="")
    nats = _task_assign_handler(monkeypatch)

    # A 500 is transient: queued again MAX_TRANSIENT_RETRIES times, each
    # saying why, then blocked with the error (issue #766). An empty answer
    # blocks at once.
    transient = intake.is_transient_llm_failure(expected)
    attempts = intake.MAX_TRANSIENT_RETRIES + 1 if transient else 1
    for attempt in range(attempts):
        asyncio.run(
            nats.handler(None, {"backlog_item_id": item.id, "sprint_id": sprint.id})
        )
        db.expire_all()
        if attempt < attempts - 1:
            stored = db.get(BacklogItem, item.id)
            assert stored.status == "todo"
            assert stored.comments[-1]["text"].startswith(intake.TRANSIENT_RETRY_PREFIX)
            assert stored.comments[-1]["text"].endswith(expected)

    stored = db.get(BacklogItem, item.id)
    comments = stored.comments
    if isinstance(comments, str):
        comments = json.loads(comments)
    assert comments[-1]["text"] == expected
    assert stored.status == "blocked"


def test_a_tool_call_written_as_text_is_retried_once_then_blocks(llm, db, monkeypatch):
    """The first final answer that is only a tool call written as text queues
    the task again (a new attempt, a fresh tool budget) and says so; the
    second blocks it, saying it happened again (issue #766)."""
    reason = "the model's last answer was a tool call written as text; the tool budget had run out"
    llm.response = {
        "choices": [{"message": {"content": "<tool_calls>read</tool_calls>"}}],
        "served_model": "deepseek-v4-pro",
        "tool_loop": {"rounds_exhausted": True, "not_a_deliverable": reason},
    }
    _company, sprint, item = _board(db, role="Developer", agent_model="")
    nats = _task_assign_handler(monkeypatch)
    assign = {"backlog_item_id": item.id, "sprint_id": sprint.id}

    asyncio.run(nats.handler(None, assign))
    db.expire_all()
    stored = db.get(BacklogItem, item.id)
    assert stored.status == "todo"
    retried = stored.comments[-1]["text"]
    assert retried.startswith(intake.TOOL_CALL_AS_TEXT_RETRY_PREFIX + " (1/1)")
    assert retried.endswith(reason)

    asyncio.run(nats.handler(None, assign))
    db.expire_all()
    stored = db.get(BacklogItem, item.id)
    assert stored.status == "blocked"
    texts = [c["text"] for c in stored.comments]
    assert retried in texts  # The first one stays on the task's events.
    assert reason in texts[-1] and "again, after one retry" in texts[-1]


# ---------------------------------------------------------------------------
# AgentRun.model is the model that answered
# ---------------------------------------------------------------------------


def test_served_model_is_recorded_on_the_agent_run(llm, db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(worker_deliver, "_resolve_workspace_root", lambda cid: None)
    llm.response = {
        "choices": [{"message": {"content": "A written analysis with no files."}}],
        "served_model": "qwen3.6-flash",
        "provider": "qwen",
    }
    company, sprint, item = _board(db, role="Developer", agent_model="deepseek-v4-pro")
    ctx_agent = {"role": "Developer", "name": "Dev", "model": "deepseek-v4-pro"}
    item_dict = {"id": item.id, "title": item.title, "description": item.description}
    sprint_dict = {"id": sprint.id, "company_id": company.id, "name": "Routing"}

    deliverable = worker_execute._execute_task(item_dict, ctx_agent, "Routing")
    asyncio.run(
        worker_deliver._deliver_artifacts(
            _Nats(), item_dict, ctx_agent, sprint_dict, deliverable
        )
    )

    db.expire_all()
    run = db.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).first()
    assert run.model == "qwen3.6-flash"


# ---------------------------------------------------------------------------
# The conversation: the developer asks the Architect, then hands over to QA
# ---------------------------------------------------------------------------


def test_task_execution_records_the_conversation(llm, db, monkeypatch):
    llm.response = {
        "choices": [{"message": {"content": "A thorough written analysis. " * 40}}],
    }
    company, sprint, item = _board(db, role="Developer", agent_model="")
    db.add(Agent(company_id=company.id, name="Ada", role="Architect", model=""))
    db.commit()
    nats = _task_assign_handler(monkeypatch)
    monkeypatch.setattr(worker_execute, "ARCHITECT_CONSULT_ENABLED", True)
    monkeypatch.setattr(
        worker_execute, "should_consult_architect", lambda *a, **k: True
    )

    asyncio.run(
        nats.handler(None, {"backlog_item_id": item.id, "sprint_id": sprint.id})
    )

    db.expire_all()
    messages = db.exec(
        select(AgentMessage)
        .where(AgentMessage.backlog_item_id == item.id)
        .order_by(AgentMessage.id)
    ).all()
    assert [(m.from_role, m.to_role, m.kind) for m in messages] == [
        ("Developer", "Architect", "question"),
        ("Architect", "Developer", "answer"),
        ("Developer", "QA", "handoff"),
    ]
    assert 'Before I implement "Implement login"' in messages[0].body
    assert (
        messages[2].body
        == 'Delivered "Implement login" for review (no files declared).'
    )
    assert {m.sprint_id for m in messages} == {sprint.id}


# ---------------------------------------------------------------------------
# The second opinion is the team's own (issue #750)
# ---------------------------------------------------------------------------


class _RecordingNats(_Nats):
    """Records every request: a CLI consultation would be ``workspace.execute``."""

    def __init__(self):
        super().__init__()
        self.requests: list[str] = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append(subject)
        raise TimeoutError("no daemon in this test")


@pytest.mark.parametrize("role", ["QA", "PO", "ScrumMaster", "Developer"])
def test_with_defaults_no_cli_runs_and_the_architect_advises_on_its_default_model(
    llm, db, monkeypatch, role
):
    """A design-sensitive task of a role an external console assistant used to
    advise is briefed by the team's Architect, on the Architect's role default
    from model_factory (DeepSeek Pro unless an override says otherwise), and
    nothing asks the daemon to run a CLI."""
    llm.response = {
        "choices": [{"message": {"content": "A thorough written analysis. " * 40}}],
    }
    company, sprint, item = _board(db, role=role, agent_model="")
    item.title = "Design the implementation slice"
    db.add(item)
    db.add(Agent(company_id=company.id, name="Ada", role="Architect", model=""))
    db.commit()
    monkeypatch.setattr(prompt, "_fetch_memory_context", lambda *args: "")
    monkeypatch.setattr(prompt, "_load_recent_lessons", lambda *args: "")
    monkeypatch.setattr(prompt, "_load_sprint_completed_tasks", lambda s, i: "")
    nats = _RecordingNats()
    with pytest.raises(_StopAfterSubscribe):
        asyncio.run(intake.process_backlog_tasks(nats))

    asyncio.run(
        nats.handler(None, {"backlog_item_id": item.id, "sprint_id": sprint.id})
    )

    assert worker_execute.ARCHITECT_CONSULT_ENABLED, "on by default"
    db.refresh(company)
    assert not company.console_producers, "a console producer is never on by default"
    assert "workspace.execute" not in nats.requests, "no CLI ran"
    assert not [
        p for _s, p in nats.published if p.get("semantic_type") == "assistant.telemetry"
    ]
    architect = [c for c in llm.calls if c["role"] == "Architect"]
    assert architect, "the Architect was consulted"
    assert architect[0]["model"] == _role_default(AgentRole.ARCHITECT)
    db.expire_all()
    kinds = [
        (m.from_role, m.to_role, m.kind)
        for m in db.exec(
            select(AgentMessage).where(AgentMessage.backlog_item_id == item.id)
        ).all()
    ]
    assert ("Architect", role, "answer") in kinds
