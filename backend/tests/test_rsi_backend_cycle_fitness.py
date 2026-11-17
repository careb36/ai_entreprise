"""The backend's cycle records a real ``fitness.decide`` result
(``services/management/rsi_cycle_steps.record_decision``; review of PR #632):

- a decision about another B or C is refused: a keep for C1 never authorizes
  C2, and nothing is recorded;
- the metrics ``fitness.decide`` returns, a list of rows, are kept whole;
- the test helpers' decision has the real result's shape.
"""

from __future__ import annotations

import pytest
from fitness_helpers import decide, observations
from rsi_cycle_helpers import (
    B,
    C,
    hosting,
    measured,
    refusal_codes,
    result,
    rsi_on,  # noqa: F401 - a fixture
)
from services.management import rsi_cycle, rsi_cycle_steps, rsi_cycle_view
from sod_helpers import auth_on  # noqa: F401 - an autouse fixture

pytestmark = pytest.mark.usefixtures("rsi_on")


def _real_keep():
    real = decide(observations())
    assert real["decision"] == "keep", real["reasons"]
    assert (real["baseline"], real["candidate"]) == (B, C)
    return real


def test_a_keep_for_another_c_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)
    rsi_cycle_steps.record_candidate(
        sqlite_session, cycle, "e" * 40, integration_id=cycle.integration_id
    )
    with pytest.raises(rsi_cycle.CycleRefused) as caught:
        rsi_cycle_steps.record_decision(sqlite_session, cycle, _real_keep())
    assert caught.value.code == "decision_not_this_cycle"
    sqlite_session.refresh(cycle)
    assert cycle.status == "running" and cycle.decision is None
    assert "decision_not_this_cycle" in refusal_codes(sqlite_session, company.id)


def test_a_decision_for_another_b_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)
    other = {**_real_keep(), "baseline": "a" * 40}
    with pytest.raises(rsi_cycle.CycleRefused):
        rsi_cycle_steps.record_decision(sqlite_session, cycle, other)


def test_a_real_keep_is_recorded_with_its_metric_rows(sqlite_session):
    company = hosting(sqlite_session)
    cycle = measured(sqlite_session, company)
    real = _real_keep()
    assert isinstance(real["metrics"], list) and real["metrics"]
    rsi_cycle_steps.record_decision(sqlite_session, cycle, real)
    assert cycle.status == "awaiting_merge" and cycle.decision == "keep"
    assert cycle.metrics == real["metrics"]
    assert rsi_cycle_view.serialize(cycle)["metrics"] == real["metrics"]


def test_the_helpers_decision_has_the_real_shape():
    real, helper = _real_keep(), result()
    assert set(helper) == set(real)
    assert isinstance(helper["metrics"], list)
