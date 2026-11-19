"""A sprint's integration state beside its status (#555, F8).

A sprint reads ``completed`` once its tasks settle, before its integration has
merged the branches and run the suite; ``integration_state`` says where that
stands, derived from the latest ``SprintIntegration`` the backend recorded.
"""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from models import Company, Sprint
from models.integration import INTEGRATION_STATUSES, SprintIntegration
from services.release import integration_state


def _company(session):
    company = Company(company_name="Integration state", git_workflow=True)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, name="Closed", status="completed"):
    sprint = Sprint(company_id=company.id, sprint_name=name, status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _integration(session, sprint, status, at):
    row = SprintIntegration(
        company_id=sprint.company_id,
        sprint_id=sprint.id,
        trigger="sprint_closed",
        requested_by="task_worker",
        status=status,
        branch=f"fabrique/sprint-{sprint.id}",
        base="main",
        created_at=at,
    )
    session.add(row)
    session.commit()
    return row


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("running", "integrating"),
        ("pending", "integrating"),
        ("tests_passed", "integrated"),
        ("untested", "integrated"),
        ("tests_failed", "failed"),
        ("conflict", "failed"),
        ("incomplete", "failed"),
        ("error", "failed"),
        (None, "none"),
    ],
)
def test_each_recorded_status_maps_to_one_state(status, state):
    assert integration_state.state_of(status) == state


def test_every_recorded_status_has_a_state_other_than_none():
    # A status added to the model without a state would read as "none" and
    # let a completed sprint look finished; this keeps the two lists together.
    assert all(integration_state.state_of(s) != "none" for s in INTEGRATION_STATUSES)


def test_list_shows_a_completed_sprint_integrating_until_its_latest_attempt_ends(
    test_client, sqlite_session
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    start = datetime(2026, 11, 1, 12, 0)
    _integration(sqlite_session, sprint, "tests_passed", start)
    latest = _integration(
        sqlite_session, sprint, "running", start + timedelta(minutes=5)
    )

    def listed():
        response = test_client.get("/v1/sprints", params={"company_id": company.id})
        assert response.status_code == 200
        return response.json()[0]

    shown = listed()
    # The status is the one stored; the state reads the latest attempt only.
    assert shown["status"] == "completed"
    assert shown["integration_state"] == "integrating"
    assert shown["integration_status"] == "running"

    latest.status = "tests_failed"
    sqlite_session.add(latest)
    sqlite_session.commit()
    assert listed()["integration_state"] == "failed"

    latest.status = "tests_passed"
    sqlite_session.add(latest)
    sqlite_session.commit()
    assert listed()["integration_state"] == "integrated"


def test_list_reads_each_sprint_its_own_integration(test_client, sqlite_session):
    company = _company(sqlite_session)
    conflicted = _sprint(sqlite_session, company, "Conflicted")
    untouched = _sprint(sqlite_session, company, "No integration")
    _integration(sqlite_session, conflicted, "conflict", datetime(2026, 11, 2))

    response = test_client.get("/v1/sprints", params={"company_id": company.id})

    by_id = {s["id"]: s for s in response.json()}
    assert by_id[conflicted.id]["integration_state"] == "failed"
    assert by_id[untouched.id]["integration_state"] == "none"
    assert by_id[untouched.id]["integration_status"] is None


def test_board_carries_the_state_beside_the_status(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    _integration(sqlite_session, sprint, "pending", datetime(2026, 11, 3))

    response = test_client.get(f"/v1/sprints/{sprint.id}/board")

    assert response.status_code == 200
    shown = response.json()["sprint"]
    assert shown["status"] == "completed"
    assert shown["integration_state"] == "integrating"
    assert shown["integration_status"] == "pending"


# --- the self-hosting harness reads the state instead of its own wait ------------


def _harness():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "self_host" / "self_host.py"
    )
    spec = importlib.util.spec_from_file_location("self_host_integration_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Api:
    """The two answers the harness reads: the sprint list, then the integration."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path.startswith("/v1/sprints?"):
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return [{"id": 4, "status": "completed", "integration_state": state}]
        return {"latest": {"id": 9, "status": "tests_failed"}, "order": []}


def test_the_harness_waits_while_the_sprint_reads_integrating(monkeypatch):
    harness = _harness()
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)
    api = _Api(["integrating", "integrating", "failed"])

    integration, latest = harness._settled_integration(api, 1, 4, timeout=600)

    # Three reads of the sprint, then the integration once, after it settled.
    assert [c.split("?")[0] for c in api.calls] == ["/v1/sprints"] * 3 + [
        "/v1/sprints/4/integration"
    ]
    assert latest == {"id": 9, "status": "tests_failed"}
    assert integration["order"] == []
