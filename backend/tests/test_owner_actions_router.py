"""Owner actions from the app (issue #701, ``docs/design/owner-actions.md``).

Only a signed-in Owner may see, preview, start or cancel an action: a
Manager and an Owner's API token get 403. The checkout is one folder name,
never a path. A start must echo the confirmation's digest, one run of an
action runs at a time, a run is cancelled on request, and start, cancel and
finish are rows of the company's hash chain. The inbox lists the actions for
an Owner. No job is launched here (``owner_action_runner.launch`` is
replaced); the job has its own tests (``test_owner_action_runner.py``).
"""

import secrets

import audit_signing
import pytest
from audit_chain import verify_chain
from models import AuditLog, Company, CompanyMember, OwnerActionRun, User
from services.management import owner_action_runner, owner_actions, self_host_snapshot
from services.platform import api_tokens
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401

NAME = owner_actions.SELF_HOST
PARAMS = {
    "checkout_name": "lf-701",
    "scope": "S",
    "timeout_seconds": 600,
    "token_budget": 50000,
}


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


@pytest.fixture
def launched(monkeypatch, tmp_path):
    """The job is not run: each launch is recorded instead."""
    calls = []
    monkeypatch.setattr(
        owner_action_runner,
        "launch",
        lambda app, nats, run, engine: calls.append(run.id),
    )
    monkeypatch.setattr(self_host_snapshot, "harness_dir", lambda: tmp_path)
    return calls


def _owner_token(session, company_id):
    """An Owner's API token, with the highest role a token may have."""
    user = User(
        email=f"owner-{secrets.token_hex(4)}@example.com",
        full_name="O",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.add(
        CompanyMember(company_id=company_id, user_id=user.id, human_role="Owner")
    )
    session.commit()
    _, raw = api_tokens.create_token(
        session, company_id, "ci", role="Manager", created_by_user_id=user.id
    )
    return {"Authorization": f"Bearer {raw}"}


def _company(session):
    company = Company(company_name=f"Owner actions {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _preview(client, company_id, headers, params=PARAMS):
    response = client.post(
        f"/v1/owner_actions/{NAME}/preview",
        json={"company_id": company_id, "params": params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start(client, company_id, headers, params=PARAMS, confirmation=None):
    if confirmation is None:
        confirmation = _preview(client, company_id, headers, params)["confirmation"]
    return client.post(
        f"/v1/owner_actions/{NAME}/runs",
        json={"company_id": company_id, "params": params, "confirmation": confirmation},
        headers=headers,
    )


def test_only_a_signed_in_owner_sees_or_starts_an_action(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,
):
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    manager = _member(sqlite_session, company.id, "Manager")
    token = _owner_token(sqlite_session, company.id)
    body = {"company_id": company.id, "params": PARAMS}

    assert (
        test_client.get(f"/v1/owner_actions?company_id={company.id}").status_code == 401
    )
    for headers in (manager, token):
        assert (
            test_client.get(
                f"/v1/owner_actions?company_id={company.id}", headers=headers
            ).status_code
            == 403
        )
        assert (
            test_client.post(
                f"/v1/owner_actions/{NAME}/preview", json=body, headers=headers
            ).status_code
            == 403
        )
        start = test_client.post(
            f"/v1/owner_actions/{NAME}/runs",
            json={**body, "confirmation": "x"},
            headers=headers,
        )
        assert start.status_code == 403
    assert launched == []

    listing = test_client.get(
        f"/v1/owner_actions?company_id={company.id}", headers=owner
    )
    assert listing.status_code == 200
    (entry,) = listing.json()["actions"]
    assert (
        entry["name"] == NAME and entry["issue"] == 701 and entry["available"] is True
    )
    assert set(entry["params"]) == {
        "checkout_name",
        "scope",
        "timeout_seconds",
        "token_budget",
        "urgent",
    }
    assert "sprint_id" in entry["records"] and "agent_run_ids" in entry["records"]


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/home/someone/la-fabrique",
        "a/b",
        "..",
        ".",
        "",
        "Upper",
        "x" * 64,
        "a\\b",
    ],
)
def test_the_checkout_is_one_folder_name_never_a_path(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,
    name,
):
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    response = test_client.post(
        f"/v1/owner_actions/{NAME}/preview",
        json={"company_id": company.id, "params": {**PARAMS, "checkout_name": name}},
        headers=owner,
    )
    assert response.status_code == 422
    started = _start(
        test_client, company.id, owner, {**PARAMS, "checkout_name": name}, "digest"
    )
    assert started.status_code == 422
    assert launched == []


def test_unknown_parameters_and_out_of_range_values_are_refused(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,
):
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    for params in (
        {**PARAMS, "command": "rm -rf /"},
        {**PARAMS, "token_budget": 10**9},
        {**PARAMS, "timeout_seconds": 5},
        {**PARAMS, "scope": "XL"},
    ):
        response = test_client.post(
            f"/v1/owner_actions/{NAME}/preview",
            json={"company_id": company.id, "params": params},
            headers=owner,
        )
        assert response.status_code == 422, params


def test_the_confirmation_shows_cost_and_effect_and_must_be_echoed(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,
):
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    shown = _preview(test_client, company.id, owner, {**PARAMS, "urgent": True})
    assert shown["cost"]["token_budget"] == 50000
    assert shown["cost"]["max_seconds"] == 600 + owner_actions.CHECKOUT_SECONDS
    assert [e["key"] for e in shown["effect"]] == [
        "copy",
        "company",
        "sprint",
        "wait",
        "sandbox",
        "urgent",
    ]
    assert shown["effect"][0]["values"] == {"name": "lf-701"}
    # The digest of other parameters does not start this run (O2).
    other = _preview(test_client, company.id, owner)["confirmation"]
    refused = _start(test_client, company.id, owner, {**PARAMS, "urgent": True}, other)
    assert refused.status_code == 409
    assert launched == []
    started = _start(
        test_client,
        company.id,
        owner,
        {**PARAMS, "urgent": True},
        shown["confirmation"],
    )
    assert started.status_code == 202, started.text
    assert started.json()["params"]["urgent"] is True
    assert launched == [started.json()["id"]]


def test_one_run_at_a_time_then_cancel_and_the_chain_holds_each_step(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,
):
    company = _company(sqlite_session)
    other_company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    other_owner = _member(sqlite_session, other_company.id, "Owner")
    first = _start(test_client, company.id, owner)
    assert first.status_code == 202
    run_id = first.json()["id"]
    # A second run, even from another company, waits for the first (the daemon is shared).
    second = _start(test_client, other_company.id, other_owner)
    assert second.status_code == 409
    assert second.json()["detail"]["running_run_id"] == run_id
    assert launched == [run_id]
    # Another company's Owner cannot read or cancel it.
    assert (
        test_client.get(
            f"/v1/owner_actions/{NAME}/runs/{run_id}?company_id={other_company.id}",
            headers=other_owner,
        ).status_code
        == 404
    )

    cancel = test_client.post(
        f"/v1/owner_actions/{NAME}/runs/{run_id}/cancel",
        json={"company_id": company.id},
        headers=owner,
    )
    assert cancel.status_code == 200 and cancel.json()["cancel_requested_at"]
    assert owner_actions.cancel_requested(sqlite_session, run_id)
    owner_actions.finish(
        sqlite_session, run_id, "cancelled", None, "cancelled", {"sprint_id": None}
    )
    again = test_client.post(
        f"/v1/owner_actions/{NAME}/runs/{run_id}/cancel",
        json={"company_id": company.id},
        headers=owner,
    )
    assert again.status_code == 409
    view = test_client.get(
        f"/v1/owner_actions/{NAME}/runs/{run_id}?company_id={company.id}", headers=owner
    ).json()
    assert view["status"] == "cancelled" and view["observations"]["finished_at"]

    rows = sqlite_session.exec(
        select(AuditLog)
        .where(AuditLog.interaction_id == f"owner_action:{run_id}")
        .order_by(AuditLog.id)
    ).all()
    assert [r.action for r in rows] == [
        "owner_action_started",
        "owner_action_cancel_requested",
        "owner_action_finished",
    ]
    assert all(
        r.agent_id.startswith("human:") and r.company_id == company.id for r in rows
    )
    assert rows[-1].result["status"] == "cancelled"
    assert verify_chain(sqlite_session, company.id)["status"] == "intact"
    # Now the next run may start.
    assert _start(test_client, other_company.id, other_owner).status_code == 202


def test_a_missing_harness_makes_the_action_unavailable(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(self_host_snapshot, "harness_dir", lambda: None)
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    (entry,) = test_client.get(
        f"/v1/owner_actions?company_id={company.id}", headers=owner
    ).json()["actions"]
    assert (entry["available"], entry["reason"]) == (False, "harness_not_found")
    assert _start(test_client, company.id, owner).status_code == 503
    assert sqlite_session.exec(select(OwnerActionRun)).all() == []


def test_the_inbox_lists_the_actions_for_an_owner_only(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    viewer = _member(sqlite_session, company.id, "Viewer")
    mine = test_client.get(f"/v1/inbox?company_id={company.id}", headers=owner).json()
    assert mine["owner_actions"] == [{"name": NAME, "issue": 701}]
    assert (
        test_client.get(f"/v1/inbox?company_id={company.id}", headers=viewer).json()[
            "owner_actions"
        ]
        == []
    )
    assert (
        owner_actions.matching(
            "run scripts/self_host/self_host.py --container-mode once"
        )
        == NAME
    )
    assert owner_actions.matching("fix the login form") is None
