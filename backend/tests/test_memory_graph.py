"""The knowledge graph is optional (issue #328).

Off by default: the backend starts without Neo4j, memory search and store run
on pgvector alone, graph writes are skipped and reported as not recorded,
every graph reader answers "not recorded" with the reason (never a 500, never
a silent empty list), and ``/health`` does not degrade for a graph that was
switched off.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import lifecycle
import pytest
from config import Settings, settings
from memory_client import GraphNotRecorded, MemoryClient
from services import healthcheck, memory_graph
from services.company_service import CompanyService
from services.platform.engram_processor import DeepSeekEngramProcessor


@pytest.fixture
def graph_off(monkeypatch):
    monkeypatch.setattr(settings, "NEO4J_ENABLED", False)


@pytest.fixture
def graph_on(monkeypatch):
    monkeypatch.setattr(settings, "NEO4J_ENABLED", True)


def test_graph_is_on_by_default_and_optional():
    assert Settings.model_fields["NEO4J_ENABLED"].default is True


# ── Readers ─────────────────────────────────────────────────────────────


def test_lineage_says_not_recorded_without_the_graph(
    test_client, graph_off, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    driver = MagicMock(side_effect=AssertionError("the graph must not be asked"))
    monkeypatch.setattr("services.memory_graph.get_neo4j_driver", driver)

    response = test_client.get(
        "/v1/enterprise/lineage", params={"file_path": "src/main.py"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recorded"] is False
    assert body["status"] == "not_recorded"
    assert "NEO4J_ENABLED=false" in body["reason"]
    assert body["lineage"] == []
    assert body["file"] == "src/main.py"
    driver.assert_not_called()


def test_lineage_kept_but_failing_is_still_an_error(test_client, graph_on, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)

    def broken():
        raise RuntimeError("bolt refused")

    monkeypatch.setattr("services.memory_graph.get_neo4j_driver", broken)

    response = test_client.get(
        "/v1/enterprise/lineage", params={"file_path": "src/main.py"}
    )

    assert response.status_code == 500


def test_lineage_with_the_graph_says_recorded(graph_on, monkeypatch):
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value.run.return_value = []
    monkeypatch.setattr("services.memory_graph.get_neo4j_driver", lambda: driver)

    body = memory_graph.lineage("a.py", 7, [7], scoped=True)

    assert body["recorded"] is True
    assert body["lineage"] == []


def test_engram_history_is_not_recorded_rather_than_empty(graph_off):
    processor = DeepSeekEngramProcessor()
    processor._driver = MagicMock(side_effect=AssertionError("no graph"))
    with pytest.raises(GraphNotRecorded):
        processor.query_engrams()


def test_graph_reads_on_a_client_without_graph_raise():
    client = MemoryClient(neo4j_driver=None)
    assert client.graph_enabled is False
    with pytest.raises(GraphNotRecorded):
        client.query_graph("MATCH (n) RETURN n")
    with pytest.raises(GraphNotRecorded):
        client.get_relationships("Episodic", "e-1")
    with pytest.raises(GraphNotRecorded):
        client.store_entity("Sprint", {"id": 1})


# ── Memory on pgvector alone ────────────────────────────────────────────


def test_memory_store_on_pgvector_alone_reports_the_graph_not_recorded():
    client = MemoryClient(neo4j_driver=None)
    with (
        patch("llm_proxy_client.LLMProxyClient") as proxy,
        patch("memory_client.Session") as session,
    ):
        proxy.return_value.embed.return_value = [0.5]
        outcome = client.store("decision", {"k": "v"}, company_id=7)

    assert outcome == {"vector": "stored", "graph": "not_recorded"}
    params = session.return_value.__enter__.return_value.execute.call_args.args[1]
    assert params["company_id"] == 7


def test_memory_store_without_an_embedding_records_nothing():
    client = MemoryClient(neo4j_driver=None)
    with (
        patch("llm_proxy_client.LLMProxyClient") as proxy,
        patch("memory_client.Session") as session,
    ):
        proxy.return_value.embed.return_value = None
        outcome = client.store("decision")

    assert outcome == {"vector": "not_recorded", "graph": "not_recorded"}
    session.assert_not_called()


def test_memory_store_with_the_graph_reports_both():
    driver = MagicMock()
    client = MemoryClient(neo4j_driver=driver)
    with (
        patch("llm_proxy_client.LLMProxyClient") as proxy,
        patch("memory_client.Session"),
    ):
        proxy.return_value.embed.return_value = [0.5]
        outcome = client.store("decision", company_id=7)

    assert outcome == {"vector": "stored", "graph": "stored"}
    driver.session.assert_called_once()


def test_memory_search_works_without_the_graph():
    client = MemoryClient(neo4j_driver=None)
    with (
        patch("llm_proxy_client.LLMProxyClient") as proxy,
        patch("memory_client.Session") as session,
    ):
        proxy.return_value.embed.return_value = [0.5]
        db = session.return_value.__enter__.return_value
        db.execute.return_value = [("decision", {}, 0.9, 7)]
        results = client.search_similar("query", company_ids=[7])

    assert [r["content"] for r in results] == ["decision"]


def test_memory_routes_answer_on_pgvector_alone(test_client, graph_off, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    previous = getattr(test_client.app.state, "memory_client", None)
    test_client.app.state.memory_client = MemoryClient(neo4j_driver=None)
    try:
        with (
            patch("llm_proxy_client.LLMProxyClient") as proxy,
            patch("memory_client.Session") as session,
        ):
            proxy.return_value.embed.return_value = [0.5]
            db = session.return_value.__enter__.return_value
            db.execute.return_value = [("decision", {}, 0.9, None)]
            stored = test_client.post(
                "/v1/memory/store", params={"content": "decision"}
            )
            found = test_client.get("/v1/memory/search", params={"query": "x"})
    finally:
        test_client.app.state.memory_client = previous

    assert stored.status_code == 200
    assert stored.json() == {
        "status": "stored",
        "vector": "stored",
        "graph": "not_recorded",
    }
    assert found.status_code == 200
    assert found.json()["results"][0]["content"] == "decision"


def test_memory_routes_say_why_without_pgvector(test_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    previous = getattr(test_client.app.state, "memory_client", None)
    test_client.app.state.memory_client = None
    try:
        response = test_client.get("/v1/memory/search", params={"query": "x"})
    finally:
        test_client.app.state.memory_client = previous

    assert response.status_code == 503
    assert "vector_memory" in response.json()["detail"]
    assert "not needed" in response.json()["detail"]


def test_engram_without_the_graph_is_kept_in_pgvector(graph_off):
    processor = DeepSeekEngramProcessor()
    with (
        patch.object(processor, "_process_with_deepseek", return_value="summary"),
        patch.object(processor, "_create_engram_node") as node,
        patch.object(processor, "_create_temporal_edges") as edges,
        patch.object(processor, "_store_in_vector_db") as vector,
    ):
        result = processor.burn_engram(
            "agent",
            "PO",
            "decision",
            related_semantic_refs=["Semantic_JavaClass:a.B"],
            company_id=7,
        )

    node.assert_not_called()
    edges.assert_not_called()
    assert vector.call_args.args[2] == 7
    assert vector.call_args.args[1]["engram_id"] == result["engram_id"]
    assert result["graph"] == "not_recorded"
    assert result["engram_id"].startswith("engram_")


def test_rag_context_comes_from_pgvector_alone_without_the_graph(graph_off):
    processor = DeepSeekEngramProcessor()
    processor._driver = MagicMock(side_effect=AssertionError("no graph"))
    with (
        patch("services.platform.engram_processor.MemoryClient") as client_cls,
        patch.object(processor, "_query_graph_rag") as graph_rag,
    ):
        client_cls.return_value.search_similar.return_value = [
            {"content": "use postgres", "similarity": 0.9}
        ]
        context = processor.query_relevant_engrams("database", company_id=7)

    client_cls.assert_called_once_with(neo4j_driver=None)
    graph_rag.assert_not_called()
    assert "use postgres" in context
    assert "Neo4j" not in context


def test_company_seed_skips_the_graph_when_off(graph_off, monkeypatch):
    called = MagicMock()
    monkeypatch.setattr("services.company_service.get_neo4j_driver", called)
    CompanyService.__new__(CompanyService)._seed_neo4j(1, "Acme")
    called.assert_not_called()


# ── Startup ─────────────────────────────────────────────────────────────


def _start(has_vector_memory):
    app = SimpleNamespace(state=SimpleNamespace())
    lifecycle._start_memory(app, has_vector_memory)
    return app.state


def test_startup_without_the_graph_never_asks_neo4j(graph_off, monkeypatch):
    driver = MagicMock(side_effect=AssertionError("Neo4j must not be needed"))
    monkeypatch.setattr(lifecycle, "get_neo4j_driver", driver)

    state = _start(True)

    assert state.memory_graph is False
    assert state.memory_client is not None
    assert state.memory_client.graph_enabled is False
    driver.assert_not_called()


def test_startup_with_the_graph_connects_it(graph_on, monkeypatch):
    driver = MagicMock()
    monkeypatch.setattr(lifecycle, "get_neo4j_driver", lambda: driver)
    monkeypatch.setattr(lifecycle, "ensure_memory_graph_schema", lambda d: None)

    state = _start(True)

    assert state.memory_graph is True
    assert state.memory_client.neo4j_driver is driver


def test_startup_with_an_unreachable_graph_keeps_memory_search(graph_on, monkeypatch):
    def unreachable():
        raise RuntimeError("bolt refused")

    monkeypatch.setattr(lifecycle, "get_neo4j_driver", unreachable)

    state = _start(True)

    assert state.memory_graph is False
    assert state.memory_client.graph_enabled is False


def test_startup_without_vector_memory_has_no_memory_client(graph_off):
    state = _start(False)
    assert state.memory_client is None


# ── Health ──────────────────────────────────────────────────────────────


def _aggregate(monkeypatch, neo4j_sync_result):
    up = {"status": "up"}

    async def ok(*_args, **_kwargs):
        return up

    monkeypatch.setattr(healthcheck, "probe_nats", ok)
    monkeypatch.setattr(healthcheck, "probe_postgres", ok)
    monkeypatch.setattr(healthcheck, "probe_llm_proxy", ok)
    probe = MagicMock(return_value=neo4j_sync_result)
    monkeypatch.setattr(healthcheck, "_sync_probe_neo4j", probe)
    return asyncio.run(healthcheck.aggregate(None)), probe


def test_health_is_not_degraded_by_a_graph_switched_off(graph_off, monkeypatch):
    document, probe = _aggregate(monkeypatch, {"status": "down", "reason": "x"})

    assert document["status"] == "healthy"
    assert document["components"]["neo4j"]["status"] == "disabled"
    probe.assert_not_called()


def test_health_is_degraded_by_an_enabled_graph_that_is_down(graph_on, monkeypatch):
    document, probe = _aggregate(monkeypatch, {"status": "down", "reason": "x"})

    assert document["status"] == "degraded"
    assert document["components"]["neo4j"]["status"] == "down"
    probe.assert_called_once()


def test_an_unreachable_graph_is_connected_once_neo4j_answers(graph_on, monkeypatch):
    """Neo4j starts slower than the backend: the graph comes on without a restart."""
    import asyncio

    driver = MagicMock()
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("bolt refused")
        return driver

    monkeypatch.setattr(lifecycle, "get_neo4j_driver", flaky)
    monkeypatch.setattr(lifecycle, "ensure_memory_graph_schema", lambda d: None)
    monkeypatch.setattr(lifecycle, "GRAPH_RETRY_SECONDS", 0)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        lifecycle._start_memory(app, True)
        assert app.state.memory_graph is False
        assert app.state.memory_client.graph_enabled is False
        connected = await lifecycle._connect_graph_later(app, True)
        return app.state, connected

    state, connected = asyncio.run(scenario())
    assert connected is True
    assert state.memory_graph is True
    assert state.memory_client.neo4j_driver is driver


def test_the_retry_gives_up_and_leaves_memory_search(graph_on, monkeypatch):
    import asyncio

    def unreachable():
        raise RuntimeError("bolt refused")

    monkeypatch.setattr(lifecycle, "get_neo4j_driver", unreachable)
    monkeypatch.setattr(lifecycle, "GRAPH_RETRY_SECONDS", 0)
    monkeypatch.setattr(lifecycle, "GRAPH_RETRY_ATTEMPTS", 2)
    app = SimpleNamespace(state=SimpleNamespace(memory_graph=False, memory_client=None))

    assert asyncio.run(lifecycle._connect_graph_later(app, True)) is False
    assert app.state.memory_graph is False
