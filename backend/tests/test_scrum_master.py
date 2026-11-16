"""The ScrumMaster's question and answer.

The prompt carries the task, the block and the rework asked; only the fixed
format is a decision (Markdown and wrapped fields, ``END`` with a period or
Windows line endings, a wrapped line starting with ``END``); a long brief is
cut; the decision is asked of the company's ScrumMaster, once and with a short
timeout, and a failed call raises.

``services/learning/scrum_master.py``: the ScrumMaster answers an escalation
in a fixed format; the decision is recorded as a message to the assignee
(another attempt, with a brief) or to nobody in particular (a person decides),
and read back from the conversation. The shared rows are in
``scrum_master_helpers``.
"""

import pytest
import scrum_master_helpers
from models import Agent

# test_replicas.py imports TRIAGE and RETRY_ANSWER from here too.
from scrum_master_helpers import RETRY_ANSWER, TRIAGE, _blocked
from services.learning import scrum_master

# The shared fixtures.
company = scrum_master_helpers.company


def test_the_prompt_carries_the_task_the_block_and_the_rework_asked(
    sqlite_session, company
):
    _company, sprint = company
    item = _blocked(sqlite_session, sprint)

    prompt = scrum_master.triage_prompt(item, TRIAGE, "\n\n## Company Procedures\nx")

    assert prompt.startswith("You are the ScrumMaster. A task was blocked")
    assert "## Task: Create testing strategy document" in prompt
    assert "Assigned to: QA" in prompt
    assert f"## What blocked it\n{TRIAGE}" in prompt
    assert "- QA Quinn: NEEDS WORK: cover integration tests too" in prompt
    assert "- Quality gate: NEEDS WORK: Too short (12 words)." in prompt
    assert "## Company Procedures" in prompt
    assert "DECISION: <retry or person>" in prompt


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            RETRY_ANSWER,
            {
                "decision": "retry",
                "brief": "Write the full strategy document (at least 180 words) with sections for unit, integration and end-to-end tests, as a path= block.",
                "why": "the attempts were cut short, not wrong.",
            },
        ),
        (
            "Sure.\nDECISION: person\nBRIEF: n/a\nWHY: it needs the team's test budget.\nEND\nThanks",
            {
                "decision": "person",
                "brief": "",
                "why": "it needs the team's test budget.",
            },
        ),
        ("decision: Retry\nEND", {"decision": "retry", "brief": "", "why": ""}),
        ("I think it should be retried with a longer document.", None),
        ("DECISION: maybe\nEND", None),
        ("DECISION: retry\nBRIEF: no END marker", None),
    ],
    ids=["retry", "person", "bare", "prose", "unknown", "unterminated"],
)
def test_only_the_fixed_format_is_a_decision(answer, expected):
    assert scrum_master.parse_answer(answer) == expected


def test_a_long_brief_is_cut():
    answer = f"DECISION: retry\nBRIEF: {'x' * 700}\nWHY: y\nEND"

    assert len(scrum_master.parse_answer(answer)["brief"]) == scrum_master.BRIEF_LIMIT


def test_the_decision_is_asked_of_the_companys_scrum_master(
    sqlite_session, company, monkeypatch
):
    company, _sprint = company
    sqlite_session.add(
        Agent(
            company_id=company.id,
            name="Sam",
            role="ScrumMaster",
            model="deepseek-v4-pro",
            system_prompt="Be brief.",
        )
    )
    sqlite_session.commit()
    calls = []

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            calls.append((messages, model, role, self.timeout))
            return {"choices": [{"message": {"content": "  DECISION: person\nEND "}}]}

    monkeypatch.setattr(scrum_master, "LLMProxyClient", _Client)

    answer = scrum_master.ask(sqlite_session, company.id, "the question")

    assert answer == "DECISION: person\nEND"
    (messages, model, role, timeout) = calls[0]
    assert (model, role, timeout) == (
        "deepseek-v4-pro",
        "ScrumMaster",
        scrum_master.TIMEOUT_SECONDS,
    )
    assert messages == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "the question"},
    ]


@pytest.mark.parametrize(
    "result",
    [{"error": "proxy down", "status_code": 503}, {"choices": [{"message": {}}]}],
    ids=["error", "empty"],
)
def test_a_failed_call_raises(sqlite_session, company, monkeypatch, result):
    company, _sprint = company

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            return result

    monkeypatch.setattr(scrum_master, "LLMProxyClient", _Client)

    with pytest.raises(scrum_master.ScrumMasterError):
        scrum_master.ask(sqlite_session, company.id, "q")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            "**DECISION:** `retry`\n**BRIEF:** Write the full document\n(at least 180 words) as a path= block.\n**WHY:** cut short.\n**END**",
            {
                "decision": "retry",
                "brief": "Write the full document (at least 180 words) as a path= block.",
                "why": "cut short.",
            },
        ),
        (
            'DECISION: "person"\nWHY: needs a budget decision\nthe team cannot make.\nEND',
            {
                "decision": "person",
                "brief": "",
                "why": "needs a budget decision the team cannot make.",
            },
        ),
    ],
    ids=["markdown-and-wrapped-brief", "quoted-and-wrapped-why"],
)
def test_markdown_and_wrapped_fields_are_still_a_decision(answer, expected):
    assert scrum_master.parse_answer(answer) == expected


def test_a_wrapped_line_starting_with_end_does_not_end_the_answer():
    answer = (
        "DECISION: retry\n"
        "BRIEF: Deliver all files and\n"
        "end-to-end tests for the parser.\n"
        "WHY: the tests were missing\n"
        "END"
    )

    assert scrum_master.parse_answer(answer) == {
        "decision": "retry",
        "brief": "Deliver all files and end-to-end tests for the parser.",
        "why": "the tests were missing",
    }


@pytest.mark.parametrize(
    "answer",
    [
        "DECISION: retry\nBRIEF: Finish the document.\nWHY: cut short\nEND.",
        "DECISION: retry\r\nBRIEF: Finish the document.\r\nWHY: cut short\r\nEND\r\n",
    ],
    ids=["end-with-a-period", "windows-line-endings"],
)
def test_end_with_a_period_or_windows_line_endings_still_closes_the_answer(answer):
    assert scrum_master.parse_answer(answer) == {
        "decision": "retry",
        "brief": "Finish the document.",
        "why": "cut short",
    }


def test_the_triage_asks_once_with_a_short_timeout(monkeypatch):
    seen = []

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None
            self.max_retries = 2

        def chat(self, messages, model, role, bu_id=None):
            seen.append((self.timeout, self.max_retries))
            return {"choices": [{"message": {"content": "DECISION: person\nEND"}}]}

    monkeypatch.setattr(scrum_master, "LLMProxyClient", _Client)

    scrum_master.ask_triage("m", None, "q")
    scrum_master.ask_model("m", None, "q")

    assert seen == [
        (scrum_master.TRIAGE_TIMEOUT_SECONDS, 0),
        (scrum_master.TIMEOUT_SECONDS, 2),
    ]
