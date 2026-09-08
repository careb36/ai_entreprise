from llm.model_factory import AgentRole, ModelFactory, create_completion


def test_role_model_map_uses_gemini_2_5_models():
    assert ModelFactory.ROLE_MODEL_MAP[AgentRole.PO].model_name == "gemini-2.5-pro"
    assert ModelFactory.ROLE_MODEL_MAP[AgentRole.ARCHITECT].model_name == "gemini-2.5-pro"
    assert ModelFactory.ROLE_MODEL_MAP[AgentRole.SCRUM_MASTER].model_name == "gemini-2.5-pro"
    assert ModelFactory.ROLE_MODEL_MAP[AgentRole.SECOPS].model_name == "gemini-2.5-pro"
    assert ModelFactory.ROLE_MODEL_MAP[AgentRole.TECH_WRITER].model_name == "gemini-2.5-flash"


def test_create_completion_passes_bu_header(monkeypatch):
    captured = {}

    class _Response:
        content = "ok"

    class _DummyModel:
        def invoke(self, messages, **config):
            captured["config"] = config
            return _Response()

    monkeypatch.setattr("llm.model_factory.get_llm_for_role", lambda _role: _DummyModel())

    result = create_completion(role="Developer", prompt="hello", bu_id=7)

    assert result == "ok"
    assert captured["config"] == {"headers": {"X-Business-Unit-ID": "7"}}
