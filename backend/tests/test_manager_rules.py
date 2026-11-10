"""Every Manager decision the company's policy allows (issue #394): each new
rule decides from what the platform observed and holds only on its facts,
never unless named, flagged, over budget or after an override; the agent's
action goes through a person's code path, is recorded with its rule and
facts, and the client's override takes it back (or says why it cannot)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from history_check import check_task
from models import (
    AgentRun,
    Architecture,
    AuditLog,
    BacklogItem,
    Specification,
    Sprint,
)
from models.agent_run import TASK_WORKER_PRODUCER, Verification
from models.integration import SprintIntegration
from models.review import RunPublication
from services import (
    carry_over,
    manager_actions,
    manager_observe,
    manager_policy,
    manager_record,
    manager_rules,
)
from services.carry_over import CARRY_MARK_PREFIX, carry_mark
from services.learning import scrum_master
from services.manager_actions import ACCEPTED_PREFIX, MANAGER_AGENT_NAME
from services.planning import arch_check, spec_check
from services.tasks.board import RISK_HOLD_PREFIX, SEND_BACK_MARKER, is_send_back
from sqlmodel import select
from test_architectures import GOOD_ARCH
from test_manager_agent import _company, _cycle, _hold, _policy, _rows, answer
from test_specifications import GOOD

SHA = "a" * 40
SHA2 = "b" * 40
P = manager_policy.Policy


def _ok(rule: str, facts: dict, policy=None) -> bool:
    return manager_rules.rule_holds(rule, facts, policy or P()) is None


@pytest.fixture()
def daemon():
    """A fake workspace daemon: the requests it got, and its answers."""
    calls: list[tuple[str, dict]] = []
    answers: dict[str, dict] = {}

    def request(subject, payload, timeout):
        calls.append((subject, payload))
        reply = answers.get(subject)
        if isinstance(reply, Exception):
            raise reply
        return reply

    manager_observe.set_requester(request)
    yield calls, answers
    manager_observe.set_requester(None)


# ── The policy: the opt-in rules and the confirmation set ────────────────────


def test_the_strict_rules_are_opt_in_and_the_confirmation_set_stays():
    assert manager_policy.ALWAYS_ESCALATE == {
        "cancel_sprint",
        "delete_sprint",
        "change_autonomy",
        "spend_above_budget",
    }
    for name in ("approve_specification", "approve_architecture", "accept_high_risk"):
        assert manager_policy.validate({"rules": [name]}).rules == [name]
    policy = manager_policy.validate(
        {"rules": ["publish"], "max_files_high_risk": 2, "publish_enabled": True}
    )
    assert (policy.max_files_high_risk, policy.publish_enabled) == (2, True)
    assert manager_policy.validate({}).max_files_high_risk == 3
    assert manager_policy.validate({}).publish_enabled is False
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"publish_enabled": "yes"})
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"max_files_high_risk": -1})
    for name in manager_policy.RULES:
        assert name in manager_rules.CHECKS and name in manager_policy.RULE_DECISIONS


GOOD_FACTS = {
    "merge_local": {
        "has_branch": True,
        "qa_approved": True,
        "verdict": "verified",
        "risk_tier": "low",
        "files": 2,
        "merged": False,
    },
    "undo_delivery": {
        "verdict": "refuted",
        "on_branch": False,
        "has_snapshot": True,
        "person_touched": False,
    },
    "send_back_blocked": {
        "blocked": True,
        "triage_decision": "retry",
        "triage_brief": True,
        "sprint_status": "active",
    },
    "carry_over": {"blocked": True, "triage_decision": "retry"},
    "answer_question": {"open_question": True, "answer_known": True},
    "publish": {
        "has_branch": True,
        "qa_approved": True,
        "verdict": "verified",
        "risk_tier": "medium",
        "files": 1,
        "published": False,
        "publishing_on": True,
        "has_remote": True,
    },
    "resume_after_credit": {
        "sprint_status": "paused",
        "paused_for_credit": True,
        "balance_ok": True,
    },
    "approve_specification": {
        "blocking_findings": 0,
        "open_questions": 0,
        "all_requirements_have_criteria": True,
    },
    "approve_architecture": {
        "blocking_findings": 0,
        "open_questions": 0,
        "specification_status": "approved",
        "covers_all_requirements": True,
    },
    "accept_high_risk": {
        "verdict": "verified",
        "criteria": 2,
        "all_criterion_tests_pass": True,
        "standards_secret": False,
        "files": 3,
    },
}
POLICY_ON = P(publish_enabled=True)


@pytest.mark.parametrize(
    ("rule", "change"),
    [
        ("merge_local", {"qa_approved": False}),
        ("merge_local", {"verdict": "refuted"}),
        ("merge_local", {"risk_tier": "high"}),
        ("merge_local", {"files": None}),
        ("merge_local", {"files": 11}),
        ("merge_local", {"has_branch": False}),
        ("merge_local", {"merged": True}),
        ("undo_delivery", {"verdict": "verified"}),
        ("undo_delivery", {"on_branch": True}),
        ("undo_delivery", {"has_snapshot": False}),
        ("undo_delivery", {"person_touched": True}),
        ("undo_delivery", {"person_touched": None}),
        ("send_back_blocked", {"triage_decision": "person"}),
        ("send_back_blocked", {"triage_brief": False}),
        ("send_back_blocked", {"sprint_status": "completed"}),
        ("send_back_blocked", {"frozen": True}),
        ("send_back_blocked", {"blocked": False}),
        ("carry_over", {"carried": True}),
        ("carry_over", {"triage_decision": "person"}),
        ("carry_over", {"second_attempt": True}),
        ("carry_over", {"marked_for_carry_over": True}),
        ("answer_question", {"answer_known": False}),
        ("answer_question", {"open_question": False}),
        ("publish", {"publishing_on": False}),
        ("publish", {"has_remote": None}),
        ("publish", {"has_remote": False}),
        ("publish", {"published": True}),
        ("publish", {"qa_approved": False}),
        ("resume_after_credit", {"balance_ok": None}),
        ("resume_after_credit", {"balance_ok": False}),
        ("resume_after_credit", {"paused_for_credit": False}),
        ("approve_specification", {"blocking_findings": 1}),
        ("approve_specification", {"open_questions": 1}),
        ("approve_specification", {"all_requirements_have_criteria": False}),
        ("approve_architecture", {"blocking_findings": 2}),
        ("approve_architecture", {"open_questions": 1}),
        ("approve_architecture", {"specification_status": "superseded"}),
        ("approve_architecture", {"covers_all_requirements": False}),
        ("accept_high_risk", {"verdict": "unverifiable"}),
        ("accept_high_risk", {"all_criterion_tests_pass": False}),
        ("accept_high_risk", {"criteria": 0}),
        ("accept_high_risk", {"standards_secret": True}),
        ("accept_high_risk", {"standards_secret": None}),
        ("accept_high_risk", {"files": 4}),
    ],
)
def test_each_rule_holds_only_on_its_facts(rule, change):
    assert _ok(rule, GOOD_FACTS[rule], POLICY_ON)
    assert not _ok(rule, {**GOOD_FACTS[rule], **change}, POLICY_ON)


def test_merge_of_an_integration_needs_its_suite_passed():
    facts = {"target": "integration", "integration_status": "tests_passed"}
    assert _ok("merge_local", facts)
    assert not _ok("merge_local", {**facts, "integration_status": "untested"})
    assert not _ok("merge_local", {**facts, "merged": True})


def test_publishing_needs_the_client_s_switch_and_the_tighter_limit_is_the_policy_s():
    assert not _ok("publish", GOOD_FACTS["publish"], P(publish_enabled=False))
    facts = GOOD_FACTS["accept_high_risk"]
    assert not _ok("accept_high_risk", facts, P(max_files_high_risk=2))
    assert _ok("accept_high_risk", {**facts, "files": 10}, P(max_files_high_risk=10))


def test_a_rule_applies_only_when_named_and_never_flagged_over_budget_or_overridden():
    facts = GOOD_FACTS["approve_specification"]
    category = "approve_specification"
    off = manager_policy.evaluate(category, "approve", facts, P())
    assert not off["allowed"] and off["rule"] is None
    on = P(rules=["approve_specification"])
    assert manager_policy.evaluate(category, "approve", facts, on)["allowed"]
    for extra in ({"over_budget": True}, {"overridden_before": True}):
        assert not manager_policy.evaluate(category, "approve", {**facts, **extra}, on)[
            "allowed"
        ]
    assert not manager_policy.evaluate(
        category, "approve", facts, on, flagged={"approve_specification"}
    )["allowed"]
    assert not manager_policy.evaluate(category, "escalate", facts, on)["allowed"]


def test_a_blocked_task_s_two_rules_answer_to_their_own_decision():
    both = P(rules=["send_back_blocked", "carry_over"])
    facts = GOOD_FACTS["send_back_blocked"]
    send = manager_policy.evaluate("blocked_task", "send_back", facts, both)
    carry = manager_policy.evaluate("blocked_task", "carry_over", facts, both)
    assert (send["rule"], send["allowed"]) == ("send_back_blocked", True)
    assert (carry["rule"], carry["allowed"]) == ("carry_over", True)
    only_carry = P(rules=["carry_over"])
    refused = manager_policy.evaluate("blocked_task", "send_back", facts, only_carry)
    assert not refused["allowed"] and "carry_over" in refused["why"]


# ── Blocked tasks: send back, carry over ─────────────────────────────────────


def _blocked(session, company, sprint_status="active") -> BacklogItem:
    sprint = Sprint(company_id=company.id, sprint_name="S", status=sprint_status)
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Parse the file",
        assignee="Developer",
        status="blocked",
        comments=[
            {"author": "Developer", "text": "```python path=a.py\nx = 1\n```"},
            {"author": "QA", "text": "NEEDS WORK: the parser drops lines."},
            {"author": "System", "text": "Blocked for manual triage: rework limit."},
        ],
    )
    session.add(item)
    session.commit()
    return item


@pytest.fixture()
def triage(monkeypatch):
    decisions: dict[int, dict] = {}
    monkeypatch.setattr(
        scrum_master,
        "triages_for",
        lambda session, items, records=None: {
            i.id: decisions[i.id] for i in items if i.id in decisions
        },
    )
    return decisions


def test_a_blocked_task_the_triage_retries_is_sent_back_and_the_override_holds_it_again(
    sqlite_session, triage
):
    company = _company(sqlite_session)
    item = _blocked(sqlite_session, company)
    triage[item.id] = {"decision": "retry", "brief": "Keep empty lines."}
    _policy(sqlite_session, company, "send_back_blocked")
    _cycle(sqlite_session, company, answer("send_back", "blocked_task"))
    sqlite_session.refresh(item)
    assert item.status == "todo"
    note = item.comments[-1]
    assert note["author"] == MANAGER_AGENT_NAME and is_send_back(note)
    assert "Keep empty lines." in note["text"]
    (row,) = _rows(sqlite_session, company)
    assert (row.rule, row.outcome) == ("send_back_blocked", "sent_back")
    audit = sqlite_session.get(AuditLog, row.audit_log_id)
    assert audit.action == "manager_agent_blocked_task"
    assert audit.result["facts"]["brief"] == "Keep empty lines."

    result = manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(item)
    assert result["outcome"] == "waits_for_a_person" and item.status == "blocked"
    assert carry_over.waits_for_a_person(item)
    record = manager_record.track_record(sqlite_session, company.id)
    assert record["categories"]["blocked_task"]["overridden"] == 1


def test_a_send_back_is_not_taken_back_once_the_agents_took_the_task_up(
    sqlite_session, triage
):
    company = _company(sqlite_session)
    item = _blocked(sqlite_session, company)
    triage[item.id] = {"decision": "retry", "brief": "Keep empty lines."}
    _policy(sqlite_session, company, "send_back_blocked")
    _cycle(sqlite_session, company, answer("send_back", "blocked_task"))
    sqlite_session.refresh(item)
    item.status = "in_progress"
    sqlite_session.add(item)
    sqlite_session.commit()
    (row,) = _rows(sqlite_session, company)
    with pytest.raises(manager_actions.OverrideRefused) as refused:
        manager_actions.override(sqlite_session, row, "client@example.com")
    assert refused.value.status == 409


def test_without_a_brief_nothing_is_sent_back(sqlite_session, triage):
    company = _company(sqlite_session)
    item = _blocked(sqlite_session, company)
    triage[item.id] = {"decision": "retry", "brief": ""}
    _policy(sqlite_session, company, "send_back_blocked")
    _cycle(sqlite_session, company, answer("send_back", "blocked_task"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert _rows(sqlite_session, company)[0].action_state == "not_allowed"


def test_a_blocked_task_is_marked_for_the_next_sprint_and_the_mark_taken_back(
    sqlite_session, triage, test_client
):
    company = _company(sqlite_session, autonomy=3)
    item = _blocked(sqlite_session, company)
    _policy(sqlite_session, company, "carry_over")
    _cycle(sqlite_session, company, answer("carry_over", "blocked_task"))
    sqlite_session.refresh(item)
    assert item.status == "blocked" and carry_mark(item) is True
    assert item.comments[-1]["text"].startswith(CARRY_MARK_PREFIX)
    # Still waiting for a person (the mark is no deliverable), and the
    # inbox tells the "Plan a sprint" form to tick it.
    assert carry_over.waits_for_a_person(item)
    inbox = test_client.get(f"/v1/inbox?company_id={company.id}").json()
    assert inbox["blocked_tasks"][0]["marked_for_carry_over"] is True

    (row,) = _rows(sqlite_session, company)
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(item)
    assert carry_mark(item) is False
    # Taken back: not carried on the company's own either.
    next_sprint = Sprint(company_id=company.id, sprint_name="S2", status="active")
    sqlite_session.add(next_sprint)
    sqlite_session.commit()
    assert (
        carry_over.carry_over_on_the_companys_own(
            sqlite_session, company.id, item.sprint_id, next_sprint, 5
        )
        == []
    )


def test_a_task_the_triage_left_for_a_person_is_not_marked(sqlite_session, triage):
    company = _company(sqlite_session)
    item = _blocked(sqlite_session, company)
    triage[item.id] = {"decision": "person", "brief": ""}
    _policy(sqlite_session, company, "carry_over")
    _cycle(sqlite_session, company, answer("carry_over", "blocked_task"))
    sqlite_session.refresh(item)
    assert carry_mark(item) is None


# ── A high-risk hold under the strict rule ───────────────────────────────────


def _strict_hold(sqlite_session, company, results, secret=False, files=2):
    item = _hold(
        sqlite_session,
        company,
        human_led=False,
        tier="high",
        files=files,
        covers=["REQ-1.1", "REQ-1.2"],
    )
    run = sqlite_session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).one()
    verification.checks = [
        *verification.checks,
        {"name": "criteria_tests", "outcome": "pass", "results": results},
        {
            "name": "standards",
            "outcome": "refuted" if secret else "pass",
            "findings": [],
        },
    ]
    sqlite_session.add(verification)
    sqlite_session.commit()
    return item


def test_a_high_risk_hold_is_accepted_only_under_the_strict_rule(sqlite_session):
    company = _company(sqlite_session, autonomy=2)
    passing = {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    item = _strict_hold(sqlite_session, company, passing)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"  # accept_delivery never covers a high-risk hold

    other = _company(sqlite_session, autonomy=2)
    item = _strict_hold(sqlite_session, other, passing)
    _policy(sqlite_session, other, "accept_high_risk")
    _cycle(sqlite_session, other, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "done"
    assert item.comments[-1]["text"].startswith(ACCEPTED_PREFIX)
    assert "(rule accept_high_risk," in item.comments[-1]["text"]
    assert "high_risk_done_without_person" not in check_task(
        {"id": item.id, "status": "done", "comments": item.comments},
        [{"version": 1, "status": "verified"}],
        [],
    )
    (row,) = _rows(sqlite_session, other)
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(item)
    assert item.status == "blocked"


@pytest.mark.parametrize(
    ("results", "secret", "files"),
    [
        ({"REQ-1.1": "pass", "REQ-1.2": "fail"}, False, 2),
        ({"REQ-1.1": "pass"}, False, 2),
        ({"REQ-1.1": "pass", "REQ-1.2": "pass"}, True, 2),
        ({"REQ-1.1": "pass", "REQ-1.2": "pass"}, False, 4),
    ],
)
def test_a_high_risk_hold_stays_without_every_strict_fact(
    sqlite_session, results, secret, files
):
    company = _company(sqlite_session, autonomy=2)
    item = _strict_hold(sqlite_session, company, results, secret=secret, files=files)
    _policy(sqlite_session, company, "accept_high_risk")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


# ── Specifications, architectures and their questions ────────────────────────


def _spec(session, company, status="draft", **changes) -> Specification:
    spec = Specification(
        company_id=company.id,
        status=status,
        **spec_check.normalize({**GOOD, **changes}),
    )
    session.add(spec)
    session.commit()
    return spec


def test_a_clean_specification_is_approved_alone_and_taken_back_to_draft(
    sqlite_session,
):
    company = _company(sqlite_session)
    spec = _spec(sqlite_session, company)
    _cycle(sqlite_session, company, answer("approve", "approve_specification"))
    sqlite_session.refresh(spec)
    assert spec.status == "draft"  # no policy: a suggestion only
    _policy(sqlite_session, company, "approve_specification")
    spec.title = "Task board, edited"
    spec.updated_at = datetime.now(UTC)
    sqlite_session.add(spec)
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("approve", "approve_specification"))
    sqlite_session.refresh(spec)
    assert (spec.status, spec.approved_by) == ("approved", MANAGER_AGENT_NAME)
    row = _rows(sqlite_session, company)[-1]
    assert row.rule == "approve_specification"
    assert row.evidence["blocking_findings"] == 0
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(spec)
    assert spec.status == "draft" and spec.approved_by is None


def test_an_approval_planned_from_is_not_taken_back(sqlite_session):
    company = _company(sqlite_session)
    spec = _spec(sqlite_session, company)
    _policy(sqlite_session, company, "approve_specification")
    _cycle(sqlite_session, company, answer("approve", "approve_specification"))
    sqlite_session.add(
        Sprint(company_id=company.id, sprint_name="Plan", specification_id=spec.id)
    )
    sqlite_session.commit()
    (row,) = _rows(sqlite_session, company)
    with pytest.raises(manager_actions.OverrideRefused) as refused:
        manager_actions.override(sqlite_session, row, "client@example.com")
    assert refused.value.status == 409 and "planned" in refused.value.detail


def test_a_specification_with_a_blocking_finding_waits_for_the_client(
    sqlite_session,
):
    company = _company(sqlite_session)
    spec = _spec(
        sqlite_session, company, quality=[{"attribute": "speed", "target": "fast"}]
    )
    _policy(sqlite_session, company, "approve_specification")
    _cycle(sqlite_session, company, answer("approve", "approve_specification"))
    sqlite_session.refresh(spec)
    assert spec.status == "draft"


def _arch(session, company, spec, **changes) -> Architecture:
    arch = Architecture(
        company_id=company.id,
        specification_id=spec.id,
        **arch_check.normalize({**GOOD_ARCH, **changes}),
    )
    session.add(arch)
    session.commit()
    return arch


def test_an_architecture_covering_every_requirement_is_approved_alone(
    sqlite_session,
):
    company = _company(sqlite_session)
    spec = _spec(sqlite_session, company, status="approved")
    arch = _arch(sqlite_session, company, spec)
    _policy(sqlite_session, company, "approve_architecture")
    _cycle(sqlite_session, company, answer("approve", "approve_architecture"))
    sqlite_session.refresh(arch)
    assert (arch.status, arch.approved_by) == ("approved", MANAGER_AGENT_NAME)
    (row,) = _rows(sqlite_session, company)
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(arch)
    assert arch.status == "draft"


def test_an_architecture_leaving_a_requirement_uncovered_waits(sqlite_session):
    company = _company(sqlite_session)
    spec = _spec(sqlite_session, company, status="approved")
    modules = [{**m, "covers": []} for m in GOOD_ARCH["modules"]]
    modules[0]["covers"] = ["REQ-1"]
    arch = _arch(sqlite_session, company, spec, modules=modules)
    _policy(sqlite_session, company, "approve_architecture")
    _cycle(sqlite_session, company, answer("approve", "approve_architecture"))
    sqlite_session.refresh(arch)
    assert arch.status == "draft"
    assert (
        _rows(sqlite_session, company)[0].evidence["covers_all_requirements"] is False
    )


def test_an_open_question_is_answered_only_from_an_approved_document(
    sqlite_session,
):
    company = _company(sqlite_session)
    _spec(
        sqlite_session,
        company,
        status="approved",
        questions=[{"question": "Which database?", "answer": "Postgres 15."}],
    )
    draft = _spec(
        sqlite_session,
        company,
        questions=[
            {"question": "which   database"},
            {"question": "Who signs in?"},
        ],
    )
    _policy(sqlite_session, company, "answer_question")
    _cycle(sqlite_session, company, answer("answer", "answer_question"))
    sqlite_session.refresh(draft)
    first, second = draft.questions
    assert (
        first["answer"] == "Postgres 15." and first["answered_by"] == MANAGER_AGENT_NAME
    )
    assert first["answer_source"].startswith("specification #")
    assert not second.get("answer")
    (row,) = _rows(sqlite_session, company)
    assert row.outcome == "answered 1"
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(draft)
    assert not draft.questions[0].get("answer")


def test_a_question_nothing_answers_is_left_to_the_client(sqlite_session):
    company = _company(sqlite_session)
    draft = _spec(sqlite_session, company, questions=[{"question": "Who signs in?"}])
    _policy(sqlite_session, company, "answer_question")
    _cycle(sqlite_session, company, answer("answer", "answer_question"))
    sqlite_session.refresh(draft)
    assert not draft.questions[0].get("answer")
    assert _rows(sqlite_session, company)[0].action_state == "not_allowed"


def test_the_client_s_own_approval_is_counted_against_the_suggestion(sqlite_session):
    company = _company(sqlite_session)
    spec = _spec(sqlite_session, company)
    _cycle(sqlite_session, company, answer("escalate", "approve_specification"))
    spec.status, spec.approved_by = "approved", "client@example.com"
    sqlite_session.add(spec)
    sqlite_session.commit()
    counts = manager_record.track_record(sqlite_session, company.id)["categories"][
        "approve_specification"
    ]
    assert (counts["decided"], counts["agreed"]) == (1, 0)


# ── A sprint paused for lack of credit ───────────────────────────────────────


@pytest.fixture()
def credit(monkeypatch):
    calls: list[str] = []
    replies: list[dict] = []

    def probe(model, role, company_id, bu_id):
        calls.append(role)
        return replies.pop(0)

    monkeypatch.setattr(manager_observe, "probe_call", probe)
    manager_observe._last_credit_probe.clear()
    yield calls, replies
    manager_observe._last_credit_probe.clear()


def _paused(session, company) -> Sprint:
    sprint = Sprint(
        company_id=company.id,
        sprint_name="S",
        status="paused",
        pause_reason="No credit at deepseek: the provider refused the call.",
    )
    session.add(sprint)
    session.commit()
    session.add(
        BacklogItem(sprint_id=sprint.id, title="T", assignee="QA", status="todo")
    )
    session.commit()
    return sprint


def test_a_sprint_paused_for_credit_resumes_once_a_call_passes(sqlite_session, credit):
    calls, replies = credit
    company = _company(sqlite_session)
    sprint = _paused(sqlite_session, company)
    _policy(sqlite_session, company, "resume_after_credit")
    replies.append({"error": "payment required", "detail": {"status": 402}})
    _cycle(sqlite_session, company, answer("resume", "resume_after_credit"))
    sqlite_session.refresh(sprint)
    (row,) = _rows(sqlite_session, company)
    assert sprint.status == "paused" and row.action_state == "pending"
    # Within the probe interval nothing is called and the row keeps waiting.
    _cycle(sqlite_session, company, answer("resume", "resume_after_credit"))
    assert len(calls) == 1 and _cycle.prompts == []
    manager_observe._last_credit_probe.clear()
    replies.append({"choices": [{"message": {"content": "OK"}}]})
    _cycle(sqlite_session, company, answer("resume", "resume_after_credit"))
    sqlite_session.refresh(sprint)
    sqlite_session.refresh(row)
    assert (sprint.status, sprint.pause_reason) == ("active", None)
    assert (row.outcome, calls) == ("resumed", ["QA", "QA"])
    manager_actions.override(sqlite_session, row, "client@example.com")
    sqlite_session.refresh(sprint)
    assert sprint.status == "paused" and sprint.pause_reason.startswith("No credit")


def test_no_credit_call_is_made_unless_the_rule_is_named(sqlite_session, credit):
    calls, _ = credit
    company = _company(sqlite_session)
    _paused(sqlite_session, company)
    _cycle(sqlite_session, company, answer("resume", "resume_after_credit"))
    assert calls == [] and _rows(sqlite_session, company)[0].action_state == (
        "not_allowed"
    )


# ── Branches: merge locally, publish; deliveries: undo ───────────────────────


def _delivered(
    session,
    company,
    *,
    status="done",
    verdict="verified",
    git=True,
    comments=None,
    snapshot=False,
):
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Greeting",
        assignee="Developer",
        status=status,
        comments=comments
        or [
            {"author": "Developer", "text": "```python path=app/hi.py\nx = 1\n```"},
            {"author": "QA", "text": "APPROVED: right."},
        ],
    )
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id},
        claims={"files": ["app/hi.py"]},
        artifact_digest=secrets.token_hex(8),
        artifact_size=10,
    )
    session.add(run)
    session.commit()
    evidence = {
        "workspace_root": "/repo",
        "workspace_writes": [{"path": "app/hi.py", "status": "written"}],
    }
    if git:
        evidence["git"] = {
            "branch": f"fabrique/task-{item.id}",
            "repository": "/repo",
            "commit": SHA,
            "base": "main",
        }
    if snapshot:
        evidence["snapshot"] = {"id": "snap1", "files": []}
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            evidence=evidence,
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": "low"}],
            observed_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
        )
    )
    session.commit()
    return item, run


MERGED = {
    "ok": True,
    "branch": "fabrique/task-1",
    "base": "main",
    "commit": SHA,
    "before": SHA2,
    "after": SHA,
}


def test_a_done_task_s_branch_is_merged_locally_and_the_override_only_recorded(
    sqlite_session, daemon
):
    calls, answers = daemon
    answers["workspace.git.merge"] = MERGED
    company = _company(sqlite_session)
    _, run = _delivered(sqlite_session, company)
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    assert _rows(sqlite_session, company) == []  # not gathered unless named
    _policy(sqlite_session, company, "merge_local")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    assert [s for s, _ in calls] == ["workspace.git.merge"]
    assert calls[0][1]["commit"] == SHA and calls[0][1]["base"] == "main"
    publication = sqlite_session.exec(
        select(RunPublication).where(RunPublication.agent_run_id == run.id)
    ).one()
    assert (publication.how, publication.published_by) == ("local", MANAGER_AGENT_NAME)
    audit = sqlite_session.get(AuditLog, publication.audit_log_id)
    assert audit.agent_id == MANAGER_AGENT_NAME
    (row,) = _rows(sqlite_session, company)
    assert (row.item_kind, row.outcome) == ("task_branch", "merged")
    result = manager_actions.override(sqlite_session, row, "client@example.com")
    assert result["outcome"] == "recorded_not_undone" and "git" in result["note"]
    record = manager_record.track_record(sqlite_session, company.id)
    assert record["categories"]["merge_local"]["overridden"] == 1


def test_no_daemon_leaves_the_merge_pending_and_a_refusal_fails_it(
    sqlite_session, daemon
):
    from services.workspace.workspace_daemon import DaemonError, DaemonUnavailable

    _, answers = daemon
    company = _company(sqlite_session)
    _delivered(sqlite_session, company)
    _policy(sqlite_session, company, "merge_local")
    answers["workspace.git.merge"] = DaemonUnavailable("down")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    (row,) = _rows(sqlite_session, company)
    assert row.action_state == "pending" and row.decided_by is None
    answers["workspace.git.merge"] = DaemonError(409, "conflict in app/hi.py")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    sqlite_session.refresh(row)
    assert row.action_state == "failed" and row.decided_by is None


def test_a_branch_qa_did_not_approve_is_not_merged(sqlite_session, daemon):
    calls, _ = daemon
    company = _company(sqlite_session)
    _delivered(
        sqlite_session,
        company,
        comments=[
            {"author": "Developer", "text": "```python path=a.py\nx\n```"},
            {"author": "user", "text": "Done by hand."},
        ],
    )
    _policy(sqlite_session, company, "merge_local")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    assert calls == []
    assert _rows(sqlite_session, company)[0].action_state == "not_allowed"


def test_an_integration_whose_suite_passed_is_merged(sqlite_session, daemon):
    _, answers = daemon
    answers["workspace.git.merge"] = MERGED
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="close",
        requested_by="system",
        status="tests_passed",
        branch=f"fabrique/sprint-{sprint.id}",
        base="main",
        repository="/repo",
        commit=SHA,
    )
    sqlite_session.add(row)
    sqlite_session.commit()
    _policy(sqlite_session, company, "merge_local")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    sqlite_session.refresh(row)
    assert row.details["local_merge"]["merged_by"] == MANAGER_AGENT_NAME


def test_a_refuted_delivery_is_undone_from_its_snapshot(sqlite_session, daemon):
    calls, answers = daemon
    answers["workspace.undo"] = {
        "ok": True,
        "snapshot": "snap1",
        "restored": ["app/hi.py"],
        "removed": [],
    }
    company = _company(sqlite_session)
    _delivered(
        sqlite_session,
        company,
        status="todo",
        verdict="refuted",
        git=False,
        snapshot=True,
    )
    _policy(sqlite_session, company, "undo_delivery")
    _cycle(sqlite_session, company, answer("undo", "undo_delivery"))
    assert calls == [
        ("workspace.undo", {"workspace_root": "/repo", "snapshot_id": "snap1"})
    ]
    (row,) = _rows(sqlite_session, company)
    assert row.outcome == "undone"
    assert manager_actions.override(sqlite_session, row, "c@example.com")[
        "outcome"
    ] == ("recorded_not_undone")


def test_a_delivery_a_person_worked_on_since_is_not_undone(sqlite_session, daemon):
    calls, _ = daemon
    company = _company(sqlite_session)
    _delivered(
        sqlite_session,
        company,
        status="todo",
        verdict="refuted",
        git=False,
        snapshot=True,
        comments=[
            {"author": "Developer", "text": "```python path=a.py\nx\n```"},
            {
                "author": "user",
                "text": "Fixed a line myself.",
                "created_at": datetime.now(UTC).isoformat(),
            },
        ],
    )
    _policy(sqlite_session, company, "undo_delivery")
    _cycle(sqlite_session, company, answer("undo", "undo_delivery"))
    assert calls == []
    assert _rows(sqlite_session, company)[0].evidence["person_touched"] is True


def test_a_branch_is_published_only_with_the_switch_on_and_a_remote(
    sqlite_session, daemon
):
    calls, answers = daemon
    answers["workspace.inspect"] = {"git": {"remote_url": "git@example.com:a/b.git"}}
    answers["workspace.git.publish"] = {
        "ok": True,
        "how": "compare",
        "commit": SHA,
        "branch": "fabrique/task-1",
        "base": "main",
        "url": "https://example.com/a/b/compare",
    }
    company = _company(sqlite_session)
    _delivered(sqlite_session, company)
    _policy(sqlite_session, company, "publish")
    _cycle(sqlite_session, company, answer("publish", "publish"))
    assert calls == []  # the switch is off: not even the remote is read
    policy = manager_policy.row_for(sqlite_session, company.id)
    policy.publish_enabled = True
    policy.updated_at = datetime.now(UTC).replace(tzinfo=None)
    sqlite_session.add(policy)
    sqlite_session.commit()
    _, _run = _delivered(sqlite_session, company)
    _cycle(sqlite_session, company, answer("publish", "publish"))
    # The first branch's situation was decided already (no switch); the
    # second one is published.
    assert [s for s, _ in calls] == ["workspace.inspect", "workspace.git.publish"]
    row = _rows(sqlite_session, company)[-1]
    assert row.outcome == "published"
    with pytest.raises(manager_actions.OverrideRefused) as refused:
        manager_actions.override(sqlite_session, row, "client@example.com")
    assert refused.value.status == 409 and "platform" in refused.value.detail


def test_no_remote_means_no_publication(sqlite_session, daemon):
    calls, answers = daemon
    answers["workspace.inspect"] = {"git": {"remote_url": None}}
    company = _company(sqlite_session)
    _delivered(sqlite_session, company)
    _policy(sqlite_session, company, "publish", publish_enabled=True)
    _cycle(sqlite_session, company, answer("publish", "publish"))
    assert [s for s, _ in calls] == ["workspace.inspect"]
    assert _rows(sqlite_session, company)[0].action_state == "not_allowed"


def test_the_policy_api_takes_the_new_fields(sqlite_session, test_client):
    company = _company(sqlite_session)
    url = f"/v1/companies/{company.id}/manager_policy"
    saved = test_client.put(
        url,
        json={
            "rules": ["accept_high_risk", "publish"],
            "max_files_high_risk": 2,
            "publish_enabled": True,
        },
    ).json()
    assert saved["rules"] == ["accept_high_risk", "publish"]
    assert (saved["max_files_high_risk"], saved["publish_enabled"]) == (2, True)
    assert "merge_local" in saved["catalogue"]
    refused = test_client.put(url, json={"rules": ["cancel_sprint"]})
    assert refused.status_code == 422


def test_a_hold_note_is_never_read_as_a_deliverable_after_a_mark():
    comments = [
        {"author": "Developer", "text": "```python path=a.py\nx\n```"},
        {"author": "QA", "text": "APPROVED: fine."},
        {"author": "System", "text": f"{RISK_HOLD_PREFIX} (auth)."},
        {"author": MANAGER_AGENT_NAME, "text": f"{CARRY_MARK_PREFIX}: x"},
    ]
    from services.tasks.board import blocked_item_next_step

    assert blocked_item_next_step(comments, "Developer") is None
    assert is_send_back({"author": "Manager agent", "text": f"{SEND_BACK_MARKER}."})
