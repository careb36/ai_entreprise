"""Model suggestions (services.model_suggestions)."""

from services import model_suggestions


def test_suggests_per_role():
    models = [
        {
            "id": "a",
            "capabilities": {"tool_call": True, "reasoning": True},
            "limit": {"context": 100000},
            "cost": {"input": 1.0},
        },
        {
            "id": "b",
            "capabilities": {"tool_call": True},
            "limit": {"context": 50000},
            "cost": {"input": 0.5},
        },
    ]
    s = model_suggestions.suggest(models)
    assert s["PO"] == "a"
    assert s["Developer"] == "a"
    assert s["TechWriter"] == "b"


def test_empty_models_returns_empty():
    assert model_suggestions.suggest([]) == {}
