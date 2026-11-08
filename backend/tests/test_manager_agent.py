"""The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief."""

from __future__ import annotations

import secrets
import threading

import auth_tokens
import pytest
from config import settings
from history_check import check_task
from models import AgentRun, AgentRunApproval, AuditLog, BacklogItem, Procedure, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, Verification
from models.company import BusinessUnit, Company, CompanyMember, User
from models.manager import ManagerDecision, ManagerPolicy
from services import (
    locks,
    manager_actions,
    manager_agent,
    manager_policy,
    manager_record,
)
from services.board import RISK_HOLD_PREFIX, SEND_BACK_MARKER, awaits_risk_acceptance
from services.manager_actions import ACCEPTED_PREFIX, MANAGER_AGENT_NAME
from services.sprint_control import HUMAN_LED_REASON
from sqlmodel import Session, SQLModel, create_engine, select


def answer(decision: str, category: str, reason: str = "The run is verified.") -> str:
    return f"DECISION: {decision}\nCATEGORY: {category}\nREASON:\n{reason}\nEND"


def _company(session: Session, autonomy: int = 1) -> Company:
    company = Company(
        company_name=f"Client {secrets.token_hex(3)}", autonomy_level=autonomy
    )
    session.add(company)
    session.commit()
    return company


def _hold(
    session: Session,
    company: Company,
    *,
    human_led: bool = True,
    verdict: str = "verified",
    tier: str = "low",
    files: int = 2,
    covers: list | None = None,
) -> BacklogItem:
    """A task QA approved that waits for a person, with its verified run."""
    sprint = Sprint(company_id=company.id, sprint_name="S1", status="active")
    session.add(sprint)
    session.commit()
    reason = HUMAN_LED_REASON if human_led else "authentication: app/auth.py"
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Add the greeting",
        assignee="Developer",
        status="blocked",
        covers=covers,
        comments=[
            {"author": "Developer", "text": "```python path=app/hi.py\nprint(1)\n```"},
            {"author": "QA", "text": "APPROVED: looks right."},
            {
                "author": "System",
                "text": f"{RISK_HOLD_PREFIX} ({reason}). Mark it done to accept it.",
            },
        ],
    )
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id},
        claims={"files": [f"app/f{i}.py" for i in range(files)]},
        artifact_digest=secrets.token_hex(8),
        artifact_size=10,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": tier}],
        )
    )
    session.commit()
    return item


def _policy(session: Session, company: Company, *rules: str, **limits) -> None:
    session.add(ManagerPolicy(company_id=company.id, rules=list(rules), **limits))
    session.commit()


def _cycle(session: Session, company: Company, reply) -> int:
    calls = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return reply(prompt) if callable(reply) else reply

    written = manager_agent.company_cycle(session, company, ask)
    _cycle.prompts = calls
    return written


def _rows(session: Session, company: Company) -> list[ManagerDecision]:
    return session.exec(
        select(ManagerDecision)
        .where(ManagerDecision.company_id == company.id)
        .order_by(ManagerDecision.id)
    ).all()


# ── The fixed answer format ───────────────────────────────────────────────────


def test_the_answer_is_read_only_in_its_fixed_form():
    parsed = manager_agent.parse_suggestion(
        answer("accept", "accept_delivery"), "accept_delivery"
    )
    assert parsed == {"decision": "accept", "reason": "The run is verified."}
    crlf = answer("send_back", "blocked_task").replace("\n", "\r\n") + "."
    assert manager_agent.parse_suggestion(crlf, "blocked_task")["decision"] == (
        "send_back"
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        "accept it",
        # Another category than the item's: the category is the platform's.
        answer("accept", "accept_high_risk"),
        # A decision the category does not allow.
        answer("approve", "accept_delivery"),
        answer("accept", "accept_delivery", reason=""),
        "DECISION: accept\nCATEGORY: accept_delivery\nREASON:\nno end",
    ],
)
def test_an_unusable_answer_is_none(text):
    assert manager_agent.parse_suggestion(text, "accept_delivery") is None


# ── The policy ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(manager_policy.ALWAYS_ESCALATE))
def test_the_confirmation_set_cannot_be_unlocked(name):
    with pytest.raises(
        manager_policy.PolicyError, match="always decided by the client"
    ):
        manager_policy.validate({"rules": [name]})
    assert not manager_policy.evaluate(
        name, "accept", {}, manager_policy.Policy(rules=list(manager_policy.RULES))
    )["allowed"]


def test_a_policy_is_validated():
    with pytest.raises(manager_policy.PolicyError, match="unknown rule"):
        manager_policy.validate({"rules": ["merge_everything"]})
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"rules": "accept_delivery"})
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"max_files": -1})
    policy = manager_policy.validate(
        {"rules": ["accept_delivery", "accept_delivery"], "max_files": 3}
    )
    assert (policy.rules, policy.max_files, policy.max_criteria) == (
        ["accept_delivery"],
        3,
        5,
    )


def test_a_rule_written_behind_the_api_is_never_applied(sqlite_session):
    company = _company(sqlite_session)
    _policy(sqlite_session, company, "accept_high_risk", "accept_delivery", "x")
    assert manager_policy.load(sqlite_session, company.id).rules == ["accept_delivery"]


GOOD_DELIVERY = {
    "human_led_hold": True,
    "verdict": "verified",
    "risk_tier": "medium",
    "files": 3,
    "criteria": 2,
}


@pytest.mark.parametrize(
    "change, why",
    [
        ({"human_led_hold": False}, "not only the autonomy level"),
        ({"verdict": "refuted"}, "refuted"),
        ({"verdict": None}, "not verified"),
        ({"risk_tier": "high"}, "high"),
        ({"risk_tier": None}, "unknown"),
        ({"files": 11}, "over the limit"),
        ({"files": None}, "unknown files"),
        ({"criteria": 6}, "criteria"),
        ({"over_budget": True}, "spend_above_budget"),
        ({"overridden_before": True}, "overrode"),
    ],
)
def test_accept_delivery_holds_only_on_its_facts(change, why):
    policy = manager_policy.Policy(rules=["accept_delivery"])
    ok = manager_policy.evaluate("accept_delivery", "accept", GOOD_DELIVERY, policy)
    assert ok == {
        "allowed": True,
        "rule": "accept_delivery",
        "why": manager_policy.RULES["accept_delivery"],
    }
    refused = manager_policy.evaluate(
        "accept_delivery", "accept", {**GOOD_DELIVERY, **change}, policy
    )
    assert not refused["allowed"] and why in refused["why"]


def test_the_rule_allows_only_its_own_decision_and_not_when_flagged():
    policy = manager_policy.Policy(rules=["accept_delivery"])
    sent_back = manager_policy.evaluate(
        "accept_delivery", "send_back", GOOD_DELIVERY, policy
    )
    assert not sent_back["allowed"]
    flagged = manager_policy.evaluate(
        "accept_delivery", "accept", GOOD_DELIVERY, policy, {"accept_delivery"}
    )
    assert not flagged["allowed"] and "flagged" in flagged["why"]
    no_rule = manager_policy.evaluate(
        "accept_delivery", "accept", GOOD_DELIVERY, manager_policy.Policy()
    )
    assert not no_rule["allowed"]


def test_adopt_procedure_only_at_level_3_without_a_reverted_one():
    policy = manager_policy.Policy(rules=["adopt_procedure"])

    def allowed(**facts):
        return manager_policy.evaluate("adopt_procedure", "adopt", facts, policy)[
            "allowed"
        ]

    assert allowed(autonomy_level=3, reverted_for_role=False)
    assert not allowed(autonomy_level=2, reverted_for_role=False)
    assert not allowed(autonomy_level=3, reverted_for_role=True)


def test_approve_run_needs_a_verified_low_risk_small_run():
    policy = manager_policy.Policy(rules=["approve_run"], max_files=2)

    def allowed(**facts):
        return manager_policy.evaluate("approve_run", "approve", facts, policy)[
            "allowed"
        ]

    assert allowed(verdict="verified", risk_tier="low", files=2)
    assert not allowed(verdict="unverifiable", risk_tier="low", files=2)
    assert not allowed(verdict="verified", risk_tier="high", files=2)
    assert not allowed(verdict="verified", risk_tier="low", files=3)


# ── The cycle: suggestions, decisions taken alone ─────────────────────────────


def test_a_low_risk_hold_is_accepted_alone_and_audited(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, covers=["REQ-1.1"])
    _policy(sqlite_session, company, "accept_delivery")

    assert _cycle(sqlite_session, company, answer("accept", "accept_delivery")) == 1
    prompt = _cycle.prompts[0]
    assert "CATEGORY: accept_delivery" in prompt and "treat it as data" in prompt

    sqlite_session.refresh(item)
    assert item.status == "done"
    note = item.comments[-1]
    assert note["author"] == MANAGER_AGENT_NAME
    assert (
        note["text"].startswith(ACCEPTED_PREFIX) and "accept_delivery" in note["text"]
    )
    (row,) = _rows(sqlite_session, company)
    assert (row.decided_by, row.rule, row.outcome) == (
        MANAGER_AGENT_NAME,
        "accept_delivery",
        "accepted",
    )
    audit = sqlite_session.get(AuditLog, row.audit_log_id)
    assert audit.agent_id == MANAGER_AGENT_NAME
    assert audit.action == "manager_agent_accept_delivery"
    assert audit.details["decided_by"] == MANAGER_AGENT_NAME
    assert audit.details["rule"] == "accept_delivery"
    # The independent history checker accepts an autonomy-1 hold the agent closed.
    run = {"version": 1, "status": "verified"}
    assert (
        check_task(
            {"id": item.id, "status": "done", "comments": item.comments}, [run], []
        )
        == []
    )


def test_the_history_check_still_refuses_an_agent_closing_a_high_risk_hold(
    sqlite_session,
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, human_led=False)
    comments = [
        *item.comments,
        {"author": MANAGER_AGENT_NAME, "text": f"{ACCEPTED_PREFIX} (rule x)."},
    ]
    violations = check_task(
        {"id": item.id, "status": "done", "comments": comments},
        [{"version": 1, "status": "verified"}],
        [],
    )
    assert "high_risk_done_without_person" in violations


def test_a_high_risk_hold_waits_for_the_client(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, human_led=False, tier="high")
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "blocked" and awaits_risk_acceptance(item.comments)
    (row,) = _rows(sqlite_session, company)
    assert (row.category, row.suggested_decision, row.decided_by) == (
        "accept_high_risk",
        "accept",
        None,
    )


def test_without_a_policy_the_agent_only_suggests(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert _rows(sqlite_session, company)[0].suggested_decision == "accept"


def test_over_budget_nothing_is_taken_alone(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    sqlite_session.add(
        BusinessUnit(
            company_id=company.id,
            unit_name="eng",
            token_quota_limit=100,
            token_quota_used=100,
        )
    )
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_an_unusable_answer_is_left_to_the_client_and_not_asked_again(
    sqlite_session,
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    assert _cycle(sqlite_session, company, "Sure, accept it!") == 1
    (row,) = _rows(sqlite_session, company)
    assert row.suggested_decision is None and row.decided_by is None
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert _cycle(sqlite_session, company, answer("accept", "accept_delivery")) == 0
    assert _cycle.prompts == []


def test_a_failed_call_writes_nothing(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)

    def boom(prompt):
        raise RuntimeError("proxy down")

    assert _cycle(sqlite_session, company, boom) == 0
    assert _rows(sqlite_session, company) == []


def test_one_situation_gets_one_row_behind_the_lock(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    fields = {
        "company_id": company.id,
        "item_kind": "task",
        "item_id": item.id,
        "item_state": "blocked:2",
        "category": "accept_delivery",
    }
    assert manager_agent._claim(sqlite_session, ManagerDecision(**fields))
    assert not manager_agent._claim(sqlite_session, ManagerDecision(**fields))
    assert len(_rows(sqlite_session, company)) == 1


def test_two_replicas_never_decide_the_same_item(tmp_path):
    """The second replica skips an item the first is asking about, and asks no model."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manager.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        company = _company(session)
        _hold(session, company)
        _policy(session, company, "accept_delivery")
        company_id = company.id

    asking = threading.Event()
    second_done = threading.Event()
    calls = []

    def slow(prompt):
        calls.append("first")
        asking.set()
        second_done.wait(timeout=30)
        return answer("accept", "accept_delivery")

    def fast(prompt):
        calls.append("second")
        return answer("accept", "accept_delivery")

    results = {}

    def replica(name, ask):
        with Session(engine) as session:
            company = session.get(Company, company_id)
            results[name] = manager_agent.company_cycle(session, company, ask)

    first = threading.Thread(target=replica, args=("first", slow))
    first.start()
    assert asking.wait(timeout=30)
    replica("second", fast)
    second_done.set()
    first.join(timeout=30)

    assert results == {"first": 1, "second": 0}
    assert calls == ["first"]
    with Session(engine) as session:
        rows = session.exec(select(ManagerDecision)).all()
        assert len(rows) == 1 and rows[0].decided_by == MANAGER_AGENT_NAME
        # Once the first is done, a later round sees the situation and asks nothing.
        company = session.get(Company, company_id)
        assert manager_agent.company_cycle(session, company, fast) == 0
    assert calls == ["first"]


def test_the_loop_skips_a_round_another_replica_runs(sqlite_session, monkeypatch):
    engine = sqlite_session.get_bind()
    ran = []
    monkeypatch.setattr(manager_agent, "manager_cycle", lambda e: ran.append(e) or 1)
    with locks.held(engine, locks.MANAGER_AGENT) as mine:
        assert mine
        assert (
            locks.run_exclusive(
                engine, locks.MANAGER_AGENT, manager_agent.manager_cycle, engine
            )
            is None
        )
    assert ran == []


def test_a_procedure_is_adopted_alone_only_at_level_3(sqlite_session):
    for autonomy, status in ((2, "proposed"), (3, "active")):
        company = _company(sqlite_session, autonomy=autonomy)
        proc = Procedure(
            company_id=company.id,
            role="Developer",
            title="Run the tests",
            body="Run them before delivering.",
            proposed_by="ScrumMaster",
        )
        sqlite_session.add(proc)
        sqlite_session.commit()
        _policy(sqlite_session, company, "adopt_procedure")
        _cycle(sqlite_session, company, answer("adopt", "adopt_procedure"))
        sqlite_session.refresh(proc)
        assert proc.status == status
        if status == "active":
            assert proc.decided_by == MANAGER_AGENT_NAME


def _outside_run(session, company, verdict="verified", tier="low"):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"pr-{secrets.token_hex(3)}",
        producer="github",
        claims={"files": ["README.md"]},
        artifact_digest=secrets.token_hex(8),
        artifact_size=10,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": tier}],
        )
    )
    session.commit()
    return run


def _approvals(session, run):
    return session.exec(
        select(AgentRunApproval)
        .where(AgentRunApproval.agent_run_id == run.id)
        .order_by(AgentRunApproval.id)
    ).all()


def test_an_outside_run_is_approved_alone_through_the_approval_record(
    sqlite_session,
):
    company = _company(sqlite_session)
    run = _outside_run(sqlite_session, company)
    refuted = _outside_run(sqlite_session, company, verdict="refuted")
    _policy(sqlite_session, company, "approve_run")
    _cycle(sqlite_session, company, answer("approve", "approve_run"))
    (approval,) = _approvals(sqlite_session, run)
    assert (approval.approver_name, approval.decision) == (
        MANAGER_AGENT_NAME,
        "approved",
    )
    assert sqlite_session.get(AuditLog, approval.audit_log_id).agent_id == (
        MANAGER_AGENT_NAME
    )
    assert _approvals(sqlite_session, refuted) == []


# ── Routes: policy, override, record, brief ───────────────────────────────────


@pytest.fixture()
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session: Session, company_id: int | None, role: str) -> dict[str, str]:
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
        full_name=role,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_a_manager_writes_the_policy_and_a_viewer_reads_it(
    sqlite_session, test_client, auth_on
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, None, "Manager")
    url = f"/v1/companies/{company.id}/manager_policy"

    empty = test_client.get(url, headers=viewer).json()
    assert empty["rules"] == [] and "accept_high_risk" in empty["always_escalate"]
    assert test_client.put(url, json={"rules": []}, headers=viewer).status_code == 403
    assert test_client.get(url, headers=outsider).status_code == 403
    refused = test_client.put(
        url, json={"rules": ["approve_specification"]}, headers=manager
    )
    assert refused.status_code == 422 and "always decided" in refused.json()["detail"]
    saved = test_client.put(
        url, json={"rules": ["accept_delivery"], "max_files": 4}, headers=manager
    ).json()
    assert saved["rules"] == ["accept_delivery"] and saved["max_files"] == 4
    assert saved["updated_by"].startswith("manager-")
    assert test_client.get(url, headers=viewer).json()["rules"] == ["accept_delivery"]
    assert sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == "manager_policy_updated",
        )
    ).first()


def test_the_client_overrides_a_decision_and_it_is_counted(
    sqlite_session, test_client, auth_on, monkeypatch
):
    monkeypatch.setattr(settings, "MANAGER_AGENT_ENABLED", True)
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (row,) = _rows(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    other = _member(sqlite_session, _company(sqlite_session).id, "Manager")

    inbox = test_client.get(f"/v1/inbox?company_id={company.id}", headers=viewer)
    assert [d["id"] for d in inbox.json()["agent_decisions"]] == [row.id]

    url = f"/v1/manager_decisions/{row.id}/override"
    assert test_client.post(url, headers=viewer).status_code == 403
    assert test_client.post(url, headers=other).status_code == 403
    done = test_client.post(url, headers=manager)
    assert done.status_code == 200 and done.json()["outcome"] == "waits_for_a_person"
    assert test_client.post(url, headers=manager).status_code == 409

    sqlite_session.refresh(item)
    assert item.status == "blocked" and awaits_risk_acceptance(item.comments)
    inbox = test_client.get(f"/v1/inbox?company_id={company.id}", headers=viewer)
    (task,) = inbox.json()["blocked_tasks"]
    assert task["id"] == item.id and task["risk_hold"]
    assert task["suggestion"]["overridden_by"].startswith("manager-")

    record = test_client.get(
        f"/v1/manager_record?company_id={company.id}", headers=viewer
    ).json()
    assert record["categories"]["accept_delivery"]["taken_alone"] == 1
    assert record["categories"]["accept_delivery"]["overridden"] == 1
    assert record["total"]["override_rate"] == 1.0
    assert (
        test_client.get(
            f"/v1/manager_record?company_id={company.id}", headers=other
        ).status_code
        == 403
    )
    # Overridden once, the agent never takes that item alone again.
    sqlite_session.refresh(item)
    item.comments = [*item.comments, {"author": "System", "text": "Re-checked."}]
    sqlite_session.add(item)
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_overriding_an_adopted_procedure_brings_back_the_one_it_replaced(
    sqlite_session, test_client
):
    company = _company(sqlite_session, autonomy=3)
    old = Procedure(
        company_id=company.id,
        role="QA",
        title="Review",
        body="v1",
        proposed_by="ScrumMaster",
        status="active",
    )
    new = Procedure(
        company_id=company.id,
        role="QA",
        title="Review",
        body="v2",
        proposed_by="ScrumMaster",
    )
    sqlite_session.add_all([old, new])
    sqlite_session.commit()
    _policy(sqlite_session, company, "adopt_procedure")
    _cycle(sqlite_session, company, answer("adopt", "adopt_procedure"))
    sqlite_session.refresh(old)
    assert old.status == "retired"
    (row,) = _rows(sqlite_session, company)
    result = test_client.post(f"/v1/manager_decisions/{row.id}/override").json()
    assert result["outcome"] == "retired, 1 restored"
    sqlite_session.refresh(old)
    sqlite_session.refresh(new)
    assert (old.status, new.status) == ("active", "retired")


def test_overriding_an_approved_run_records_the_client_s_rejection(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    run = _outside_run(sqlite_session, company)
    _policy(sqlite_session, company, "approve_run")
    _cycle(sqlite_session, company, answer("approve", "approve_run"))
    (row,) = _rows(sqlite_session, company)
    test_client.post(f"/v1/manager_decisions/{row.id}/override")
    latest = _approvals(sqlite_session, run)[-1]
    assert (latest.approver_name, latest.decision) == ("local demo", "rejected")


def test_the_record_counts_agreement_with_the_client_and_goes_into_the_prompt(
    sqlite_session,
):
    company = _company(sqlite_session)
    agreed = _hold(sqlite_session, company, human_led=False)
    missed = _hold(sqlite_session, company, human_led=False)
    unusable = _hold(sqlite_session, company, human_led=False)
    replies = iter(
        [
            answer("send_back", "accept_high_risk"),
            answer("accept", "accept_high_risk"),
            "no idea",
        ]
    )
    _cycle(sqlite_session, company, lambda prompt: next(replies))
    # The client sends the first two back and accepts the third.
    for item, status, text in (
        (agreed, "todo", f"{SEND_BACK_MARKER} by a person."),
        (missed, "todo", f"{SEND_BACK_MARKER} by a person."),
        (unusable, "done", "High-risk delivery accepted by a person."),
    ):
        sqlite_session.refresh(item)
        item.status = status
        item.comments = [*item.comments, {"author": "user", "text": text}]
        sqlite_session.add(item)
    sqlite_session.commit()

    record = manager_record.track_record(sqlite_session, company.id)
    counts = record["categories"]["accept_high_risk"]
    assert (counts["decided"], counts["agreed"], counts["unusable"]) == (3, 1, 1)
    context = manager_record.record_context(record)
    assert "matched the client's decision 1 of 3" in context
    assert "1 without a usable answer" in context

    _hold(sqlite_session, company, human_led=False)
    _cycle(sqlite_session, company, answer("escalate", "accept_high_risk"))
    assert "matched the client's decision 1 of 3" in _cycle.prompts[0]


def test_a_rule_whose_overrides_rise_is_flagged_and_not_applied(sqlite_session):
    company = _company(sqlite_session)
    _policy(sqlite_session, company, "accept_delivery")
    for _ in range(2):
        _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    for row in _rows(sqlite_session, company):
        manager_actions.override(sqlite_session, row, "client")
    record = manager_record.track_record(sqlite_session, company.id)
    assert record["flagged_rules"] == ["accept_delivery"]
    fresh = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(fresh)
    assert fresh.status == "blocked"
    # The policy is untouched: the agent narrows by not applying, never edits.
    assert manager_policy.load(sqlite_session, company.id).rules == ["accept_delivery"]


def test_the_brief_says_what_got_done_decided_waits_and_cost(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    accepted = _hold(sqlite_session, company)
    _hold(sqlite_session, company, human_led=False)
    _policy(sqlite_session, company, "accept_delivery")
    replies = iter(
        [answer("accept", "accept_delivery"), answer("escalate", "accept_high_risk")]
    )
    _cycle(sqlite_session, company, lambda prompt: next(replies))
    sqlite_session.add(
        AuditLog(company_id=company.id, action="generate_code", cost_tokens=1234)
    )
    sqlite_session.commit()

    brief = test_client.get(f"/v1/companies/{company.id}/brief").json()
    assert brief["done"]["count"] == 1
    assert brief["done"]["tasks"][0]["id"] == accepted.id
    assert brief["agent"]["taken_alone"] == 1
    (decision,) = brief["agent"]["decisions"]
    assert decision["rule"] == "accept_delivery" and decision["reason"]
    assert brief["waiting"]["risk_holds"] == 1
    assert brief["cost"]["tokens_total"] >= 1234
    assert brief["record"]["total"]["taken_alone"] == 1
    assert test_client.get("/v1/companies/999999/brief").status_code == 404


def test_the_brief_is_company_scoped(sqlite_session, test_client, auth_on):
    company = _company(sqlite_session)
    other = _member(sqlite_session, _company(sqlite_session).id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/brief"
    assert test_client.get(url, headers=other).status_code == 403
    assert test_client.get(url, headers=viewer).status_code == 200
