"""Memory records each vector's source and compares only like with like (issue #755).

The proxy embeds with a local semantic model (multilingual-e5-small) by
default and with the hash vector when the model cannot load. Vectors from the
two do not compare, so a stored row carries the model the proxy named, a
search filters on the query's model, rows of another source are re-embedded
once the semantic model answers (never towards the hash vector), and
``/health/deep`` shows memory on only when the embedding is semantic.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import lifecycle
import memory_client
from memory_client import HASH_MODEL, MemoryClient
from services.platform import capabilities

E5 = "multilingual-e5-small"


class _Proxy:
    """``LLMProxyClient`` answering every text with *model*, or nothing."""

    def __init__(self, model):
        self.model = model
        self.texts = []

    def __call__(self):
        return self

    def embed_with_model(self, text, *args, **kwargs):
        self.texts.append(text)
        return None if self.model is None else ([0.1] * 1536, self.model)


def test_a_stored_vector_records_the_model_that_made_it():
    with (
        patch("llm_proxy_client.LLMProxyClient", _Proxy(E5)),
        patch("memory_client.Session") as session,
    ):
        outcome = MemoryClient().store_vector("a decision", {"k": "v"}, company_id=3)

    query, params = session.return_value.__enter__.return_value.execute.call_args.args
    assert outcome == memory_client.STORED
    assert "embedding_model" in str(query)
    assert params["embedding_model"] == E5


def test_a_search_compares_only_rows_of_the_querys_model():
    with (
        patch("llm_proxy_client.LLMProxyClient", _Proxy(E5)),
        patch("memory_client.Session") as session,
    ):
        db = session.return_value.__enter__.return_value
        db.execute.return_value = [("decision", {}, 0.9, 7)]
        MemoryClient().search_similar("query", company_ids=[7])

    query, params = db.execute.call_args.args
    query = str(query)
    assert "embedding_model = :embedding_model" in query
    assert (
        query.index("WHERE") < query.index("embedding_model") < query.index("ORDER BY")
    )
    assert params["embedding_model"] == E5


def _stale_db(session, batches):
    """A session whose SELECT answers *batches* in turn; returns the UPDATE calls."""
    db = session.return_value.__enter__.return_value
    answers = iter(batches)
    updates = []

    def execute(query, params=None):
        if str(query).startswith("SELECT"):
            result = MagicMock()
            result.all.return_value = next(answers, [])
            return result
        updates.append(params)
        return MagicMock()

    db.execute.side_effect = execute
    return updates


def test_stale_rows_are_re_embedded_with_the_semantic_model():
    proxy = _Proxy(E5)
    with (
        patch("llm_proxy_client.LLMProxyClient", proxy),
        patch("memory_client.Session") as session,
    ):
        updates = _stale_db(session, [[(1, "uno"), (2, "two")], []])
        count = MemoryClient().reembed_stale()

    assert count == 2
    assert [u["id"] for u in updates] == [1, 2]
    assert {u["model"] for u in updates} == {E5}
    assert proxy.texts[1:] == ["uno", "two"]


def test_nothing_is_re_embedded_towards_the_hash_vector():
    with (
        patch("llm_proxy_client.LLMProxyClient", _Proxy(HASH_MODEL)),
        patch("memory_client.Session") as session,
    ):
        assert MemoryClient().reembed_stale() == 0
    session.assert_not_called()


def test_nothing_is_re_embedded_without_a_proxy_answer():
    with (
        patch("llm_proxy_client.LLMProxyClient", _Proxy(None)),
        patch("memory_client.Session") as session,
    ):
        assert MemoryClient().reembed_stale() == 0
    session.assert_not_called()


def test_re_embedding_stops_when_the_proxy_falls_back_midway():
    answers = iter([E5, E5, HASH_MODEL])

    class Flaky(_Proxy):
        def embed_with_model(self, text, *args, **kwargs):
            return [0.1] * 1536, next(answers)

    with (
        patch("llm_proxy_client.LLMProxyClient", Flaky(E5)),
        patch("memory_client.Session") as session,
    ):
        updates = _stale_db(session, [[(1, "a"), (2, "b")]])
        count = MemoryClient().reembed_stale()

    assert count == 1 and [u["id"] for u in updates] == [1]


def test_startup_re_embeds_in_the_background_and_a_failure_is_only_logged(monkeypatch):
    monkeypatch.setattr(lifecycle, "REEMBED_DELAY_SECONDS", 0)
    client = MagicMock()
    client.reembed_stale.return_value = 4
    app = SimpleNamespace(state=SimpleNamespace(memory_client=client))

    assert asyncio.run(lifecycle._reembed_memory_later(app)) == 4

    client.reembed_stale.side_effect = RuntimeError("database gone")
    assert asyncio.run(lifecycle._reembed_memory_later(app)) == 0


# ── Capabilities ────────────────────────────────────────────────────────


def _caps(embeddings):
    return {
        "memory": {"state": "on", "reason": "pgvector memory is kept"},
        "llm.embeddings": embeddings,
    }


def test_memory_is_on_when_the_local_model_is_loaded():
    caps = capabilities.with_memory_embedding(
        _caps({"state": "on", "provider": "local", "model": E5, "reason": "loaded"})
    )

    assert caps["memory"]["state"] == "on"
    assert E5 in caps["memory"]["reason"]


def test_memory_is_degraded_with_the_reason_when_the_hash_vector_stands_in():
    reason = "the local model multilingual-e5-small is not loaded (missing)"
    caps = capabilities.with_memory_embedding(
        _caps(
            {
                "state": "degraded",
                "provider": "local-hash",
                "model": HASH_MODEL,
                "reason": reason,
            }
        )
    )

    assert caps["memory"]["state"] == "degraded"
    assert reason in caps["memory"]["reason"]


def test_memory_without_a_table_or_a_proxy_answer_is_left_as_observed():
    off = {"memory": {"state": "off", "reason": "no vector_memory table"}}
    assert capabilities.with_memory_embedding(dict(off)) == off
    no_proxy = {"memory": {"state": "on", "reason": "kept"}, "llm": {"state": "off"}}
    assert capabilities.with_memory_embedding(dict(no_proxy))["memory"]["state"] == "on"
