"""``metric_matches`` and ``metric_threshold``: a claimed metric against the observed one.

``services/evidence/metric_matches.py``: an agent's stated metric is a claim
(the run's ``claims.metrics``); a metric a declared program printed and the
backend parsed is an observation (``observed_metrics`` on the call's
``program_run``). A forged claim is refuted; no observation is
unverifiable; a ``program_output`` metric never supports a quality claim or
threshold; at the ``high`` tier a ``committed_evaluator`` metric does not
either (the owner's decision 2). A refutation sends the delivery back.
"""

from __future__ import annotations

import pytest
from services.evidence import delivery_gate, metric_matches

RULES = {
    "tolerance": 0.01,
    "precision": 4,
    "thresholds": {"accuracy": {"min": 0.8, "split": "val"}},
}


def _evidence(value=0.7, provenance="committed_evaluator", rules=RULES):
    observed = [
        {
            "name": "accuracy",
            "value": value,
            "split": "val",
            "provenance": provenance,
            "program": "evaluate",
        }
    ]
    run = {
        "program": "evaluate",
        "metrics_declared": rules,
        "observed_metrics": observed,
    }
    return {"tool_calls": [{"tool": "run_program", "program_run": run}]}


def _claims(value):
    return {
        "files": [],
        "metrics": [{"name": "accuracy", "value": value, "split": "val"}],
    }


def test_a_forged_claim_against_the_observed_metric_is_refuted():
    check = metric_matches.check_matches(_claims(0.99), _evidence(0.7), "low")
    assert check["outcome"] == "refuted"
    assert (
        "claimed 0.99" in check["observation"]
        and "observed 0.7" in check["observation"]
    )


def test_a_claim_within_the_tolerance_of_a_committed_evaluator_passes_at_low_and_medium():
    for tier in ("low", "medium"):
        assert (
            metric_matches.check_matches(_claims(0.705), _evidence(0.7), tier)[
                "outcome"
            ]
            == "pass"
        )


def test_at_the_high_tier_a_committed_evaluator_metric_does_not_support_the_claim():
    check = metric_matches.check_matches(_claims(0.7), _evidence(0.7), "high")
    assert (
        check["outcome"] == "unverifiable"
        and "needs platform_scorer" in check["observation"]
    )


def test_a_program_output_metric_never_supports_the_claim():
    check = metric_matches.check_matches(
        _claims(0.7), _evidence(0.7, "program_output"), "low"
    )
    assert (
        check["outcome"] == "unverifiable" and "never supports" in check["observation"]
    )


def test_no_observed_value_is_unverifiable():
    check = metric_matches.check_matches(_claims(0.9), {"tool_calls": []}, "low")
    assert (
        check["outcome"] == "unverifiable"
        and "no observed value" in check["observation"]
    )


def test_no_claim_and_no_threshold_gives_no_check():
    assert metric_matches.checks({"files": []}, {"tool_calls": []}, "low") == []


def test_a_claim_in_the_observed_field_is_never_read_as_an_observation():
    # The agent's report of a metric (in claims) cannot supply the observation.
    claims = _claims(0.99) | {
        "observed_metrics": [{"name": "accuracy", "value": 0.99, "split": "val"}]
    }
    check = metric_matches.check_matches(claims, {}, "low")
    assert check["outcome"] == "unverifiable"


@pytest.mark.parametrize(
    ("value", "provenance", "tier", "outcome"),
    [
        (0.85, "committed_evaluator", "medium", "pass"),
        (0.75, "committed_evaluator", "medium", "refuted"),
        (0.95, "program_output", "low", "unverifiable"),
        (0.95, "committed_evaluator", "high", "unverifiable"),
    ],
)
def test_a_threshold_is_met_only_by_a_metric_that_may_support_quality(
    value, provenance, tier, outcome
):
    check = metric_matches.check_thresholds(_evidence(value, provenance), tier)
    assert check["outcome"] == outcome


def test_a_refuted_metric_sends_the_delivery_back():
    assert metric_matches.CHECK_MATCHES in delivery_gate._GATE_PROBLEMS
    assert metric_matches.CHECK_THRESHOLD in delivery_gate._GATE_PROBLEMS


def test_the_verifier_refutes_a_forged_metric_claim(sqlite_session):
    from uuid import uuid4

    from models import Company
    from models.agent_run import AgentRun
    from services.evidence.verification import verify_agent_run

    company = Company(company_name=f"Metric {uuid4().hex[:6]}")
    sqlite_session.add(company)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{uuid4().hex[:6]}",
        producer="le-fabrique-task-worker",
        claims={"files": ["docs/model.md"]} | _claims(0.99),
        artifact_text="# Artifact: docs/model.md\nAccuracy 0.99\n",
        artifact_digest="d",
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    verification = verify_agent_run(sqlite_session, run, _evidence(0.7))
    outcomes = {c["name"]: c["outcome"] for c in verification.checks}
    assert outcomes["metric_matches"] == "refuted" and verification.verdict == "refuted"


def test_the_record_keeps_stated_metrics_in_the_claims():
    from services.loop.worker import record

    answer = 'Trained.\nclaimed-metric: {"name": "accuracy", "value": 0.99, "split": "val"}\n'
    assert record._claimed_metrics(answer) == {
        "metrics": [{"name": "accuracy", "value": 0.99, "split": "val"}]
    }
    assert record._claimed_metrics("no metrics here") == {}
