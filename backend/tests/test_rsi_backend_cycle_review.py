"""The review of PR #632 on the backend's self-improvement cycle
(``services/management/rsi_cycle*.py``, ``services/release/self_change.py``),
one failing case per finding, each failing closed:

- a cycle's integration or its sprint's task branches are never offered to the
  Manager agent as ``merge_local``, and their merge, from the web or the agent,
  is refused unless a signed-in Owner makes it;
- the size is evidence only from the integration of the cycle's sprint at C,
  only when the daemon's parsed diff agrees with its inventory, and a change
  of C or of the integration forgets the earlier size;
- a keep needs current, complete usage (the sprint's turns all reported, the
  bench's counted) within the budget;
- two starts in one company cannot run their checks at once;
- a sprint of another company, or none, is not the cycle's;
- ``read_size`` stops a cycle at the kill switch before asking the daemon.
"""

from __future__ import annotations

import asyncio

import factories
import pytest
from config import settings
from manager_agent_helpers import _cycle, _policy, _rows, answer
from models.integration import SprintIntegration
from models.rsi_cycle import RsiCycle
from rsi_cycle_helpers import (
    C,
    diff,
    hosting,
    integration,
    kept,
    measured,
    refusal_codes,
    result,
    rsi_on,  # noqa: F401 - a fixture
    spend,
    started,
)
from services.management import rsi_cycle, rsi_cycle_steps, rsi_cycle_view
from services.management.manager_inbox import inbox_items
from services.platform import api_tokens, locks
from services.release import self_change
from services.release.git_publish import PublishRefused
from services.release.local_merge import integration_merge_spec
from sod_helpers import _person, auth_on  # noqa: F401 - an autouse fixture

pytestmark = pytest.mark.usefixtures("rsi_on")


def _refused(code: str, call, *args, **kwargs):
    with pytest.raises(rsi_cycle.CycleRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


# -- 1. No ordinary merge of a self-change -------------------------------------------


def test_a_cycle_s_integration_is_not_offered_as_merge_local(sqlite_session):
    company = hosting(sqlite_session, autonomy=3)
    cycle = kept(sqlite_session, company)
    _policy(sqlite_session, company, "merge_local")
    items = inbox_items(sqlite_session, company)
    assert not [i for i in items if i["category"] == "merge_local"]
    assert [i["id"] for i in items if i["category"] == "merge_self_change"] == [
        cycle.id
    ]


def test_the_manager_agent_cannot_merge_a_cycle_s_integration(sqlite_session):
    company = hosting(sqlite_session, autonomy=3)
    cycle = kept(sqlite_session, company)
    row = sqlite_session.get(SprintIntegration, cycle.integration_id)
    with pytest.raises(PublishRefused) as caught:
        integration_merge_spec(sqlite_session, row, None)  # the agent: no person
    assert caught.value.status == 403
    _policy(sqlite_session, company, "merge_local")
    _cycle(sqlite_session, company, answer("merge", "merge_local"))
    assert not [
        r for r in _rows(sqlite_session, company) if r.category == "merge_local"
    ]


def test_only_a_signed_in_owner_merges_a_cycle_s_branches(sqlite_session):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    manager, _ = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    import auth_deps

    token_user = auth_deps._from_bearer(sqlite_session, raw)
    for who in (None, "Manager agent", manager, token_user):
        with pytest.raises(PublishRefused):
            self_change.require_owner(
                sqlite_session, company.id, integration_id=cycle.integration_id,
                merger=who,
            )  # fmt: skip
        with pytest.raises(PublishRefused):
            self_change.require_owner(
                sqlite_session, company.id, sprint_id=cycle.sprint_id, merger=who
            )
    self_change.require_owner(
        sqlite_session, company.id, integration_id=cycle.integration_id, merger=owner
    )
    # Work that is not a cycle's merges as before.
    self_change.require_owner(sqlite_session, company.id, sprint_id=0, merger=None)


# -- 2, 3, 6. The size is the cycle's own, complete and current -----------------------


@pytest.mark.parametrize("which", ["other_sprint", "other_commit", "none"])
def test_size_evidence_is_bound_to_the_cycle_s_integration(sqlite_session, which):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    sprint = spend(sqlite_session, cycle)
    if which == "other_sprint":
        row = integration(
            sqlite_session, company, factories.sprint(sqlite_session, company)
        )
    elif which == "other_commit":
        row = integration(sqlite_session, company, sprint, commit="f" * 40)
    else:
        row = None
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)
    rsi_cycle_steps.observe_size(sqlite_session, cycle, diff(), integration=row)
    assert cycle.status == "awaiting_person"
    assert cycle.reasons[0]["code"] == "size_unobserved"


def test_a_parsed_diff_that_misses_a_listed_path_is_unmeasured(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    sprint = spend(sqlite_session, cycle)
    row = integration(sqlite_session, company, sprint)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C, integration_id=row.id)
    hidden = diff()
    hidden["paths"] = [*hidden["paths"], 'weird\t"name".py']  # quoted by git
    rsi_cycle_steps.observe_size(sqlite_session, cycle, hidden, integration=row)
    assert cycle.status == "awaiting_person"
    assert cycle.reasons[0]["code"] == "size_unobserved"


def test_a_new_c_or_integration_forgets_the_earlier_size(sqlite_session):
    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)
    assert cycle.diff_files == 3
    rsi_cycle_steps.record_candidate(
        sqlite_session, cycle, "e" * 40, integration_id=cycle.integration_id
    )
    assert (cycle.diff_files, cycle.diff_lines, cycle.diff_sha256) == (None,) * 3
    rsi_cycle_steps.record_decision(sqlite_session, cycle, result(candidate="e" * 40))
    assert cycle.decision == "unmeasured"


# -- 4. Usage: current and complete ---------------------------------------------------


def test_a_keep_without_the_bench_s_usage_is_unmeasured(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    sprint = spend(sqlite_session, cycle)
    row = integration(sqlite_session, company, sprint)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C, integration_id=row.id)
    rsi_cycle_steps.observe_size(sqlite_session, cycle, diff(), integration=row)
    rsi_cycle_steps.record_decision(sqlite_session, cycle, result())
    assert cycle.decision == "unmeasured"
    assert {"guard": "cycle", "code": "bench_usage_missing"} in cycle.reasons


def test_a_keep_without_the_sprint_s_usage_is_unmeasured(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)
    rsi_cycle_steps.account_tokens(sqlite_session, cycle, bench_tokens=10)
    rsi_cycle_steps.record_decision(sqlite_session, cycle, result())
    assert cycle.decision == "unmeasured"
    assert {"guard": "cycle", "code": "sprint_usage_missing"} in cycle.reasons


def test_a_turn_with_unreported_usage_leaves_the_keep_unmeasured(sqlite_session):
    from services.platform import orchestration_metrics

    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)
    task = factories.task(sqlite_session, cycle.sprint_id)
    orchestration_metrics.record_agent_turn(
        company_id=company.id, sprint_id=cycle.sprint_id, backlog_item_id=task.id,
        agent={"role": "QA"}, action="qa_review", llm_result={},
        session=sqlite_session,
    )  # fmt: skip
    rsi_cycle_steps.record_decision(sqlite_session, cycle, result())
    assert cycle.decision == "unmeasured"
    assert {"guard": "cycle", "code": "sprint_usage_incomplete"} in cycle.reasons


def test_usage_spent_after_the_last_count_is_read_at_the_decision(sqlite_session):
    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)  # 100 + 100 of 2000, counted
    from services.platform import orchestration_metrics

    task = factories.task(sqlite_session, cycle.sprint_id)
    orchestration_metrics.record_agent_turn(
        company_id=company.id, sprint_id=cycle.sprint_id, backlog_item_id=task.id,
        agent={"role": "QA"}, action="qa_review",
        llm_result={"usage": {"total_tokens": 5000}}, session=sqlite_session,
    )  # fmt: skip
    rsi_cycle_steps.record_decision(sqlite_session, cycle, result())
    assert cycle.status == "budget_exceeded" and cycle.decision == "unmeasured"
    assert cycle.tokens_reported == 5200


# -- 7. One start at a time per company -----------------------------------------------


def test_a_start_while_another_holds_the_company_s_lock_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    engine = sqlite_session.get_bind()
    with locks.held(engine, locks.RSI_CYCLE_START, company.id) as mine:
        assert mine
        _refused("start_in_progress", started, sqlite_session, company)
    assert started(sqlite_session, company, day=2).status == "running"


def test_the_start_holds_the_lock_through_its_checks_and_its_commit(
    sqlite_session, monkeypatch
):
    company = hosting(sqlite_session)
    engine = sqlite_session.get_bind()
    seen = []

    def busy() -> bool:
        with locks.held(engine, locks.RSI_CYCLE_START, company.id) as mine:
            return not mine

    checks, audit = rsi_cycle.why_not_start, rsi_cycle.audit

    def checking(*args, **kwargs):
        seen.append(("checks", busy()))
        return checks(*args, **kwargs)

    def auditing(*args, **kwargs):
        seen.append(("commit", busy()))
        return audit(*args, **kwargs)

    monkeypatch.setattr(rsi_cycle, "why_not_start", checking)
    monkeypatch.setattr(rsi_cycle, "audit", auditing)
    started(sqlite_session, company)
    assert seen == [("checks", True), ("commit", True)]
    assert busy() is False


# -- 8. The cycle's sprint is its company's -------------------------------------------


def test_a_sprint_of_another_company_or_none_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    foreign = factories.sprint(sqlite_session, hosting(sqlite_session), status="active")
    for sprint_id in (foreign.id, 987654):
        _refused("bad_sprint", rsi_cycle_steps.record_sprint, sqlite_session, cycle,
                 sprint_id)  # fmt: skip
    sqlite_session.refresh(foreign)
    assert foreign.status == "active" and cycle.sprint_id is None
    assert refusal_codes(sqlite_session, company.id).count("bad_sprint") == 2


# -- 10. The kill switch before the daemon --------------------------------------------


def test_read_size_stops_at_the_kill_switch_without_the_daemon(
    sqlite_session, monkeypatch
):
    from services.release import integration_review

    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)

    async def unreachable(*args, **kwargs):
        raise AssertionError("the daemon was asked")

    monkeypatch.setattr(integration_review, "read_diff", unreachable)
    monkeypatch.setattr(settings, "RSI_ENABLED", False)
    with pytest.raises(rsi_cycle.CycleRefused):
        asyncio.run(rsi_cycle_steps.read_size(None, sqlite_session, cycle))
    sqlite_session.refresh(cycle)
    assert cycle.status == "stopped"


def test_metrics_as_rows_are_kept_and_shown(sqlite_session):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    assert cycle.metrics == result()["metrics"]
    assert rsi_cycle_view.serialize(cycle)["metrics"] == result()["metrics"]
    assert sqlite_session.get(RsiCycle, cycle.id).metrics[0]["metric"] == (
        "criteria_rate"
    )
