"""The model catalogue (``services/model_catalog.py``; issue #356, step 2):
the shipped snapshot describes the providers La Fabrique can call through an
OpenAI-compatible client, with each model's capabilities, limits and price;
a refresh from models.dev replaces it and a failed one keeps it; a provider
connection's base URL comes from the catalogue; a company's models merge the
catalogue with what each connection listed at its validation."""

from __future__ import annotations

import httpx
import pytest
from config import settings
from models import ProviderConnection
from services import model_catalog, provider_connections
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company


@pytest.fixture(autouse=True)
def _snapshot_catalog(monkeypatch):
    """Each test starts from the snapshot and leaves it there."""
    monkeypatch.setattr(model_catalog, "_catalog", model_catalog._Catalog())
    monkeypatch.setattr(settings, "MODELS_CATALOG_REFRESH_HOURS", 0)


def test_the_snapshot_describes_providers_and_their_models():
    deepseek = model_catalog.provider("deepseek")
    assert deepseek["base_url"] == "https://api.deepseek.com/v1"
    pro = deepseek["models"]["deepseek-v4-pro"]
    assert pro["tool_call"] is True and pro["context"] > 0 and "cost_input" in pro
    summary = {p["id"]: p for p in model_catalog.providers_summary()}
    assert {"openrouter", "openai", "anthropic", "google", "groq", "lmstudio"} <= set(
        summary
    )
    assert summary["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert model_catalog.origin() == "snapshot"


def test_slim_catalog_keeps_only_providers_it_can_call():
    raw = {
        "compat": {
            "name": "Compat",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://c.example/v1/",
            "models": {
                "m1": {
                    "id": "m1",
                    "tool_call": True,
                    "limit": {"context": 8},
                    "cost": {"input": 1},
                }
            },
        },
        "native": {
            "name": "Native",
            "npm": "@ai-sdk/native",
            "models": {"x": {"id": "x"}},
        },
        "openai": {"name": "OpenAI", "npm": "@ai-sdk/openai", "models": {}},
    }
    slim = model_catalog.slim_catalog(raw)["providers"]
    assert set(slim) == {"compat", "openai"}
    assert slim["compat"]["base_url"] == "https://c.example/v1"
    assert slim["compat"]["models"]["m1"] == {
        "id": "m1",
        "tool_call": True,
        "context": 8,
        "cost_input": 1,
    }
    assert slim["openai"]["base_url"] == "https://api.openai.com/v1"


def test_a_refresh_replaces_the_catalogue_and_a_failed_one_keeps_it():
    payload = {
        "compat": {
            "name": "C",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://c/v1",
            "models": {},
        }
    }
    model_catalog.refresh(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    assert model_catalog.origin() == "live"
    assert [p["id"] for p in model_catalog.providers_summary()] == ["compat"]
    with pytest.raises(httpx.HTTPStatusError):
        model_catalog.refresh(
            transport=httpx.MockTransport(lambda r: httpx.Response(503))
        )
    with pytest.raises(ValueError, match="no usable provider"):
        model_catalog.refresh(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
    assert [p["id"] for p in model_catalog.providers_summary()] == ["compat"]


def test_a_connection_takes_its_base_url_from_the_catalogue():
    assert (
        provider_connections.normalize_base_url("moonshotai", None)
        == "https://api.moonshot.ai/v1"
    )
    assert (
        provider_connections.normalize_base_url("lmstudio", None)
        == "http://127.0.0.1:1234/v1"
    )
    assert (
        provider_connections.normalize_base_url("ollama", None)
        == "http://localhost:11434/v1"
    )
    with pytest.raises(provider_connections.ProviderConfigError):
        provider_connections.normalize_base_url("not-a-provider", None)


def test_the_catalogue_routes_are_public(test_client):
    listed = test_client.get("/v1/models/catalog")
    assert listed.status_code == 200 and listed.json()["origin"] == "snapshot"
    assert any(p["id"] == "deepseek" for p in listed.json()["providers"])
    one = test_client.get("/v1/models/catalog/deepseek")
    assert one.status_code == 200
    assert any(m["id"] == "deepseek-v4-pro" for m in one.json()["models"])
    assert test_client.get("/v1/models/catalog/nope").status_code == 404


@pytest.mark.usefixtures("enforcement")
def test_a_companys_models_merge_the_catalogue_with_what_the_provider_listed(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Viewer")
    row = ProviderConnection(
        company_id=company.id,
        provider="deepseek",
        name="DS",
        base_url="https://api.deepseek.com/v1",
        listed_models=["deepseek-v4-pro", "deepseek-new"],
        validation_ok=True,
    )
    off = ProviderConnection(
        company_id=company.id,
        provider="groq",
        name="off",
        base_url="https://g/v1",
        enabled=False,
    )
    sqlite_session.add_all([row, off])
    sqlite_session.commit()
    url = f"/v1/companies/{company.id}/models"
    assert test_client.get(url, headers=outsider).status_code == 403
    body = test_client.get(url, headers=viewer).json()
    assert [c["name"] for c in body["connections"]] == [
        "DS"
    ]  # disabled ones are left out
    models = {m["id"]: m for m in body["connections"][0]["models"]}
    assert (
        models["deepseek-v4-pro"]["listed"] and models["deepseek-v4-pro"]["in_catalog"]
    )
    assert models["deepseek-v4-pro"]["tool_call"] is True
    assert models["deepseek-new"] == {
        "id": "deepseek-new",
        "in_catalog": False,
        "listed": True,
    }
    assert (
        models["deepseek-flash"]["listed"] is False
        and models["deepseek-flash"]["in_catalog"]
    )


def test_a_successful_validation_keeps_the_listed_models():
    row = ProviderConnection(
        company_id=1, provider="deepseek", name="d", base_url="https://x/v1"
    )
    provider_connections.validate(
        row,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
        ),
    )
    assert row.listed_models == ["a", "b"]
    assert provider_connections.serialize(row)["listed_models"] == ["a", "b"]
