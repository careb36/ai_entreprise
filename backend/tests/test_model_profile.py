"""A model's profile (``services/models/model_profile.py``, issue #766).

The capabilities come from the operator's override, then the catalogue,
then a safe default; the Developer's limits from the override, then the
settings; each field says where it came from, a malformed override is
ignored, the limits stay under fixed ceilings, and the answer's cap never
asks for more than the model returns. ``developer_tools.apply_limits`` puts
the limits on a toolbox.
"""

from __future__ import annotations

import pytest
from config import settings
from services.models import model_catalog, model_profile
from services.tools import developer_tools


@pytest.fixture(autouse=True)
def _snapshot_and_defaults(monkeypatch):
    monkeypatch.setattr(model_catalog, "_catalog", model_catalog._Catalog())
    monkeypatch.setattr(settings, "MODELS_CATALOG_REFRESH_HOURS", 0)
    monkeypatch.setattr(settings, "MODEL_PROFILES", {})
    monkeypatch.setattr(settings, "DEVELOPER_TOOL_ROUNDS", 8)
    monkeypatch.setattr(settings, "MCP_RESULT_MAX_CHARS", 4000)
    monkeypatch.setattr(settings, "DEVELOPER_MAX_OUTPUT_TOKENS", 0)


def test_a_catalogued_model_takes_its_capabilities_from_the_catalogue():
    p = model_profile.profile("deepseek-v4-pro")
    entry = model_catalog.provider("deepseek")["models"]["deepseek-v4-pro"]
    assert p["context"] == entry["context"] and p["output"] == entry["output"]
    assert p["sources"]["context"] == "catalog:deepseek"
    # With no override the limits are the settings', as before.
    assert (p["tool_rounds"], p["result_max_chars"], p["max_output_tokens"]) == (
        8,
        4000,
        0,
    )
    assert p["sources"]["tool_rounds"] == "setting"


def test_a_company_connection_name_is_looked_up_by_its_model():
    assert model_profile.bare_name("conn:7:deepseek-v4-pro") == "deepseek-v4-pro"
    assert model_profile.profile("conn:7:deepseek-v4-pro")["sources"]["output"] == (
        "catalog:deepseek"
    )


def test_a_model_nobody_describes_gets_the_safe_default():
    p = model_profile.profile("a-model-no-catalogue-has")
    assert p["context"] == model_profile.DEFAULT_CAPABILITIES["context"]
    assert p["output"] == model_profile.DEFAULT_CAPABILITIES["output"]
    assert {p["sources"][k] for k in model_profile.CAPABILITIES} == {"default"}


def test_the_operator_overrides_a_model(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MODEL_PROFILES",
        {"qwen4-max": {"context": 262144, "output": 65536, "tool_rounds": 12}},
    )
    p = model_profile.profile("qwen4-max")
    assert (p["context"], p["output"], p["tool_rounds"]) == (262144, 65536, 12)
    assert p["sources"]["context"] == "override"
    assert p["sources"]["tool_rounds"] == "override"
    assert p["sources"]["result_max_chars"] == "setting"


def test_a_malformed_override_is_ignored_not_applied(monkeypatch, caplog):
    monkeypatch.setattr(
        settings,
        "MODEL_PROFILES",
        {
            "m": {
                "tool_rounds": -3,
                "result_max_chars": "lots",
                "nonsense": 1,
                "context": 1000,
            }
        },
    )
    p = model_profile.profile("m")
    assert p["tool_rounds"] == 8 and p["result_max_chars"] == 4000
    assert p["context"] == 1000
    assert "not valid: ignored" in caplog.text


def test_the_limits_stay_under_the_ceilings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MODEL_PROFILES",
        {"m": {"tool_rounds": 10_000, "result_max_chars": 10**9}},
    )
    p = model_profile.profile("m")
    assert p["tool_rounds"] == model_profile.CEILINGS["tool_rounds"]
    assert p["result_max_chars"] == model_profile.CEILINGS["result_max_chars"]


def test_the_answer_cap_never_exceeds_what_the_model_returns(monkeypatch):
    monkeypatch.setattr(settings, "DEVELOPER_MAX_OUTPUT_TOKENS", 50_000)
    monkeypatch.setattr(settings, "MODEL_PROFILES", {"small": {"output": 8192}})
    assert model_profile.profile("small")["max_output_tokens"] == 8192
    assert model_profile.profile("deepseek-v4-pro")["max_output_tokens"] == 50_000


class _Box:
    def __init__(self, rounds: int) -> None:
        self.max_rounds, self.result_max_chars = rounds, 4000


def test_apply_limits_sets_the_workspace_rounds_and_every_result_budget():
    workspace = developer_tools.WorkspaceToolbox(object(), "/tmp/project")
    research, mcp = _Box(6), _Box(0)
    combined = developer_tools.CombinedToolbox(workspace, None, research)
    combined.mcp = mcp
    developer_tools.apply_limits(combined, rounds=14, result_max_chars=9000)
    assert workspace.max_rounds == 14 and combined.max_rounds == 14
    assert research.max_rounds == 6
    assert workspace.result_max_chars == mcp.result_max_chars == 9000

    alone = developer_tools.WorkspaceToolbox(object(), "/tmp/project")
    developer_tools.apply_limits(alone, rounds=3, result_max_chars=500)
    assert (alone.max_rounds, alone.result_max_chars) == (3, 500)
    developer_tools.apply_limits(None, rounds=3, result_max_chars=500)  # no tools
