"""
The backend with DeepSeek alone (issue #751).

The owner's standing decision (2026-11-20): La Fabrique runs on DeepSeek and no
other configuration may stop the work. With only ``DEEPSEEK_API_KEY`` set
(no Gemini, Qwen or Nebius key, no GitHub token, no SMTP, the knowledge graph
off): every role plans, executes and reviews on a DeepSeek model, a task runs
from the board to QA with a fake proxy that refuses any other provider's
model, memory works without an embeddings provider and without a graph, the
startup self-check names every capability without a value, and
``/health/deep`` lists them with their reasons without changing its status.
"""

import asyncio
import json
import logging
from typing import ClassVar
from uuid import uuid4

import pytest
import worker_stages
from config import settings
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services.loop.worker import execute as worker_execute
from services.loop.worker import intake, prompt, qa_review
from services.models import model_factory
from services.models.model_factory import AgentRole, ModelFactory
from services.planning import discovery_team
from services.platform import capabilities, healthcheck

OTHER_PROVIDER_ENV = (
    "GEMINI_API_KEY",
    "QWEN_API_KEY",
    "NEBIUS_API_KEY",
    "GITHUB_TOKEN",
    "SMTP_HOST",
    "AGENT_MODEL",
    "NEMOTRON_ROLES",
    "LLM_FALLBACK_PROVIDERS",
    "EMBEDDING_PROVIDER",
)


@pytest.fixture()
def deepseek_only(monkeypatch):
    """Only ``DEEPSEEK_API_KEY`` among the providers and integrations."""
    for name in OTHER_PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    for role in AgentRole:
        monkeypatch.delenv(f"AGENT_MODEL_{role.name}", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "QWEN_API_KEY", None)
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "")
    monkeypatch.setattr(settings, "NEO4J_ENABLED", False)
    monkeypatch.setattr(settings, "MANAGER_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "SUPPORT_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "REVIEWER_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_MODEL_POLICY", "")


def _is_deepseek(model: str) -> bool:
    return model in (model_factory.PRO_MODEL, model_factory.FLASH_MODEL)


# ── Plan, execute, review: every role on DeepSeek ───────────────────────


@pytest.mark.parametrize("role", list(AgentRole), ids=lambda r: r.value)
def test_every_role_runs_on_deepseek(deepseek_only, role):
    assert _is_deepseek(ModelFactory.model_for_role(role))


@pytest.mark.parametrize(
    "role", ["PO", "Architect", "SM", "Dev", "QA", "Security", "DevOps", "Data"]
)
def test_the_planned_team_is_deepseek(deepseek_only, role):
    # New company's suggested team (planning) takes each role's default.
    assert _is_deepseek(discovery_team.model_for_discovery_role(role))


def test_the_reviewer_judges_on_deepseek_pro(deepseek_only):
    assert model_factory.REVIEWER_MODEL == model_factory.PRO_MODEL


class _DeepSeekOnlyProxy:
    """A fake LLM proxy client that serves only DeepSeek models."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, base_url=None):
        self.timeout = None

    def chat(self, messages, model, role, bu_id=None):
        type(self).calls.append({"model": model, "role": role})
        if not _is_deepseek(model):
            return {
                "error": f"HTTP 400: unknown model {model}",
                "ok": False,
                "status_code": 400,
            }
        return {
            "choices": [{"message": {"content": "A thorough written analysis. " * 40}}],
            "served_model": model,
            "provider": "deepseek",
        }


class _Stop(Exception):
    pass


class _Nats:
    jetstream_enabled = False

    def __init__(self):
        self.handler = None

    async def consume(self, subject, durable, callback, **kwargs):
        self.handler = callback
        raise _Stop

    async def publish(self, subject, payload, msg_id=None):
        return None

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError("no daemon in this test")


def test_a_task_runs_from_the_board_to_review_on_deepseek_alone(
    deepseek_only, monkeypatch, sqlite_session
):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    _DeepSeekOnlyProxy.calls = []
    monkeypatch.setattr(worker_execute, "LLMProxyClient", _DeepSeekOnlyProxy)
    monkeypatch.setattr(qa_review, "LLMProxyClient", _DeepSeekOnlyProxy)
    monkeypatch.setattr(worker_execute, "ARCHITECT_CONSULT_ENABLED", False)
    # Memory and lessons run for real elsewhere; here they add nothing.
    monkeypatch.setattr(prompt, "_load_recent_lessons", lambda *args: "")
    monkeypatch.setattr(
        prompt, "_load_sprint_completed_tasks", lambda sprint_id, item_id: ""
    )

    company = Company(company_name=f"DeepSeek only {uuid4().hex[:8]}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    sprint = Sprint(
        company_id=company.id, sprint_name="DS", scope_size="M", status="active"
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Implement login",
        description="Login handler",
        status="todo",
        priority="low",
        assignee="Developer",
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    sqlite_session.refresh(item)

    nats = _Nats()
    with pytest.raises(_Stop):
        asyncio.run(intake.process_backlog_tasks(nats))
    asyncio.run(
        nats.handler(None, {"backlog_item_id": item.id, "sprint_id": sprint.id})
    )

    sqlite_session.expire_all()
    stored = sqlite_session.get(BacklogItem, item.id)
    comments = (
        stored.comments
        if not isinstance(stored.comments, str)
        else json.loads(stored.comments)
    )
    assert _DeepSeekOnlyProxy.calls, "the task made no model call"
    assert all(_is_deepseek(call["model"]) for call in _DeepSeekOnlyProxy.calls)
    assert stored.status not in ("todo", "in_progress", "blocked"), (
        stored.status,
        comments,
    )
    assert not any("LLM call failed" in str(c.get("text", "")) for c in comments or [])


def test_qa_review_runs_on_deepseek(deepseek_only, monkeypatch):
    _DeepSeekOnlyProxy.calls = []
    monkeypatch.setattr(qa_review, "LLMProxyClient", _DeepSeekOnlyProxy)

    qa_review._call_llm_for_review({"role": "QA", "model": ""}, "Review this")

    assert _is_deepseek(_DeepSeekOnlyProxy.calls[0]["model"])


# ── Memory without an embeddings provider or a graph ────────────────────


class _ProxyAnswer:
    def __init__(self, body):
        self._body = body
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_the_local_embedding_reaches_memory_without_error(deepseek_only, monkeypatch):
    import llm_proxy_client

    # What the proxy answers with no embedding provider chosen.
    local = {
        "data": [{"embedding": [0.0] * 1535 + [1.0], "index": 0}],
        "model": "local-fallback-embedding",
    }
    monkeypatch.setattr(
        llm_proxy_client.requests, "post", lambda *a, **k: _ProxyAnswer(local)
    )

    vector = llm_proxy_client.LLMProxyClient("http://proxy").embed("a decision")

    assert vector is not None and len(vector) == 1536


def test_memory_without_a_proxy_answer_records_nothing_and_does_not_raise(
    deepseek_only, monkeypatch
):
    import llm_proxy_client
    import requests
    from memory_client import NOT_RECORDED, MemoryClient

    def refused(*args, **kwargs):
        raise requests.ConnectionError("proxy down")

    monkeypatch.setattr(llm_proxy_client.requests, "post", refused)

    client = MemoryClient(neo4j_driver=None)
    assert client.store("a decision", company_id=1) == {
        "vector": NOT_RECORDED,
        "graph": NOT_RECORDED,
    }
    assert client.search_similar("a decision", company_ids=[1]) == []


def test_the_task_prompts_memory_context_never_raises_without_a_graph(
    deepseek_only, monkeypatch
):
    import llm_proxy_client

    monkeypatch.setattr(
        llm_proxy_client.LLMProxyClient,
        "embed_with_model",
        lambda self, text, *a, **k: None,
    )

    context = prompt._fetch_memory_context(
        {"id": 1, "title": "Login"}, {"role": "Developer"}, 1
    )

    assert isinstance(context, str)


# ── Capabilities: the self-check and /health/deep ───────────────────────


def _deepseek_caps():
    return capabilities.backend(
        settings,
        memory_store=True,
        graph_connected=False,
        auth_configured=True,
        encryption=False,
        signing=False,
        forge_ceiling="none",
    )


def test_every_optional_integration_is_named_with_a_reason(deepseek_only):
    caps = _deepseek_caps()

    for name in (
        "knowledge_graph",
        "github_token",
        "mail",
        "error_reports",
        "forge",
        "manager_agent",
        "support_agent",
        "reviewer_agent",
    ):
        assert caps[name]["state"] == "off", name
    assert caps["memory"]["state"] == "on"
    assert caps["credential_encryption"]["state"] == "degraded"
    assert all(cap["reason"] for cap in caps.values())
    assert "NEO4J_ENABLED=false" in caps["knowledge_graph"]["reason"]


def test_an_enabled_graph_that_does_not_answer_is_degraded_not_fatal(
    deepseek_only, monkeypatch
):
    monkeypatch.setattr(settings, "NEO4J_ENABLED", True)

    assert _deepseek_caps()["knowledge_graph"]["state"] == "degraded"


def test_capabilities_never_carry_a_settings_value(deepseek_only, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.internal.example")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "test-github-token-value")
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "https://errors.example/hook")

    text = json.dumps(_deepseek_caps())

    assert "mail.internal.example" not in text
    assert "test-github-token-value" not in text
    assert "errors.example" not in text
    assert "test-deepseek-key" not in text


def test_the_startup_self_check_logs_names_and_reasons(deepseek_only, caplog):
    import lifecycle

    class _State:
        memory_client = None
        memory_graph = False

    class _App:
        state = _State()

    with caplog.at_level(logging.INFO):
        lifecycle.log_capabilities(_App())

    text = caplog.text
    assert "Effective capabilities:" in text
    assert "knowledge_graph=off" in text and "mail=off" in text
    assert "test-deepseek-key" not in text


PROXY_CAPS = {
    "providers_configured": ["deepseek"],
    "embeddings": {
        "state": "degraded",
        "provider": "local",
        "model": "local-fallback-embedding",
        "reason": "no embedding provider chosen (DeepSeek has no embeddings endpoint)",
        "unexpected": "dropped",
    },
    "failover": {
        "state": "on",
        "scope": "same_provider",
        "reason": "DeepSeek Pro and Flash",
    },
}


def test_health_deep_lists_the_capabilities_and_keeps_its_status(
    deepseek_only, monkeypatch
):
    async def up(*args, **kwargs):
        return {"status": "up"}

    async def proxy():
        return {"status": "up", "capabilities": PROXY_CAPS}

    async def graph_off():
        return {"status": "disabled", "reason": "NEO4J_ENABLED=false"}

    monkeypatch.setattr(healthcheck, "probe_nats", up)
    monkeypatch.setattr(healthcheck, "probe_postgres", up)
    monkeypatch.setattr(healthcheck, "probe_neo4j", graph_off)
    monkeypatch.setattr(healthcheck, "probe_llm_proxy", proxy)

    document = asyncio.run(
        healthcheck.aggregate(None, backend_capabilities=_deepseek_caps())
    )

    assert document["status"] == "healthy"
    caps = document["capabilities"]
    assert caps["llm.providers"]["state"] == "on"
    assert caps["llm.embeddings"]["provider"] == "local"
    assert "unexpected" not in caps["llm.embeddings"]
    assert caps["llm.failover"]["scope"] == "same_provider"
    assert caps["knowledge_graph"]["state"] == "off"
    assert "capabilities" not in document["components"]["llm_proxy"]


def test_a_proxy_without_capabilities_is_named_not_hidden(deepseek_only):
    assert capabilities.from_proxy(None)["llm"]["state"] == "off"
    assert (
        capabilities.from_proxy({"providers_configured": []})["llm.providers"]["state"]
        == "off"
    )


def test_the_deep_health_route_serves_the_capabilities(deepseek_only, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def fake_aggregate(
        nats_client, nats_state=None, backend_capabilities=None, verification_state=None
    ):
        return {
            "status": "healthy",
            "components": {},
            "capabilities": backend_capabilities,
        }

    import importlib

    monkeypatch.setattr(
        importlib.import_module("app.main"), "aggregate", fake_aggregate
    )

    body = TestClient(app).get("/health/deep").json()

    assert body["capabilities"]["mail"]["state"] == "off"
    assert body["capabilities"]["knowledge_graph"]["state"] == "off"
