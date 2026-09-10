"""
Tests for human review feedback injection in task_worker prompts.
"""

import services.task_worker as task_worker


def test_execute_task_includes_human_review_feedback_before_memory(monkeypatch):
    captured = {}

    class FakeLLMProxyClient:
        def __init__(self, base_url):
            self.base_url = base_url
            self.timeout = None

        def chat(self, *, messages, model, role):
            captured["messages"] = messages
            captured["model"] = model
            captured["role"] = role
            return {"choices": [{"message": {"content": "deliverable"}}]}

    monkeypatch.setattr(task_worker, "LLMProxyClient", FakeLLMProxyClient)

    feedback = "The error handling is missing.\n" + ("x" * 2100) + "TAIL"
    result = task_worker._execute_task(
        item={"id": 1, "title": "Improve robustness", "description": "Handle exceptions", "priority": "high"},
        agent={"role": "Developer", "model": "deepseek-coder", "name": "Dev"},
        sprint_name="Sprint Alpha",
        memory_context="Past decision",
        human_review_feedback=feedback,
    )

    assert result == "deliverable"
    user_prompt = captured["messages"][1]["content"]
    assert "## Human Review Feedback" in user_prompt
    assert "The error handling is missing." in user_prompt
    assert "TAIL" not in user_prompt
    assert user_prompt.index("## Human Review Feedback") < user_prompt.index("## Memory: Relevant Past Decisions")


def test_execute_task_omits_human_review_feedback_section_when_absent(monkeypatch):
    captured = {}

    class FakeLLMProxyClient:
        def __init__(self, base_url):
            self.base_url = base_url
            self.timeout = None

        def chat(self, *, messages, model, role):
            captured["messages"] = messages
            return {"choices": [{"message": {"content": "deliverable"}}]}

    monkeypatch.setattr(task_worker, "LLMProxyClient", FakeLLMProxyClient)

    task_worker._execute_task(
        item={"id": 2, "title": "Improve logging", "description": "Add context", "priority": "medium"},
        agent={"role": "Developer", "model": "deepseek-coder", "name": "Dev"},
        sprint_name="Sprint Beta",
        memory_context="Past decision",
    )

    user_prompt = captured["messages"][1]["content"]
    assert "## Human Review Feedback" not in user_prompt
