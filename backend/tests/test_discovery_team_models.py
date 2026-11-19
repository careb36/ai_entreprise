"""The suggested team in New company runs the deployment's default models.

PR #739 made the default team DeepSeek alone; the suggestion must follow it
whichever path built the team (the model's answer or the rules), whatever
model the answer names and whatever the live catalogue lists, while the
deployment's own settings (``AGENT_MODEL``, ``AGENT_MODEL_<ROLE>``) still win.
"""

import json
from pathlib import Path

import llm_proxy_client
import pytest
from services.planning import discovery_service
from services.planning.discovery_team import _TEAM_PROMPT, _build_team

PRO = "deepseek-v4-pro"
FLASH = "deepseek-flash"
EXPECTED = {
    "PO": PRO,
    "Architect": PRO,
    "Security": PRO,
    "SM": FLASH,
    "Dev": FLASH,
    "QA": FLASH,
    "DevOps": FLASH,
    "Data": FLASH,
}

STACK = {
    "language": "Python",
    "all_languages": {"Python": 40},
    "frameworks": ["FastAPI"],
    "databases": ["PostgreSQL"],
    "infra": ["docker"],
    "complexity": "High",
    "file_count": 40,
}


@pytest.fixture(autouse=True)
def _no_model_settings(monkeypatch):
    for name in ("AGENT_MODEL", "NEMOTRON_ROLES", "NEMOTRON_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for role in (
        "PO",
        "ARCHITECT",
        "SCRUM_MASTER",
        "DEVELOPER",
        "QA",
        "SECOPS",
        "DEVOPS",
        "DATA_ENGINEER",
    ):
        monkeypatch.delenv(f"AGENT_MODEL_{role}", raising=False)


def _fake_client(
    monkeypatch, catalogue: dict, answer_model: str = "gemini-3.1-pro-preview"
):
    """A proxy client whose answer names *answer_model* for every agent."""
    calls: list[dict] = []
    team = [
        {
            "role": role,
            "seniority": "Senior",
            "specialty": f"{role} work",
            "model": answer_model,
            "system_prompt": "I do the work.",
            "reasoning": "The project needs it.",
        }
        for role in EXPECTED
    ]

    class FakeClient:
        def __init__(self, base_url=None, **_):
            self.base_url = base_url

        def get_available_models(self):
            calls.append({"catalogue": True})
            return catalogue

        def chat(self, messages, model=None, role=None, **_):
            calls.append(
                {"model": model, "role": role, "prompt": messages[0]["content"]}
            )
            return {"choices": [{"message": {"content": json.dumps(team)}}]}

    monkeypatch.setattr(llm_proxy_client, "LLMProxyClient", FakeClient)
    return calls


GEMINI_CATALOGUE = {
    "models": [
        {
            "id": "gemini-3.1-pro-preview",
            "provider": "gemini",
            "tier": "strategic",
            "description": "x",
        },
        {
            "id": "gemini-3.8-flash",
            "provider": "gemini",
            "tier": "code",
            "description": "x",
        },
    ],
    "orchestrator": "gemini-3.8-flash",
}


def test_the_model_answer_never_chooses_the_model(monkeypatch, tmp_path: Path):
    calls = _fake_client(monkeypatch, GEMINI_CATALOGUE)
    team = discovery_service._ai_suggest_team(STACK, tmp_path, "http://proxy")
    assert team is not None
    assert {agent["role"]: agent["model"] for agent in team} == EXPECTED
    chats = [c for c in calls if "model" in c]
    # The request runs on the Architect's default, not the proxy's orchestrator.
    assert chats == [{"model": PRO, "role": "Architect", "prompt": chats[0]["prompt"]}]
    assert "gemini" not in chats[0]["prompt"]


def test_an_empty_catalogue_still_suggests_a_team(monkeypatch, tmp_path: Path):
    _fake_client(monkeypatch, {})
    team = discovery_service._ai_suggest_team(STACK, tmp_path, "http://proxy")
    assert team is not None
    assert {agent["role"]: agent["model"] for agent in team} == EXPECTED


def test_the_prompt_asks_for_no_model():
    assert "{model_catalog}" not in _TEAM_PROMPT
    assert '"model"' not in _TEAM_PROMPT
    assert not hasattr(discovery_service, "_build_dynamic_catalog")


def test_the_rules_fallback_sets_each_role_default():
    team = _build_team(
        "Python", ["FastAPI", "LangGraph"], ["PostgreSQL"], {"docker": True}, "High"
    )
    assert team
    for agent in team:
        assert agent["model"] == EXPECTED[agent["role"]], agent


def test_the_service_falls_back_to_rules_with_default_models(
    monkeypatch, tmp_path: Path
):
    (tmp_path / "app.py").write_text("print('hi')\n")
    monkeypatch.setattr(discovery_service, "_ai_suggest_team", lambda *a, **k: None)
    service = discovery_service.DiscoveryService(
        session=None, llm_proxy_url="http://proxy"
    )
    _, stack, team = service.discover_project_team(None, str(tmp_path))
    assert stack["team_source"] == "rules"
    assert team and all(agent["model"] == EXPECTED[agent["role"]] for agent in team)


def test_a_deployment_setting_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_MODEL", "qwen3.6-flash")
    monkeypatch.setenv("AGENT_MODEL_ARCHITECT", "custom-architect")
    _fake_client(monkeypatch, GEMINI_CATALOGUE)
    team = discovery_service._ai_suggest_team(STACK, tmp_path, "http://proxy")
    models = {agent["role"]: agent["model"] for agent in team}
    assert models["Architect"] == "custom-architect"
    assert {m for role, m in models.items() if role != "Architect"} == {"qwen3.6-flash"}
    rules = _build_team("Python", [], [], {}, "Low")
    assert {a["role"]: a["model"] for a in rules}["Architect"] == "custom-architect"
    assert {a["model"] for a in rules if a["role"] != "Architect"} == {"qwen3.6-flash"}


def test_an_unknown_role_becomes_a_developer_on_its_default(
    monkeypatch, tmp_path: Path
):
    calls: list = []

    class FakeClient:
        def __init__(self, **_):
            pass

        def chat(self, messages, model=None, role=None, **_):
            calls.append(model)
            content = json.dumps([{"role": "Wizard", "model": "gemini-3.8-flash"}])
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(llm_proxy_client, "LLMProxyClient", FakeClient)
    team = discovery_service._ai_suggest_team(STACK, tmp_path, "http://proxy")
    assert [(a["role"], a["model"]) for a in team] == [("Dev", FLASH)]
