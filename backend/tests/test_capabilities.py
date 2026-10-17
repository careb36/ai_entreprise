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


def test_default_capabilities(test_client, default_settings):
    app.state.memory_client = None

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "agent_graph": False,
        "memory": False,
        "github": False,
        "auth_required": True,
    }


def test_agent_graph_enabled(test_client, default_settings, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_GRAPH_ENABLED", True)

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["agent_graph"] is True


def test_memory_follows_memory_client(test_client, default_settings):
    app.state.memory_client = MagicMock()
    try:
        assert test_client.get("/v1/capabilities").json()["memory"] is True
    finally:
        app.state.memory_client = None

    assert test_client.get("/v1/capabilities").json()["memory"] is False


def test_github_token_enables_github(test_client, default_settings, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")

    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["github"] is True


def test_public_when_auth_required(test_client, default_settings):
    response = test_client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["auth_required"] is True
