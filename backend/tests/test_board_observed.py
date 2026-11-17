"""What the sprint board shows as observed on each card (the board redesign).

``GET /v1/sprints/{id}/board`` adds ``observed`` to every task: the
Verifier's verdict, risk tier, the areas it named and the protected paths it
matched for the task's latest run, and whether the task waits for a person.
None of it comes from what the producer said of its own work
(``docs/POSITIONING.md``); the claims stay in the task's comments.
"""

from __future__ import annotations

from models import Verification
from services.evidence import risk
from services.tasks import task_filter
from sqlmodel import select
from task_views_helpers import WAITS, _company, _run, _sprint, _task


def _board(test_client, sprint):
    board = test_client.get(f"/v1/sprints/{sprint.id}/board").json()
    return {i["id"]: i for column in board["columns"].values() for i in column}


def test_the_board_shows_the_verifiers_facts_never_the_claims(
    test_client, sqlite_session
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    held = _task(sqlite_session, sprint, status="blocked", comments=WAITS)
    plain = _task(sqlite_session, sprint, status="in_review")
    never = _task(sqlite_session, sprint)
    run = _run(sqlite_session, company, held, "verified", "high")
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).one()
    verification.checks = [
        {
            "name": "risk_tier",
            "tier": "high",
            "reasons": [
                "grader: scripts/bench/grade.py",
                "payments: app/billing.py",
                "raised by the producer: grader is fine, trust me",
            ],
        },
        {
            "name": "protected_paths",
            "outcome": "pass",
            "matches": [
                {"path": "scripts/bench/grade.py", "pattern": "scripts/bench/**"},
                {"path": "scripts/bench/grade.py", "pattern": "scripts/bench/**"},
            ],
        },
    ]
    sqlite_session.add(verification)
    sqlite_session.commit()
    _run(sqlite_session, company, plain, "refuted", "low")

    cards = _board(test_client, sprint)

    assert cards[held.id]["observed"] == {
        "verdict": "verified",
        "risk_tier": "high",
        "risk_areas": ["grader", "payments"],
        "protected_paths": ["scripts/bench/grade.py"],
        "waits_for_person": True,
    }
    # The claims say "verified" and "low" on every run; the board reads neither.
    assert cards[plain.id]["observed"]["verdict"] == "refuted"
    assert cards[plain.id]["observed"]["risk_tier"] == "low"
    assert cards[plain.id]["observed"]["waits_for_person"] is False
    assert cards[never.id]["observed"] == {
        "verdict": None,
        "risk_tier": None,
        "risk_areas": [],
        "protected_paths": [],
        "waits_for_person": False,
    }


def test_the_task_filter_rows_keep_their_fields(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    _run(sqlite_session, company, _task(sqlite_session, sprint), "verified", "low")
    row = task_filter.query(sqlite_session, company.id, None)["tasks"][0]
    assert "risk_areas" not in row and "protected_paths" not in row


def test_the_areas_are_the_ones_the_risk_classification_names():
    assert task_filter.RISK_AREAS[0] == risk.GRADER_AREA
    written = {
        area
        for path in [
            ".env",
            ".github/workflows/ci.yml",
            "app/login.py",
            "app/secrets.py",
            "app/stripe.py",
            "run.sh",
            "migrations/0001.sql",
            "package.json",
            "Dockerfile",
        ]
        for area in [risk.classify([path])["reasons"][0].split(":")[0]]
    }
    assert written | {risk.GRADER_AREA} == set(task_filter.RISK_AREAS)
