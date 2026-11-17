"""The self-improvement cycle in the backend (``services/management/rsi_cycle.py``;
``docs/design/rsi-loop.md``, PR 10 of section 8; REQ-22 to REQ-26), refusals
first:

- ``RSI_ENABLED`` off, or a company not marked as hosting the factory: nothing
  starts, and a running cycle stops at its next step (REQ-3, REQ-24);
- a second cycle the same day is refused;
- after N cycles without a keep nothing starts until a signed-in Owner
  resumes: not a Manager, not an API token, not the Manager agent (REQ-23);
- a diff over the size cap, or one the daemon did not list in full, leaves
  the cycle unmeasured and waiting for a person, and a keep without an
  observed size is recorded unmeasured (REQ-25);
- unknown first-sprint tokens start nothing; passing the budget ends the
  cycle ``budget_exceeded``, stops its sprint and keeps nothing (REQ-22);
- no policy names ``merge_self_change``, and the Manager agent cannot merge a
  self-change at any autonomy; a person's merge is pinned to C (REQ-17).

Then what is allowed. Every refusal and every step is audited (REQ-26).
"""

from __future__ import annotations

from datetime import datetime

import factories
import pytest
from config import settings
from manager_agent_helpers import _cycle, _policy, _rows, answer
from models.rsi_cycle import RsiCycle
from rsi_cycle_helpers import (
    B,
    C,
    audits,
    diff,
    ended_without_keep,
    finding,
    hosting,
    kept,
    refusal_codes,
    rsi_on,  # noqa: F401 - a fixture
    started,
)
from services.management import (
    manager_policy,
    rsi_cycle,
    rsi_cycle_steps,
    rsi_cycle_view,
)
from services.platform import api_tokens
from sod_helpers import _person, auth_on  # noqa: F401 - an autouse fixture
from sqlmodel import select

pytestmark = pytest.mark.usefixtures("rsi_on")


def _refused(code: str, call, *args, **kwargs):
    with pytest.raises(rsi_cycle.CycleRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


# -- The kill switches -----------------------------------------------------------


def test_with_rsi_off_nothing_starts(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "RSI_ENABLED", False)
    company = hosting(sqlite_session)
    _refused("rsi_disabled", started, sqlite_session, company)
    assert sqlite_session.exec(select(RsiCycle)).all() == []
    assert refusal_codes(sqlite_session, company.id) == ["rsi_disabled"]
    assert (
        rsi_cycle_view.state(sqlite_session, company.id)["why_not_start"]
        == "rsi_disabled"
    )


def test_a_company_not_hosting_the_factory_starts_nothing(sqlite_session):
    company = hosting(sqlite_session, on=False)
    _refused("not_self_hosting", started, sqlite_session, company)
    assert refusal_codes(sqlite_session, company.id) == ["not_self_hosting"]


@pytest.mark.parametrize("switch", ["rsi", "company"])
def test_a_running_cycle_stops_at_its_next_step(sqlite_session, monkeypatch, switch):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    sprint = factories.sprint(sqlite_session, company, status="active")
    factories.task(sqlite_session, sprint, status="todo")
    rsi_cycle_steps.record_sprint(sqlite_session, cycle, sprint.id)
    if switch == "rsi":
        monkeypatch.setattr(settings, "RSI_ENABLED", False)
    else:
        company.protected_paths_enabled = False
        sqlite_session.add(company)
        sqlite_session.commit()
    _refused(
        "rsi_disabled" if switch == "rsi" else "not_self_hosting",
        rsi_cycle_steps.record_candidate,
        sqlite_session,
        cycle,
        C,
    )
    sqlite_session.refresh(cycle)
    sqlite_session.refresh(sprint)
    assert cycle.status == "stopped" and cycle.decision == "unmeasured"
    assert sprint.status == "cancelled"
    assert len(audits(sqlite_session, company.id, "rsi_cycle_stopped")) == 1


# -- One cycle a day, and the stop after N -----------------------------------------


def test_a_second_cycle_the_same_day_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    first = started(sqlite_session, company, day=3)
    rsi_cycle_steps.record_decision(sqlite_session, first, {"decision": "revert"})
    other = finding(sqlite_session, company.id, 99)
    _refused(
        "daily_limit",
        rsi_cycle.start,
        sqlite_session,
        company.id,
        other.id,
        B,
        now=datetime(2026, 11, 3, 23, 59),
    )
    assert refusal_codes(sqlite_session, company.id) == ["daily_limit"]
    # The next day it may.
    nxt = rsi_cycle.start(
        sqlite_session, company.id, other.id, B, now=datetime(2026, 11, 4, 0, 1)
    )
    assert nxt.status == "running"


def test_a_cycle_does_not_start_while_another_runs(sqlite_session):
    company = hosting(sqlite_session)
    started(sqlite_session, company, day=3)
    _refused("cycle_running", started, sqlite_session, company, day=4)


def _stop(session, company):
    for day in (1, 2, 3):
        ended_without_keep(session, company, day=day)
    assert rsi_cycle.stopped(session, company.id)


def test_after_n_cycles_without_a_keep_nothing_starts(sqlite_session):
    company = hosting(sqlite_session)
    _stop(sqlite_session, company)
    _refused("stopped", started, sqlite_session, company, day=4)
    assert (
        rsi_cycle_view.state(sqlite_session, company.id)["why_not_start"] == "stopped"
    )


def test_only_a_signed_in_owner_resumes(sqlite_session, test_client):
    company = hosting(sqlite_session)
    _stop(sqlite_session, company)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    manager, manager_h = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    url = f"/v1/companies/{company.id}/rsi/resume"
    for headers in (manager_h, {"Authorization": f"Bearer {raw}"}):
        assert test_client.post(url, headers=headers).status_code == 403
    assert test_client.post(url).status_code == 401
    # Not the Manager agent, a Manager or nobody, called in-process either.
    for who in ("Manager agent", manager, None):
        _refused("not_an_owner", rsi_cycle.resume, sqlite_session, company.id, who)
    assert rsi_cycle.stopped(sqlite_session, company.id)
    assert audits(sqlite_session, company.id, "rsi_cycle_resumed") == []


# -- The size cap, observed ---------------------------------------------------------


@pytest.mark.parametrize(
    "observed, code",
    [
        (diff(files=21, lines_per_file=1), "size_cap"),
        (diff(files=3, lines_per_file=201), "size_cap"),
        (diff(paths_truncated=True), "size_unobserved"),
        (diff(truncated=True), "size_unobserved"),
        ({"files": [], "paths": None}, "size_unobserved"),
    ],
)
def test_a_diff_over_the_cap_or_unread_waits_for_a_person(
    sqlite_session, observed, code
):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)
    rsi_cycle_steps.observe_size(sqlite_session, cycle, observed)
    assert cycle.status == "awaiting_person" and cycle.decision == "unmeasured"
    assert cycle.reasons[0]["code"] == code
    assert audits(sqlite_session, company.id, "rsi_cycle_size_refused")
    # No keep afterwards.
    _refused(
        "not_running",
        rsi_cycle_steps.record_decision,
        sqlite_session,
        cycle,
        {"decision": "keep"},
    )


def test_a_keep_without_an_observed_size_is_unmeasured(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)
    rsi_cycle_steps.record_decision(sqlite_session, cycle, {"decision": "keep"})
    assert cycle.status == "unmeasured" and cycle.decision == "unmeasured"
    assert {"guard": "cycle", "code": "size_unobserved"} in cycle.reasons
    assert rsi_cycle_view.inbox_items(sqlite_session, company) == []


def test_a_keep_without_c_is_unmeasured(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.observe_size(sqlite_session, cycle, diff())
    rsi_cycle_steps.record_decision(sqlite_session, cycle, {"decision": "keep"})
    assert cycle.decision == "unmeasured"
    assert {"guard": "cycle", "code": "no_candidate"} in cycle.reasons


# -- The budget ---------------------------------------------------------------------


@pytest.mark.parametrize("first", [0, -5])
def test_unknown_first_sprint_tokens_start_nothing(sqlite_session, monkeypatch, first):
    monkeypatch.setattr(settings, "RSI_FIRST_SPRINT_TOKENS", first)
    company = hosting(sqlite_session)
    _refused("budget_unknown", started, sqlite_session, company)
    assert rsi_cycle.token_budget() == (None, "budget_unknown")


def test_passing_the_budget_ends_the_cycle_and_stops_its_sprint(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    assert cycle.token_budget == 2000  # twice the first sprint
    sprint = factories.sprint(sqlite_session, company, status="active")
    factories.task(sqlite_session, sprint, status="in_progress")
    rsi_cycle_steps.record_sprint(sqlite_session, cycle, sprint.id)
    rsi_cycle_steps.account_tokens(sqlite_session, cycle, bench_tokens=2001)
    sqlite_session.refresh(sprint)
    assert cycle.status == "budget_exceeded" and cycle.decision == "unmeasured"
    assert sprint.status == "cancelled"
    [row] = audits(sqlite_session, company.id, "rsi_cycle_budget_exceeded")
    assert row.result["tokens"] == 2001 and row.result["budget"] == 2000
    _refused(
        "not_running",
        rsi_cycle_steps.record_decision,
        sqlite_session,
        cycle,
        {"decision": "keep"},
    )


def test_a_configured_budget_is_capped_at_twice_the_first_sprint(monkeypatch):
    monkeypatch.setattr(settings, "RSI_CYCLE_TOKEN_BUDGET", 10_000)
    assert rsi_cycle.token_budget() == (2000, None)
    monkeypatch.setattr(settings, "RSI_CYCLE_TOKEN_BUDGET", 1500)
    assert rsi_cycle.token_budget() == (1500, None)


@pytest.mark.parametrize("tokens", [-1, True, "10"])
def test_tokens_are_a_count(sqlite_session, tokens):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    _refused(
        "bad_tokens", rsi_cycle_steps.account_tokens, sqlite_session, cycle, tokens
    )


# -- No rule merges a self-change -----------------------------------------------------


def test_no_policy_names_merge_self_change():
    assert "merge_self_change" in manager_policy.ALWAYS_ESCALATE
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"rules": ["merge_self_change"]})
    verdict = manager_policy.evaluate(
        "merge_self_change",
        "merge",
        {},
        manager_policy.Policy(rules=list(manager_policy.RULES)),
    )
    assert verdict["allowed"] is False


@pytest.mark.parametrize("autonomy", [1, 2, 3])
def test_the_manager_agent_cannot_merge_a_self_change(sqlite_session, autonomy):
    company = hosting(sqlite_session, autonomy=autonomy)
    cycle = kept(sqlite_session, company)
    _policy(sqlite_session, company, *manager_policy.RULES)
    _cycle(sqlite_session, company, answer("merge", "merge_self_change"))
    [row] = [r for r in _rows(sqlite_session, company) if r.item_kind == "rsi_cycle"]
    assert row.suggested_decision == "merge"
    assert row.action_state == "not_allowed"
    assert "merge_self_change" in (row.refusal_reason or "")
    sqlite_session.refresh(cycle)
    assert cycle.status == "awaiting_merge" and cycle.merged_by is None


def test_a_merge_is_pinned_to_c_and_recorded_by_an_owner(sqlite_session, test_client):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    owner, owner_h = _person(sqlite_session, company.id, "Owner")
    _, manager_h = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    url = f"/v1/companies/{company.id}/rsi/cycles/{cycle.id}/merge"
    for headers in (manager_h, {"Authorization": f"Bearer {raw}"}):
        assert (
            test_client.post(url, json={"merged_sha": C}, headers=headers).status_code
            == 403
        )
    moved = test_client.post(url, json={"merged_sha": "e" * 40}, headers=owner_h)
    assert moved.status_code == 409 and "C's recorded sha" in moved.json()["detail"]
    assert "not_pinned" in refusal_codes(sqlite_session, company.id)
    _refused(
        "not_an_owner",
        rsi_cycle.record_person_merge,
        sqlite_session,
        cycle,
        C,
        "Manager agent",
    )
    sqlite_session.refresh(cycle)
    assert cycle.status == "awaiting_merge"


def test_only_a_kept_cycle_is_merged(sqlite_session):
    company = hosting(sqlite_session)
    cycle = ended_without_keep(sqlite_session, company, day=1)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    _refused("not_kept", rsi_cycle.record_person_merge, sqlite_session, cycle, C, owner)


@pytest.mark.parametrize("bad", [{"decision": "ship"}, {}, {"decision": None}])
def test_a_decision_is_keep_revert_or_unmeasured(sqlite_session, bad):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    _refused(
        "bad_decision", rsi_cycle_steps.record_decision, sqlite_session, cycle, bad
    )


def test_bad_shas_and_names_are_refused(sqlite_session):
    company = hosting(sqlite_session)
    row = finding(sqlite_session, company.id)
    sqlite_session.commit()
    _refused(
        "bad_baseline", rsi_cycle.start, sqlite_session, company.id, row.id, "b" * 39
    )
    _refused(
        "bad_cycle_key",
        rsi_cycle.start,
        sqlite_session,
        company.id,
        row.id,
        B,
        cycle_key="x..y",
    )
    cycle = rsi_cycle.start(sqlite_session, company.id, row.id, B)
    _refused(
        "bad_candidate",
        rsi_cycle_steps.record_candidate,
        sqlite_session,
        cycle,
        "C" * 40,
    )


def test_a_finding_without_a_measure_or_of_another_company_starts_nothing(
    sqlite_session,
):
    company = hosting(sqlite_session)
    other = hosting(sqlite_session)
    foreign = finding(sqlite_session, other.id)
    bare = finding(sqlite_session, company.id, 7)
    bare.target_measure = None
    sqlite_session.add(bare)
    sqlite_session.commit()
    for row in (foreign, bare):
        _refused("finding", rsi_cycle.start, sqlite_session, company.id, row.id, B)


# -- What is allowed -------------------------------------------------------------------


def test_a_kept_cycle_waits_in_the_inbox_and_an_owner_merges_it(
    sqlite_session, test_client
):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    assert cycle.status == "awaiting_merge" and cycle.decision == "keep"
    assert (cycle.diff_files, cycle.diff_lines, cycle.tokens_reported) == (3, 30, 100)
    [item] = rsi_cycle_view.inbox_items(sqlite_session, company)
    assert item["category"] == "merge_self_change" and C in item["text"]
    assert item["facts"]["person_merges_required"] == 5
    owner, owner_h = _person(sqlite_session, company.id, "Owner")
    done = test_client.post(
        f"/v1/companies/{company.id}/rsi/cycles/{cycle.id}/merge",
        json={"merged_sha": C},
        headers=owner_h,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "merged" and done.json()["merged_by"] == owner.email
    assert rsi_cycle_view.inbox_items(sqlite_session, company) == []
    assert [
        r.action
        for r in audits(sqlite_session, company.id)
        if r.action.startswith("rsi_cycle_")
    ] == [
        "rsi_cycle_started",
        "rsi_cycle_candidate",
        "rsi_cycle_size",
        "rsi_cycle_tokens",
        "rsi_cycle_decided",
        "rsi_cycle_merged",
    ]
    merged = audits(sqlite_session, company.id, "rsi_cycle_merged")[0]
    assert merged.agent_id == f"human:{owner.email}"
    assert merged.result["kept_merged_by_person"] == 1
    assert (
        rsi_cycle_view.state(sqlite_session, company.id)["kept_merged_by_person"] == 1
    )


def test_an_owner_resumes_after_the_stop_and_a_cycle_starts(
    sqlite_session, test_client
):
    company = hosting(sqlite_session)
    _stop(sqlite_session, company)
    owner, owner_h = _person(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/rsi/resume"
    resumed = test_client.post(url, headers=owner_h)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["stopped"] is False
    assert test_client.post(url, headers=owner_h).status_code == 409  # not stopped
    [row] = audits(sqlite_session, company.id, "rsi_cycle_resumed")
    assert row.agent_id == f"human:{owner.email}" and len(row.result["cycles"]) == 3
    assert started(sqlite_session, company, day=4).status == "running"


def test_a_keep_resets_the_count_toward_the_stop(sqlite_session):
    company = hosting(sqlite_session)
    for day in (1, 2):
        ended_without_keep(sqlite_session, company, day=day)
    kept(sqlite_session, company, day=3)
    ended_without_keep(sqlite_session, company, day=4)
    assert rsi_cycle.consecutive_without_keep(sqlite_session, company.id) == 1


def test_a_revert_is_recorded_on_the_finding(sqlite_session):
    company = hosting(sqlite_session)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C)
    rsi_cycle_steps.record_decision(
        sqlite_session, cycle, {"decision": "revert", "reasons": [{"code": "ci"}]}
    )
    from models import Finding

    row = sqlite_session.get(Finding, cycle.finding_id)
    assert row.status == "reverted"
    assert row.outcomes[-1]["candidate_sha"] == C
    assert row.outcomes[-1]["cycle"] == cycle.cycle_key


def test_a_viewer_reads_the_cycles(sqlite_session, test_client):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    _, viewer_h = _person(sqlite_session, company.id, "Viewer")
    listed = test_client.get(f"/v1/companies/{company.id}/rsi/cycles", headers=viewer_h)
    assert listed.status_code == 200
    body = listed.json()
    assert (
        body["state"]["rsi_enabled"] is True and body["state"]["token_budget"] == 2000
    )
    assert [c["id"] for c in body["cycles"]] == [cycle.id]
    one = test_client.get(
        f"/v1/companies/{company.id}/rsi/cycles/{cycle.id}", headers=viewer_h
    )
    assert one.status_code == 200 and one.json()["candidate_sha"] == C
    other = hosting(sqlite_session)
    missing = test_client.get(
        f"/v1/companies/{other.id}/rsi/cycles/{cycle.id}",
        headers=_person(sqlite_session, other.id, "Viewer")[1],
    )
    assert missing.status_code == 404


def test_the_size_is_read_from_the_daemon_s_diff_of_the_integration(
    sqlite_session, monkeypatch
):
    import asyncio

    from models.integration import SprintIntegration
    from services.release import integration_review

    company = hosting(sqlite_session)
    sprint = factories.sprint(sqlite_session, company, status="active")
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="person",
        status="tests_passed",
        branch="fabrique/integration",
        base="main",
        repository="/work/repo",
        commit="f" * 40,
    )
    sqlite_session.add(row)
    sqlite_session.commit()
    asked = []

    async def read_diff(nats_client, session, integration):
        asked.append(integration.id)
        return diff(files=4, lines_per_file=5)

    monkeypatch.setattr(integration_review, "read_diff", read_diff)
    cycle = started(sqlite_session, company)
    rsi_cycle_steps.record_candidate(sqlite_session, cycle, C, integration_id=row.id)
    asyncio.run(rsi_cycle_steps.read_size(object(), sqlite_session, cycle))
    assert asked == [row.id]
    assert (cycle.diff_files, cycle.diff_lines, cycle.status) == (4, 20, "running")
    # Without an integration of the company, nothing is observed.
    other = started(sqlite_session, hosting(sqlite_session))
    asyncio.run(rsi_cycle_steps.read_size(object(), sqlite_session, other))
    assert other.status == "awaiting_person"
    assert other.reasons[0]["code"] == "size_unobserved"
