"""A sprint stops spending at the company's budget in money
(``services/loop/sprint_budget.py``, from Paperclip's budget policies). The
cost is the platform's own price of the proxy's usage (``run_cost``); here it
is set directly, the pricing has its own tests (``test_run_cost.py``)."""

from uuid import uuid4

import pytest
from models import BacklogItem, Company, Sprint
from models.audit import AuditLog
from services.contracts import company_limits
from services.loop import sprint_budget, sprint_control
from services.models import run_cost
from sqlmodel import select


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _cost(monkeypatch, **summary):
    out = {
        "cost_cents": None,
        "reason": None,
        "estimated_turns": 0,
        "unavailable_turns": 0,
    }
    out.update(summary)
    monkeypatch.setattr(
        run_cost, "sprint_costs", lambda s, c, ids: {sid: dict(out) for sid in ids}
    )


def _sprint(session, limits):
    company = Company(company_name=f"Budget {uuid4().hex[:6]}", limits=limits)
    session.add(company)
    session.commit()
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="S", status="active"
    )
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id, title="T", status="todo", assignee="Developer"
    )
    session.add(item)
    session.commit()
    return sprint, item


def _rows(session, sprint, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"sprint:{sprint.id}:budget",
            AuditLog.action == action,
        )
    ).all()


def _hold(session, item):
    verdict = sprint_budget.hold(session.get_bind(), item.id)
    session.expire_all()
    return verdict


def test_without_a_budget_nothing_is_checked(sqlite_session, monkeypatch):
    sprint, item = _sprint(sqlite_session, None)
    _cost(monkeypatch, reason="no_price")

    assert _hold(sqlite_session, item) is None
    assert sqlite_session.get(Sprint, sprint.id).status == "active"


def test_at_the_budget_the_sprint_pauses_and_the_task_waits(
    sqlite_session, monkeypatch
):
    sprint, item = _sprint(sqlite_session, {"sprint_budget_cents": 500})
    _cost(monkeypatch, cost_cents=500.0)

    verdict = _hold(sqlite_session, item)

    assert "spent $5.00 of its $5.00 budget" in verdict["pause"]
    stored = sqlite_session.get(Sprint, sprint.id)
    assert (
        stored.status == sprint_control.PAUSED
        and stored.pause_reason == verdict["pause"]
    )
    task = sqlite_session.get(BacklogItem, item.id)
    assert task.status == "todo" and task.comments[-1]["text"].startswith("Paused: ")
    [row] = _rows(sqlite_session, sprint, sprint_budget.REACHED_ACTION)
    assert row.result["spent_cents"] == 500.0 and row.result["budget_cents"] == 500
    assert row.chain_hash
    # Paused: the next claim neither checks again nor records a second incident.
    assert _hold(sqlite_session, item) is None
    assert len(_rows(sqlite_session, sprint, sprint_budget.REACHED_ACTION)) == 1


def test_an_unknown_cost_is_not_under_the_budget(sqlite_session, monkeypatch):
    sprint, item = _sprint(sqlite_session, {"sprint_budget_cents": 500})
    _cost(monkeypatch, reason="no_reported_usage")

    verdict = _hold(sqlite_session, item)

    assert "cannot be priced (no_reported_usage)" in verdict["pause"]
    assert sqlite_session.get(Sprint, sprint.id).status == sprint_control.PAUSED


def test_a_sprint_with_no_turns_yet_runs(sqlite_session, monkeypatch):
    sprint, item = _sprint(sqlite_session, {"sprint_budget_cents": 500})
    _cost(monkeypatch, reason=run_cost.NO_TURNS)

    assert _hold(sqlite_session, item) is None
    assert sqlite_session.get(Sprint, sprint.id).status == "active"


def test_past_the_warning_it_warns_once_and_work_goes_on(sqlite_session, monkeypatch):
    sprint, item = _sprint(
        sqlite_session, {"sprint_budget_cents": 1000, "budget_warn_percent": 50}
    )
    _cost(monkeypatch, cost_cents=400.0)
    assert _hold(sqlite_session, item) is None

    _cost(monkeypatch, cost_cents=600.0, unavailable_turns=2)
    verdict = _hold(sqlite_session, item)
    assert verdict["warn"].startswith("The sprint spent $6.00, 50% or more")
    assert _hold(sqlite_session, item) is None  # once per sprint

    assert sqlite_session.get(Sprint, sprint.id).status == "active"
    [row] = _rows(sqlite_session, sprint, sprint_budget.WARNING_ACTION)
    assert row.result["turns_without_usage"] == 2


def test_the_default_warning_is_eighty_percent(sqlite_session, monkeypatch):
    _, item = _sprint(sqlite_session, {"sprint_budget_cents": 1000})
    _cost(monkeypatch, cost_cents=799.0)
    assert _hold(sqlite_session, item) is None
    _cost(monkeypatch, cost_cents=800.0)
    assert "80% or more" in _hold(sqlite_session, item)["warn"]


def test_the_budget_stays_in_the_range_the_code_holds():
    assert company_limits.validate({"sprint_budget_cents": 0}) == {
        "sprint_budget_cents": 0
    }
    for bad in (
        {"sprint_budget_cents": -1},
        {"budget_warn_percent": 0},
        {"budget_warn_percent": 101},
        {"sprint_budget_cents": 10_000_001},
    ):
        with pytest.raises(company_limits.InvalidLimits):
            company_limits.validate(bad)
    # A stored value that no longer validates is dropped, never applied.
    assert company_limits.parse({"sprint_budget_cents": -5}) == {}


class _Nats:
    jetstream_enabled = False

    def __init__(self):
        self.published = []

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))


def test_the_worker_does_not_run_a_task_of_a_sprint_at_its_budget(
    sqlite_session, monkeypatch
):
    import asyncio

    import worker_stages
    from services.loop.worker import intake, task_run

    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    ran = []

    async def _run(nats_client, backlog_item_id, ctx):
        ran.append(backlog_item_id)

    monkeypatch.setattr(task_run, "_run_claimed_task", _run)
    sprint, item = _sprint(sqlite_session, {"sprint_budget_cents": 100})
    assignment = {"backlog_item_id": item.id, "sprint_id": sprint.id}

    _cost(monkeypatch, cost_cents=50.0)
    asyncio.run(intake._on_task_assign(_Nats(), None, assignment))
    assert ran == [item.id]

    sqlite_session.expire_all()
    item = sqlite_session.get(BacklogItem, item.id)
    item.status = "todo"
    sqlite_session.add(item)
    sqlite_session.commit()
    _cost(monkeypatch, cost_cents=120.0)
    nats = _Nats()
    asyncio.run(intake._on_task_assign(nats, None, assignment))

    assert ran == [item.id]
    sqlite_session.expire_all()
    assert sqlite_session.get(Sprint, sprint.id).status == sprint_control.PAUSED
    assert sqlite_session.get(BacklogItem, item.id).status == "todo"
    events = [p for _, p in nats.published if isinstance(p, dict)]
    assert any(e.get("semantic_type") == "sprint.paused_budget" for e in events)
