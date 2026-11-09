"""
Tests for the Product Owner Agent prompt and task generation constraints.
"""
import pytest
from backend.agents.po_agent import get_po_prompt

def test_ac_complex_ui_feature_is_broken_into_at_least_3_distinct_sub_tasks():
    """
    Acceptance Criteria: A complex UI feature is broken into at least 3 distinct sub-tasks during planning.
    """
    prompt = get_po_prompt()
    prompt_lower = prompt.lower()
    
    assert "at least 3 distinct" in prompt_lower, "Prompt must require at least 3 distinct sub-tasks"
    assert "complex ui" in prompt_lower, "Prompt must explicitly mention complex UI features"
    assert "sub-tasks" in prompt_lower, "Prompt must explicitly mention sub-tasks"

def test_ac_prevents_giant_context_windows_that_cause_110s_timeouts():
    """
    Acceptance Criteria: Enforce smaller, bounded frontend tasks, preventing giant context windows that cause 110s timeouts.
    """
    prompt = get_po_prompt()
    prompt_lower = prompt.lower()
    
    assert "giant" in prompt_lower, "Prompt must warn against giant context windows"
    assert "context window" in prompt_lower, "Prompt must explicitly mention context windows"
    assert "110s timeout" in prompt_lower or "110s timeouts" in prompt_lower, "Prompt must explicitly mention 110s timeouts"

def test_quality_standards_included_in_prompt():
    """
    Verify that the PO prompt enforces the factory quality standards.
    """
    prompt = get_po_prompt()
    
    assert "STD-SEC-1" in prompt, "Prompt must enforce STD-SEC-1"
    assert "STD-AUD-1" in prompt, "Prompt must enforce STD-AUD-1"
    assert "STD-PO-1" in prompt, "Prompt must enforce STD-PO-1"
