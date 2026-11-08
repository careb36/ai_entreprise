"""The model suggested for each role (``services/model_suggestions.py``,
``GET /v1/companies/{id}/model_suggestions``; issue #356, step 6): a
deterministic rule over the catalogue's real fields; the roles that decide get
a model that reasons, the coding roles the largest context with tools, the
TechWriter the cheapest; a model without tools is never suggested, nor one a
validated provider did not list; each suggestion is the connection's own
reference; a Viewer reads them, nobody outside the company does."""

from __future__ import annotations

import pytest
from models import ProviderConnection
from services import model_catalog, model_suggestions
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company

CATALOG = [
    {
        "id": "big-thinker",
        "name": "Big",
        "tool_call": True,
        "reasoning": True,
        "context": 1_000_000,
        "cost_input": 3.0,
    },
    {
        "id": "coder",
        "name": "Coder",
        "tool_call": True,
        "context": 2_000_000,
        "cost_input": 1.0,
    },
    {
        "id": "cheap",
        "name": "Cheap",
        "tool_call": True,
        "context": 64_000,
        "cost_input": 0.1,
    },
    {
        "id": "no-tools",
        "name": "Chat",
        "reasoning": True,
        "context": 9_000_000,
        "cost_input": 0.01,
    },
]


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(
        model_catalog, "models_for", lambda provider: [dict(m) for m in CATALOG]
    )


def _connection(session, company, name="acme", listed=None, ok=None):
    row = ProviderConnection(
        company_id=company.id,
        provider="openrouter",
        name=name,
        base_url="https://openrouter.example/api/v1",
        listed_models=listed,
        validation_ok=ok,
    )
    session.add(row)
    session.commit()
    return row


def test_each_role_gets_the_rule_s_model(sqlite_session, catalog):
    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company)
    models = model_suggestions.candidates([conn])
    assert {m["id"] for m in models} == {"big-thinker", "coder", "cheap"}
    suggestions = model_suggestions.suggest(models)
    assert suggestions["Architect"]["model"] == f"conn:{conn.id}:big-thinker"
    assert suggestions["ScrumMaster"]["model"] == f"conn:{conn.id}:big-thinker"
    assert suggestions["Developer"]["model"] == f"conn:{conn.id}:coder"
    assert suggestions["Reviewer"]["model"] == f"conn:{conn.id}:coder"
    assert suggestions["TechWriter"]["model"] == f"conn:{conn.id}:cheap"
    assert suggestions["Developer"]["connection"] == "acme"
    assert "tools" in suggestions["Developer"]["reason"]


def test_a_validated_provider_only_suggests_what_it_listed(sqlite_session, catalog):
    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=["cheap", "no-tools"], ok=True)
    suggestions = model_suggestions.suggest(model_suggestions.candidates([conn]))
    assert suggestions["Developer"]["model"] == f"conn:{conn.id}:cheap"
    # Nothing listed reasons and calls tools: no suggestion for those roles.
    assert suggestions["PO"] is None


def test_no_model_with_tools_means_no_suggestion():
    assert set(model_suggestions.suggest([]).values()) == {None}


def test_an_unpriced_model_is_not_taken_for_free():
    models = [
        {"id": "x", "ref": "conn:1:x", "connection": "c", "tool_call": True},
        {
            "id": "y",
            "ref": "conn:1:y",
            "connection": "c",
            "tool_call": True,
            "cost_input": 2.0,
        },
    ]
    assert model_suggestions.suggest(models)["TechWriter"]["model"] == "conn:1:y"


@pytest.mark.usefixtures("enforcement")
def test_a_viewer_reads_the_suggestions(sqlite_session, test_client, catalog):
    company, other = _company(sqlite_session), _company(sqlite_session)
    conn = _connection(sqlite_session, company)
    _connection(sqlite_session, company, name="off").enabled = False
    sqlite_session.commit()
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/model_suggestions"
    assert test_client.get(url, headers=outsider).status_code == 403
    response = test_client.get(url, headers=viewer)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidates"] == 3
    assert data["suggestions"]["QA"]["model"] == f"conn:{conn.id}:coder"
