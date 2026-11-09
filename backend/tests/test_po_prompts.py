"""
Tests for Product Owner Agent Prompts
Ensures prompts adhere to factory standards and sprint requirements.
"""

from agents.po.prompts import PO_TASK_GENERATION_PROMPT


def test_po_task_generation_prompt_enforces_subtasks():
    """
    Verify that the prompt explicitly requires breaking down complex UI features
    into at least 3 distinct sub-tasks to prevent context window timeouts.
    """
    assert "at least 3 distinct sub-tasks" in PO_TASK_GENERATION_PROMPT, (
        "Prompt must enforce >= 3 sub-tasks for UI features."
    )
    assert "110s" in PO_TASK_GENERATION_PROMPT, (
        "Prompt must mention the 110s timeout context."
    )
    assert "bounded" in PO_TASK_GENERATION_PROMPT.lower(), (
        "Prompt must enforce bounded tasks."
    )


def test_po_task_generation_prompt_includes_standards():
    """
    Verify that the prompt includes references to strict factory standards.
    """
    assert "STD-PO-1" in PO_TASK_GENERATION_PROMPT, (
        "Prompt must enforce STD-PO-1 (measurable quality attributes)."
    )
    assert "STD-AUD-1" in PO_TASK_GENERATION_PROMPT, (
        "Prompt must enforce STD-AUD-1 (claims vs observations)."
    )
    assert "STD-SEC-1" in PO_TASK_GENERATION_PROMPT, (
        "Prompt must enforce STD-SEC-1 (no secrets)."
    )


def test_po_task_generation_prompt_breakdown_strategy():
    """
    Verify that the prompt provides a strategy for breaking down frontend tasks.
    """
    assert "State & Data Layer" in PO_TASK_GENERATION_PROMPT, (
        "Prompt should suggest a data layer task."
    )
    assert "Presentational Components" in PO_TASK_GENERATION_PROMPT, (
        "Prompt should suggest a presentational component task."
    )
    assert "Integration" in PO_TASK_GENERATION_PROMPT, (
        "Prompt should suggest an integration task."
    )
