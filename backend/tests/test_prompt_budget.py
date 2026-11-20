"""The weight of each prompt section and the budget that cuts the least needed first (#443)."""

from services.loop.worker import execute as worker_execute
from services.loop.worker.prompt_budget import Section, budget_for, estimate_tokens, fit


def test_without_a_budget_every_section_is_kept_and_measured():
    text, measure = fit(
        [Section("a", "x" * 8), Section("empty", ""), Section("b", "y" * 5, 3)]
    )
    assert text == "x" * 8 + "y" * 5
    assert measure["budget"] is None and measure["cut"] == []
    assert [s["name"] for s in measure["sections"]] == ["a", "b"]
    assert measure["sections"][1] == {
        "name": "b",
        "chars": 5,
        "tokens": 2,
        "priority": 3,
        "cut": False,
    }
    assert measure["tokens"] == estimate_tokens(text) == 4


def test_over_budget_the_highest_priority_number_goes_first_and_the_cut_is_named():
    sections = [
        Section("task", "T" * 40),
        Section("lessons", "L" * 400, 4),
        Section("repository", "R" * 400, 3),
        Section("notes", "N" * 400, 6),
    ]
    text, measure = fit(sections, budget=300)
    assert measure["cut"] == ["notes"]
    assert "N" not in text and "L" * 400 in text and "R" * 400 in text
    assert "## Left out of this prompt" in text and "notes" in text
    assert measure["tokens"] <= 300

    text, measure = fit(sections, budget=200)
    assert measure["cut"] == ["lessons", "notes"]  # Named in prompt order.
    assert "R" * 400 in text


def test_required_sections_are_never_cut_even_when_still_over():
    text, measure = fit([Section("task", "T" * 4000), Section("memory", "M", 5)], 10)
    assert "T" * 4000 in text
    assert measure["cut"] == ["memory"]
    assert measure["tokens"] > 10


def test_among_equal_priorities_the_later_section_goes_first():
    _, measure = fit(
        [
            Section("t", "T"),
            Section("first", "a" * 400, 5),
            Section("second", "b" * 400, 5),
        ],
        budget=160,
    )
    assert measure["cut"] == ["second"]


def test_the_budget_is_read_per_role_then_the_default():
    env = {"PROMPT_BUDGET": "9000", "PROMPT_BUDGET_BACKENDDEVELOPER": "6000"}
    assert budget_for("BackendDeveloper", env) == 6000
    assert budget_for("QA", env) == 9000
    assert budget_for("Tech Writer", {"PROMPT_BUDGET_TECH_WRITER": "100"}) == 100
    assert budget_for("QA", {}) is None
    assert budget_for("QA", {"PROMPT_BUDGET": "0"}) is None
    assert budget_for("QA", {"PROMPT_BUDGET_QA": "lots", "PROMPT_BUDGET": "5"}) is None


def _prompt(monkeypatch, budget=None, **context):
    monkeypatch.setattr(
        worker_execute.developer_tools, "repository_section", lambda *a, **k: "\n\nREPO"
    )
    args = {
        "memory_context": None,
        "completed_tasks_context": None,
        "architect_advice": None,
        "lessons_context": None,
        "procedures_context": None,
        "requirements_context": None,
    }
    args.update(context)
    return worker_execute._developer_prompt(
        {"id": 1, "title": "Add login", "description": "A form."},
        {"role": "Developer"},
        "Sprint 1",
        args["memory_context"],
        args["completed_tasks_context"],
        args["architect_advice"],
        1,
        args["lessons_context"],
        args["procedures_context"],
        args["requirements_context"],
        budget,
    )


def test_the_developer_prompt_is_measured_section_by_section(monkeypatch):
    text, feedback, measure = _prompt(
        monkeypatch, memory_context="old decision", completed_tasks_context="tip"
    )
    assert feedback == []
    names = [s["name"] for s in measure["sections"]]
    assert names[:2] == ["output_format", "task"]
    assert {"repository", "memory", "completed_tasks"} <= set(names)
    assert "console_assistant_notes" not in names  # Removed, issue #750.
    assert measure["chars"] == len(text) and measure["cut"] == []
    assert sum(s["chars"] for s in measure["sections"]) == len(text)


def test_over_budget_the_completed_tasks_and_memory_go_before_the_repository(
    monkeypatch,
):
    context = {"memory_context": "m" * 2000, "completed_tasks_context": "c" * 1900}
    full, _, measure = _prompt(monkeypatch, **context)
    budget = measure["tokens"] - 300
    text, _, measure = _prompt(monkeypatch, budget, **context)
    # Memory and completed tasks share priority 5; the later one goes first.
    assert measure["cut"] == ["completed_tasks"]
    assert "REPO" in text and "m" * 2000 in text and "## Task: Add login" in text
    assert len(text) < len(full)


def test_the_measure_reaches_the_run_s_evidence(monkeypatch):
    from services.loop.worker import record

    captured = {}

    class Run:
        id = 7

    class Verified:
        verdict = "verified"

    class FakeSession:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

    def verify(session, run, evidence):
        captured["evidence"] = evidence
        return Verified()

    monkeypatch.setattr(record, "Session", FakeSession)
    monkeypatch.setattr(
        record.agent_run_service, "ingest_agent_run", lambda s, p: (Run(), True)
    )
    monkeypatch.setattr(record, "verify_agent_run", verify)
    measure = {"budget": 100, "tokens": 90, "sections": [], "cut": ["memory"]}
    result = record._record_task_run(
        {"id": 3, "title": "t"},
        {"role": "Developer", "prompt_sections": measure},
        {"id": 2, "company_id": 1},
        "done",
        [],
        None,
    )
    assert result == (7, "verified")
    assert captured["evidence"] == {"prompt_sections": measure}
