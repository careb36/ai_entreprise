"""A company's provider connection reaches the LLM proxy (issue #356, step 3):
an agent's ``conn:<id>:<model>`` is resolved by the backend into the model the
proxy calls and the headers that carry the connection (with the shared
internal token); only the company's own enabled connection is used, never
another company's; without the token or the company the call fails with the
reason instead of reaching any provider; plain model ids are untouched."""

from __future__ import annotations

import base64
import json

import pytest
from config import settings
from factories import company as _company
from llm_proxy_client import LLMProxyClient
from models import ProviderConnection
from services.models import provider_connections


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROXY_INTERNAL_TOKEN", "internal-secret")
    return "internal-secret"


@pytest.fixture
def connection(sqlite_session):
    company = _company(sqlite_session)
    row = ProviderConnection(
        company_id=company.id,
        provider="openrouter",
        name="Router",
        base_url="https://openrouter.ai/api/v1",
    )
    provider_connections.set_secrets(row, "sk-company", {"X-Title": "Fabrique"})
    sqlite_session.add(row)
    sqlite_session.commit()
    sqlite_session.refresh(row)
    return company, row


def _engine(sqlite_session):
    return sqlite_session.get_bind()


def test_model_refs_parse_and_build():
    assert (
        provider_connections.model_ref(7, "anthropic/claude-x")
        == "conn:7:anthropic/claude-x"
    )
    assert provider_connections.parse_model_ref("conn:7:anthropic/claude-x") == (
        7,
        "anthropic/claude-x",
    )
    assert provider_connections.parse_model_ref("deepseek-v4-pro") is None
    for bad in ("conn:x:model", "conn:7:", "conn:7"):
        with pytest.raises(provider_connections.ProviderConfigError):
            provider_connections.parse_model_ref(bad)


def test_a_companys_connection_routes_with_its_key(sqlite_session, connection, token):
    company, row = connection
    model, headers = provider_connections.proxy_route(
        f"conn:{row.id}:anthropic/claude-x", company.id, _engine(sqlite_session)
    )
    assert model == "anthropic/claude-x"
    assert headers["X-Internal-Token"] == token
    sent = json.loads(base64.b64decode(headers["X-Provider-Connection"]))
    assert sent == {
        "name": "Router",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-company",
        "headers": {"X-Title": "Fabrique"},
    }
    assert provider_connections.proxy_route("deepseek-v4-pro", company.id) == (
        "deepseek-v4-pro",
        {},
    )


def test_another_companys_or_a_disabled_connection_is_refused(
    sqlite_session, connection, token
):
    company, row = connection
    other = _company(sqlite_session)
    ref = f"conn:{row.id}:m"
    with pytest.raises(
        provider_connections.ProviderConfigError, match="not this company's"
    ):
        provider_connections.proxy_route(ref, other.id, _engine(sqlite_session))
    with pytest.raises(
        provider_connections.ProviderConfigError, match="needs the company"
    ):
        provider_connections.proxy_route(ref, None, _engine(sqlite_session))
    row.enabled = False
    sqlite_session.add(row)
    sqlite_session.commit()
    with pytest.raises(provider_connections.ProviderConfigError, match="disabled"):
        provider_connections.proxy_route(ref, company.id, _engine(sqlite_session))


def test_without_the_shared_token_connections_are_off(
    sqlite_session, connection, monkeypatch
):
    company, row = connection
    monkeypatch.setattr(settings, "LLM_PROXY_INTERNAL_TOKEN", "")
    with pytest.raises(
        provider_connections.ProviderConfigError, match="LLM_PROXY_INTERNAL_TOKEN"
    ):
        provider_connections.proxy_route(
            f"conn:{row.id}:m", company.id, _engine(sqlite_session)
        )


def test_the_client_sends_the_connection_and_refuses_what_it_cannot_route(
    sqlite_session, connection, token, monkeypatch
):
    company, row = connection
    import llm_proxy_client

    sent = {}

    class _Response:
        ok, status_code, text = True, 200, ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(json=json, headers=headers)
        return _Response()

    monkeypatch.setattr(llm_proxy_client.requests, "post", fake_post)
    real = provider_connections.proxy_route
    monkeypatch.setattr(
        provider_connections,
        "proxy_route",
        lambda model, company_id, engine=None: real(
            model, company_id, _engine(sqlite_session)
        ),
    )
    client = LLMProxyClient("http://proxy")
    client.company_id = company.id
    result = client.chat(
        messages=[{"role": "user", "content": "hi"}],
        model=f"conn:{row.id}:anthropic/claude-x",
    )
    assert result["choices"][0]["message"]["content"] == "ok"
    assert sent["json"]["model"] == "anthropic/claude-x"
    assert (
        sent["headers"]["X-Internal-Token"] == token
        and "X-Provider-Connection" in sent["headers"]
    )
    # Another company's client never reaches the proxy with it.
    sent.clear()
    client.company_id = _company(sqlite_session).id
    refused = client.chat(messages=[], model=f"conn:{row.id}:m")
    assert (
        refused["ok"] is False
        and "not this company's" in refused["error"]
        and sent == {}
    )


def test_scrum_master_and_authoring_clients_carry_the_company(monkeypatch):
    """A ``conn:`` model needs the company on the client: the ScrumMaster's
    questions and the PO's and Architect's drafts pass it on."""
    import llm_proxy_client
    from services.learning import scrum_master
    from services.planning import spec_author

    # Through its shim, ``app.services.learning.scrum_master`` is now the one module
    # conftest stubs, so the real client is put back for this test.
    monkeypatch.setattr(scrum_master, "LLMProxyClient", llm_proxy_client.LLMProxyClient)
    seen = []

    def fake_chat(self, messages, model, role=None, bu_id=None, **kwargs):
        seen.append(self.company_id)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("llm_proxy_client.LLMProxyClient.chat", fake_chat)
    scrum_master.ask_model("conn:1:m", None, "hi", company_id=7)
    scrum_master.ask_triage("conn:1:m", None, "hi", 8)
    spec_author.ask("conn:1:m", None, "hi", company_id=9)
    spec_author.ask_with_tools("conn:1:m", None, "hi", company_id=10)
    assert seen == [7, 8, 9, 10]
