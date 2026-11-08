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


def test_flat_catalogue_shape():
    # The shape produced by model_catalog.connection_models (flat fields).
    models = [
        {
            "id": "conn:1:a",
            "tool_call": True,
            "reasoning": True,
            "context": 100000,
            "cost_input": 1.0,
        },
        {
            "id": "conn:1:b",
            "tool_call": True,
            "reasoning": False,
            "context": 50000,
            "cost_input": 0.5,
        },
    ]
    s = model_suggestions.suggest(models)
    assert s["PO"] == "conn:1:a"
    assert s["Developer"] == "conn:1:a"
    assert s["TechWriter"] == "conn:1:b"


def test_deciding_roles_none_without_reasoning():
    models = [
        {
            "id": "x",
            "tool_call": True,
            "reasoning": False,
            "context": 10,
            "cost_input": 1.0,
        }
    ]
    s = model_suggestions.suggest(models)
    assert s["PO"] is None
    assert s["Developer"] == "x"


def test_empty_models_returns_empty():
    assert model_suggestions.suggest([]) == {}
