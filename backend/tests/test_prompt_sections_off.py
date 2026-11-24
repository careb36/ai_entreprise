"""Named prompt sections turned off for a with/without comparison (#443, bench).

``PROMPT_SECTIONS_OFF`` lists section names; the worker leaves them out of
the prompt and the run's measure says which were off, so a scorecard can
tell the runs apart. Unset: nothing is off and the prompt is unchanged.
"""

import lifecycle
from services.loop.worker import execute as worker_execute
from services.loop.worker import prompt_budget
from services.loop.worker.prompt_budget import (
    REQUIRED_NAMES,
    SECTION_NAMES,
    Section,
    fit,
    sections_off,
    startup_warnings,
)


def test_unset_or_empty_turns_nothing_off():
    assert sections_off("") == frozenset()
    assert sections_off(" , ,") == frozenset()
    assert startup_warnings("") == []


def test_known_names_are_read_trimmed_and_case_insensitive():
    assert sections_off(" Memory, lessons ,memory") == frozenset({"memory", "lessons"})


def test_an_unknown_or_required_name_is_ignored_and_named_in_a_warning():
    raw = "memory,memroy,task"
    assert sections_off(raw) == frozenset({"memory"})
    warnings = startup_warnings(raw)
    assert len(warnings) == 3
    assert "memroy" in warnings[0] and "PROMPT_SECTIONS_OFF" in warnings[0]
    assert "sprint_board" in warnings[0]  # The known names are listed.
    assert "task" in warnings[1] and "required" in warnings[1]
    # What is off is said too, so an ablation left on in production is seen.
    assert warnings[2].endswith(": memory.")


def test_the_setting_is_read_from_the_configuration(monkeypatch):
    monkeypatch.setattr(prompt_budget.settings, "PROMPT_SECTIONS_OFF", "wiki")
    assert sections_off() == frozenset({"wiki"})
    monkeypatch.setattr(prompt_budget.settings, "PROMPT_SECTIONS_OFF", "")
    assert sections_off() == frozenset()


def test_fit_leaves_out_the_sections_turned_off_and_records_them():
    sections = [
        Section("task", "T" * 8),
        Section("lessons", "L" * 8, 4),
        Section("memory", "M" * 8, 5),
    ]
    text, measure = fit(sections, off=frozenset({"memory", "completed_tasks"}))
    assert text == "T" * 8 + "L" * 8
    # Not a budget cut: the prompt does not name it, the measure does.
    assert "Left out" not in text and measure["cut"] == []
    assert [s["name"] for s in measure["sections"]] == ["task", "lessons"]
    assert measure["off"] == ["completed_tasks", "memory"]
    assert measure["off_dropped"] == ["memory"]
    assert measure["chars"] == 16


def test_required_sections_are_kept_even_if_named():
    text, measure = fit([Section("task", "T")], off=frozenset({"task"}))
    assert text == "T" and measure["off_dropped"] == []


def test_without_anything_off_the_measure_says_so_and_the_text_is_unchanged():
    sections = [Section("task", "T"), Section("memory", "M", 5)]
    text, measure = fit(sections)
    assert text == "TM"
    assert measure["off"] == [] and measure["off_dropped"] == []


def test_the_budget_counts_only_the_sections_still_on():
    sections = [
        Section("task", "T" * 40),
        Section("lessons", "L" * 400, 4),
        Section("notes", "N" * 400, 6),
    ]
    _, measure = fit(sections, budget=120, off=frozenset({"notes"}))
    assert measure["cut"] == [] and measure["off_dropped"] == ["notes"]


def _developer(monkeypatch, off=frozenset()):
    monkeypatch.setattr(
        worker_execute.developer_tools, "repository_section", lambda *a, **k: "\n\nREPO"
    )
    return worker_execute._developer_prompt(
        {"id": 1, "title": "Add login", "description": "A form."},
        {"role": "Developer"},
        "Sprint 1",
        "old decision",
        "done before",
        None,
        1,
        "a lesson",
        None,
        None,
        None,
        off=off,
    )


def test_every_developer_section_name_is_known(monkeypatch):
    _, _, measure = _developer(monkeypatch)
    names = {s["name"] for s in measure["sections"]}
    assert names <= set(SECTION_NAMES)
    assert REQUIRED_NAMES <= set(SECTION_NAMES)


def test_the_developer_prompt_drops_the_sections_turned_off(monkeypatch):
    full, _, _ = _developer(monkeypatch)
    text, _, measure = _developer(monkeypatch, frozenset({"repository", "memory"}))
    assert "REPO" in full and "REPO" not in text
    assert "old decision" in full and "old decision" not in text
    assert "## Task: Add login" in text
    assert measure["off_dropped"] == ["repository", "memory"]


def test_a_removed_setting_in_the_environment_is_named_once_and_does_not_stop_startup(
    monkeypatch,
):
    """The console assistants' settings were removed (issue #750); a .env that
    still sets one loads, and startup says it no longer does anything."""
    import config
    from removed_settings import removed_settings_in_use

    monkeypatch.setenv("CONSOLE_ASSISTANTS_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_CLI_COMMAND_TEMPLATE", "claude -p {prompt}")
    current = config.Settings()
    warnings = removed_settings_in_use(current)
    assert len(warnings) == 2
    assert all("no longer does anything" in w and "#750" in w for w in warnings)
    assert {w.split(" ")[0] for w in warnings} == {
        "CONSOLE_ASSISTANTS_ENABLED",
        "CLAUDE_CLI_COMMAND_TEMPLATE",
    }
    monkeypatch.delenv("CONSOLE_ASSISTANTS_ENABLED")
    monkeypatch.delenv("CLAUDE_CLI_COMMAND_TEMPLATE")
    assert removed_settings_in_use(config.Settings()) == []
    # The console producers' quota cooldown is still read.
    assert isinstance(current.CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES, int)


def test_lifecycle_logs_the_warning_at_startup(monkeypatch):
    logged = []
    monkeypatch.setattr(prompt_budget.settings, "PROMPT_SECTIONS_OFF", "nope")
    monkeypatch.setattr(lifecycle.logger, "warning", logged.append)
    lifecycle.log_prompt_sections_off()
    assert len(logged) == 1 and "nope" in logged[0]
