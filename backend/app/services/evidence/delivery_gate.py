"""The gate a delivery passes before QA sees it, read from the Verifier's record.

``gate`` says why the task's latest delivery must not reach QA (a refuted
or unconfirmed delivery, failed tests, a failed or edited acceptance test, a
violated standard), or that it is queued for an offline daemon; ``risk`` is
the Verifier's risk tier on it; ``latest_verification`` is the record both
read. Everything here is an observation the Verifier made, never the agent's
word.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import AgentRun, Verification
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence import app_check, criteria_tests, standards

_TASK_WORKER = "le-fabrique-task-worker"
DELIVERY_QUEUED = "delivery queued"


# The checks whose refutation sends a delivery back, and the problem named.
_GATE_PROBLEMS = {
    "files_written": "delivery refuted",
    "tests_run": "tests failed",
    criteria_tests.CHECK_CRITERIA: "acceptance tests failed",
    criteria_tests.CHECK_UNTOUCHED: "acceptance test edited",
    standards.CHECK_NAME: "standards violated",
    "secops": "security scan failed",
    "grounded": "invented dependency or import",
    app_check.CHECK_INSTALL: "dependency install failed",
    app_check.CHECK_APP: "application failed to start",
    app_check.CHECK_E2E: "end-to-end tests failed",
}

# The problems the code itself caused: the Developer fixes the code.
CODE_PROBLEMS = frozenset(
    {
        "tests failed",
        "dependency install failed",
        "application failed to start",
        "end-to-end tests failed",
        "invented dependency or import",
    }
)


def gate(engine: Engine, company_id: int, item_id: int) -> tuple[str, str] | None:
    """
    ``(problem, observation)`` when the task's latest delivery must not reach QA.

    Gates on a refuted delivery (files missing, rejected or different on disk),
    an unconfirmed one (daemon silent) and, when delivery tests are enabled, a
    test run that failed.  A delivery queued for an offline daemon returns
    ``DELIVERY_QUEUED``: the task waits, neither reviewed nor reworked, until
    the daemon reports what it wrote.  Other checks — an unevidenced "tests pass" claim, a
    test run that could not happen — are left to the evidence page, so an
    honest claim never forces a rework.

    Scoped to the task's company: ``POST /v1/agent_runs`` lets any company
    ingest a run with any producer and reference, so a run is only trusted
    when it belongs to the same company as the task.
    """
    verification = latest_verification(engine, company_id, item_id)
    if verification is None:
        return None
    if (verification.evidence or {}).get("delivery_queued"):
        return DELIVERY_QUEUED, (verification.evidence or {}).get("reason") or ""
    checks = list(verification.checks or [])
    # An edited acceptance test is the reason to give first: the tests that
    # then fail are QA's, restored.
    checks.sort(key=lambda c: c.get("name") != criteria_tests.CHECK_UNTOUCHED)
    for check in checks:
        name, outcome = check.get("name"), check.get("outcome")
        if name == "files_written" and outcome == VERDICT_UNVERIFIABLE:
            return "delivery unconfirmed", check.get("observation", "")
        if outcome == VERDICT_REFUTED and name in _GATE_PROBLEMS:
            return _GATE_PROBLEMS[name], check.get("observation", "")
    return None


def risk(engine: Engine, company_id: int, item_id: int) -> dict[str, Any] | None:
    """The Verifier's ``risk_tier`` check on the task's latest delivery, if any."""
    verification = latest_verification(engine, company_id, item_id)
    if verification is None:
        return None
    for check in verification.checks or []:
        if check.get("name") == "risk_tier":
            return check
    return None


def latest_verification(
    engine: Engine, company_id: int, item_id: int
) -> Verification | None:
    """The latest verification of the task worker's run for this task, in its company."""
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.external_ref == f"task-{item_id}",
                AgentRun.producer == _TASK_WORKER,
            )
            .order_by(AgentRun.version.desc())
        ).first()
        if run is None:
            return None
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())
        ).first()
    return verification
