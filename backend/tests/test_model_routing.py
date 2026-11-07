"""Model routing by task difficulty (services.model_routing)."""

from services import model_routing


def test_estimate_difficulty_increases_with_criteria():
    item = {"covers": ["REQ-1.1", "REQ-1.2"]}
    assert model_routing.estimate_difficulty(item) >= 2


def test_rework_raises_difficulty():
    item = {"covers": ["REQ-1.1"]}
    base = model_routing.estimate_difficulty(item, rework=False)
    rework = model_routing.estimate_difficulty(item, rework=True)
    assert rework >= base


def test_model_for_band_returns_model():
    policy = {"bands": {"1-2": "fast", "3-5": "powerful"}}
    assert model_routing.model_for("Developer", 1, policy) == "fast"
    assert model_routing.model_for("Developer", 4, policy) == "powerful"


def test_model_for_no_policy_returns_none():
    assert model_routing.model_for("Developer", 3, {}) is None
