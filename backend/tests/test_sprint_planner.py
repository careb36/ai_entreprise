"""
Unit tests for backend/app/services/sprint_planner.py — deterministic helpers.

These tests exercise the public functions and the internal deterministic helpers
*without* making any LLM calls.  Sprint planning functions that call the LLM
proxy are tested via the fallback paths (no model or no stack data).
"""

from __future__ import annotations

import pytest

from services.sprint_planner import (
    _build_stack_summary,
    _build_team_roster,
    _filter_fallback_tasks,
    _find_po_agent,
    _ensure_execution_team,
    _build_execution_fallback,
    _build_self_improvement_fallback,
    build_self_improvement_objective,
    plan_foundation_sprint,
)


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_FULL_TEAM = [
    {"name": "Atlas", "role": "PO", "seniority": "Senior", "specialty": "product", "model": "gemini-2.5-pro"},
    {"name": "Nova", "role": "Architect", "seniority": "Staff", "specialty": "design", "model": "gemini-2.5-pro"},
    {"name": "Dev", "role": "Developer", "seniority": "Mid", "specialty": "coding", "model": "deepseek-coder"},
    {"name": "Tester", "role": "QA", "seniority": "Senior", "specialty": "quality", "model": "deepseek-coder"},
]

_STACK = {
    "language": "Python",
    "all_languages": {"Python": 42, "TypeScript": 10},
    "frameworks": ["FastAPI", "React"],
    "databases": ["PostgreSQL", "Neo4j"],
    "infra": ["Docker", "NATS"],
    "complexity": "high",
    "file_count": 120,
}


# ---------------------------------------------------------------------------
# _build_stack_summary
# ---------------------------------------------------------------------------

class TestBuildStackSummary:
    def test_full_stack_renders_all_fields(self):
        summary = _build_stack_summary(_STACK)
        assert "Python" in summary
        assert "FastAPI" in summary
        assert "PostgreSQL" in summary
        assert "Docker" in summary
        assert "high" in summary
        assert "120" in summary

    def test_empty_stack_returns_fallback_string(self):
        summary = _build_stack_summary({})
        assert "No stack data available." in summary

    def test_partial_stack_does_not_raise(self):
        summary = _build_stack_summary({"language": "Go"})
        assert "Go" in summary


# ---------------------------------------------------------------------------
# _build_team_roster
# ---------------------------------------------------------------------------

class TestBuildTeamRoster:
    def test_renders_all_agents(self):
        roster = _build_team_roster(_FULL_TEAM)
        assert "Atlas" in roster
        assert "PO" in roster
        assert "Architect" in roster
        assert "deepseek-coder" in roster

    def test_empty_team_returns_placeholder(self):
        roster = _build_team_roster([])
        assert "(no agents defined)" in roster


# ---------------------------------------------------------------------------
# _find_po_agent
# ---------------------------------------------------------------------------

class TestFindPoAgent:
    def test_returns_po_when_present(self):
        po = _find_po_agent(_FULL_TEAM)
        assert po["role"] == "PO"

    def test_falls_back_to_architect_if_no_po(self):
        team_no_po = [a for a in _FULL_TEAM if a["role"] != "PO"]
        po = _find_po_agent(team_no_po)
        assert po["role"] == "Architect"

    def test_falls_back_to_first_agent_if_no_po_or_architect(self):
        team = [{"name": "D", "role": "Developer", "model": ""}]
        po = _find_po_agent(team)
        assert po["role"] == "Developer"

    def test_empty_list_returns_default(self):
        po = _find_po_agent([])
        assert po["role"] == "PO"


# ---------------------------------------------------------------------------
# _ensure_execution_team
# ---------------------------------------------------------------------------

class TestEnsureExecutionTeam:
    def test_returns_provided_team_when_non_empty(self):
        result = _ensure_execution_team(_FULL_TEAM)
        assert result is _FULL_TEAM

    def test_returns_default_team_when_empty(self):
        result = _ensure_execution_team([])
        roles = {a["role"] for a in result}
        assert "PO" in roles
        assert "Developer" in roles
        assert "QA" in roles


# ---------------------------------------------------------------------------
# _filter_fallback_tasks
# ---------------------------------------------------------------------------

class TestFilterFallbackTasks:
    def test_filters_to_available_roles(self):
        agents = [{"role": "PO"}, {"role": "QA"}]
        tasks = _filter_fallback_tasks(agents)
        assignees = {t["assignee"] for t in tasks}
        # Only PO and QA tasks should survive.
        assert assignees.issubset({"PO", "QA"})

    def test_full_team_includes_more_tasks(self):
        all_agents = [
            {"role": "PO"}, {"role": "Architect"}, {"role": "Dev"},
            {"role": "QA"}, {"role": "DevOps"}, {"role": "Security"},
        ]
        tasks_full = _filter_fallback_tasks(all_agents)
        tasks_partial = _filter_fallback_tasks([{"role": "PO"}])
        assert len(tasks_full) >= len(tasks_partial)


# ---------------------------------------------------------------------------
# _build_execution_fallback
# ---------------------------------------------------------------------------

class TestBuildExecutionFallback:
    def test_returns_required_keys(self):
        plan = _build_execution_fallback("Build a widget", _FULL_TEAM, "M")
        assert "sprint_name" in plan
        assert "goal" in plan
        assert "tasks" in plan

    def test_scope_s_limits_tasks_to_four(self):
        plan = _build_execution_fallback("Do it", _FULL_TEAM, "S")
        assert len(plan["tasks"]) <= 4

    def test_scope_l_allows_up_to_twelve(self):
        plan = _build_execution_fallback("Do it", _FULL_TEAM, "L")
        assert len(plan["tasks"]) <= 12

    def test_tasks_have_required_fields(self):
        plan = _build_execution_fallback("Do it", _FULL_TEAM, "M")
        for task in plan["tasks"]:
            assert "title" in task
            assert "description" in task
            assert "priority" in task
            assert "assignee" in task

    def test_goal_contains_objective_prefix(self):
        plan = _build_execution_fallback("implement login", _FULL_TEAM, "M")
        assert "implement login" in plan["goal"]


# ---------------------------------------------------------------------------
# _build_self_improvement_fallback
# ---------------------------------------------------------------------------

class TestBuildSelfImprovementFallback:
    def test_returns_required_keys(self):
        plan = _build_self_improvement_fallback("MyFactory", _FULL_TEAM, "M")
        assert "sprint_name" in plan
        assert "goal" in plan
        assert "tasks" in plan

    def test_goal_mentions_company_name(self):
        plan = _build_self_improvement_fallback("MyFactory", _FULL_TEAM, "M")
        assert "MyFactory" in plan["goal"]

    def test_tasks_not_empty(self):
        plan = _build_self_improvement_fallback("AFactory", _FULL_TEAM, "M")
        assert len(plan["tasks"]) > 0


# ---------------------------------------------------------------------------
# build_self_improvement_objective
# ---------------------------------------------------------------------------

class TestBuildSelfImprovementObjective:
    def test_returns_non_empty_string(self):
        obj = build_self_improvement_objective("AcmeCorp")
        assert isinstance(obj, str)
        assert "AcmeCorp" in obj

    def test_appends_retro_signals_when_provided(self):
        metrics = {
            "completion_rate": 70,
            "rework_cycles": 5,
            "qa_failures": 3,
            "blocked_tasks": 2,
        }
        obj = build_self_improvement_objective("AcmeCorp", sprint_metrics=metrics)
        assert "70%" in obj
        assert "rework" in obj.lower() or "cycles" in obj.lower()

    def test_no_retro_signals_without_metrics(self):
        obj = build_self_improvement_objective("AcmeCorp")
        assert "Retro signals" not in obj


# ---------------------------------------------------------------------------
# plan_foundation_sprint — fallback path (no model / no stack)
# ---------------------------------------------------------------------------

class TestPlanFoundationSprintFallback:
    def test_no_model_uses_fallback(self):
        """When agents have no model, plan falls back to deterministic tasks."""
        agents = [{"role": "PO", "model": ""}, {"role": "Developer", "model": ""}]
        plan = plan_foundation_sprint(
            company_name="NullModel Corp",
            agents=agents,
            detected_stack={"language": "Python"},
            project_path=None,
        )
        assert "goal" in plan
        assert isinstance(plan["tasks"], list)

    def test_no_stack_uses_fallback(self):
        """When detected_stack is None, plan falls back to deterministic tasks."""
        plan = plan_foundation_sprint(
            company_name="NoStack Corp",
            agents=_FULL_TEAM,
            detected_stack=None,
            project_path=None,
        )
        assert "goal" in plan
        assert isinstance(plan["tasks"], list)

    def test_fallback_tasks_respect_available_roles(self):
        """Fallback tasks should only be assigned to roles that exist in the team."""
        agents = [{"role": "PO", "model": ""}]
        plan = plan_foundation_sprint(
            company_name="Solo Corp",
            agents=agents,
            detected_stack=None,
            project_path=None,
        )
        for task in plan["tasks"]:
            assert task["assignee"] == "PO"
