"""Tests for the public ``GET /v1/capabilities`` endpoint."""

from unittest.mock import MagicMock

import pytest
from config import settings

from app.main import app


@pytest.fixture
def default_settings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_GRAPH_ENABLED", False)
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "PUBLIC_API_URL", None)
    monkeypatch.setattr(settings, "SUPPORT_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "NEO4J_ENABLED", False)
    monkeypatch.setattr(settings, "FORGE_POLICY_MAX", "publish")


def _state_setter(name, initial):
    """Sets ``app.state.<name>`` for a test and puts the old value back.

    Not through monkeypatch: since pytest 9.2 it records an instance attribute
    from ``__dict__``, and starlette's ``State`` keeps its values elsewhere, so
    the undo deletes the attribute once per ``setattr`` and the second delete
    raises ``KeyError``.
    """
    missing = object()
    previous = getattr(app.state, name, missing)

    def set_value(value):
        setattr(app.state, name, value)

    set_value(initial)
    yield set_value
    if previous is missing:
        if hasattr(app.state, name):
            delattr(app.state, name)
    else:
        setattr(app.state, name, previous)


@pytest.fixture
def memory_client():
    yield from _state_setter("memory_client", None)


@pytest.fixture
def memory_graph():
    yield from _state_setter("memory_graph", False)


def test_default_capabilities(test_client, default_settings):
    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "agent_graph": False,
        "memory": False,
        "memory_graph": False,
        "github": False,
        "auth_required": True,
        "support_agent": False,
        "public_api_url": None,
        "forge_policy_max": "publish",
    }


def test_agent_graph_enabled(test_client, default_settings, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_GRAPH_ENABLED", True)

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["agent_graph"] is True


def test_memory_follows_memory_client(test_client, default_settings, memory_client):
    memory_client(MagicMock())
    assert test_client.get("/v1/capabilities").json()["memory"] is True

    memory_client(None)
    assert test_client.get("/v1/capabilities").json()["memory"] is False


def test_memory_graph_is_reported_apart_from_memory(
    test_client, default_settings, memory_client, memory_graph, monkeypatch
):
    # Memory search on pgvector alone: the graph is off by default.
    memory_client(MagicMock())
    body = test_client.get("/v1/capabilities").json()
    assert body["memory"] is True
    assert body["memory_graph"] is False

    # Connected at startup, but only while the setting keeps it on.
    memory_graph(True)
    assert test_client.get("/v1/capabilities").json()["memory_graph"] is False
    monkeypatch.setattr(settings, "NEO4J_ENABLED", True)
    assert test_client.get("/v1/capabilities").json()["memory_graph"] is True


def test_github_token_enables_github(test_client, default_settings, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["github"] is True


def test_public_when_auth_required(test_client, default_settings):
    # Sanity check: the same client is rejected on a protected route.
    assert test_client.get("/v1/agent_runs?company_id=1").status_code == 401

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["auth_required"] is True


def test_public_api_url_is_reported_without_trailing_slash(
    test_client, default_settings, monkeypatch
):
    monkeypatch.setattr(settings, "PUBLIC_API_URL", "https://fabrique.example.com/")

    assert test_client.get("/v1/capabilities").json()["public_api_url"] == (
        "https://fabrique.example.com"
    )


def test_compose_keeps_the_agent_graph_off_by_default():
    """docker-compose.yml defaults AGENT_GRAPH_ENABLED to what config.py and
    .env.example default it to: off (the graph is an optional producer)."""
    from pathlib import Path

    import config
    import yaml

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    env = yaml.safe_load(compose.read_text())["services"]["backend"]["environment"]
    assert env["AGENT_GRAPH_ENABLED"] == "${AGENT_GRAPH_ENABLED:-false}"
    assert config.Settings.model_fields["AGENT_GRAPH_ENABLED"].default is False
