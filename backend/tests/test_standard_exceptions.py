"""Time-boxed exceptions to a standard (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §12 decision 1; ISO/IEC 27001:2022
clause 6.1.3 and A.5.36).

A company never relaxes a catalogue standard; an exception is the only way
around one, catalogue or company: an Owner requests it with a reason, a
compensating control and at most 90 days; a second Owner approves it (the
requester never does); the only Owner may confirm alone, flagged in the
access review; it lapses on its own, and the access review may end it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from models import CompanyStandardException
from services.platform import standard_exceptions as exceptions
from sod_helpers import _company, _person, auth_on  # noqa: F401 - an autouse fixture
from test_company_standards_activation import _actions, _draft, _url


def _ask(client, company_id, headers, **body):
    payload = {
        "standard_id": "STD-SEC-1",
        "reason": "A vendored fixture trips the scanner.",
        "compensating_control": "A person reviews each delivery by hand.",
        "days": 30,
        **body,
    }
    return client.post(_url(company_id, "/exceptions"), json=payload, headers=headers)


def test_an_exception_needs_a_second_owner_and_lapses_on_its_own(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    _, alice_h = _person(sqlite_session, company.id, "Owner")
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    asked = _ask(test_client, company.id, alice_h)
    assert asked.status_code == 201 and asked.json()["state"] == "pending"
    eid = asked.json()["id"]
    own = test_client.post(
        _url(company.id, f"/exceptions/{eid}/approve"), headers=alice_h
    )
    assert own.status_code == 403 and "four_eyes" in own.json()["detail"]
    alone = test_client.post(
        _url(company.id, f"/exceptions/{eid}/confirm"),
        json={"reason": "Nobody else is around."},
        headers=alice_h,
    )
    assert alone.status_code == 409 and "not_single_owner" in alone.json()["detail"]
    approved = test_client.post(
        _url(company.id, f"/exceptions/{eid}/approve"), headers=bob_h
    ).json()
    assert approved["state"] == "approved" and approved["in_force"] is True
    expires = datetime.fromisoformat(approved["expires_at"])
    assert (
        timedelta(days=29)
        < expires - datetime.now(UTC).replace(tzinfo=None)
        <= timedelta(days=30)
    )
    row = sqlite_session.get(CompanyStandardException, eid)
    assert [r.id for r in exceptions.in_force(sqlite_session, company.id)] == [eid]
    assert exceptions.in_force(sqlite_session, company.id, now=row.expires_at) == []
    assert _actions(sqlite_session, company.id, "standard_exception_approved")


@pytest.mark.parametrize(
    "body,status",
    [
        ({"days": 91}, 422),
        ({"days": 0}, 422),
        ({"standard_id": "ACME-NOPE-1"}, 422),
        ({"reason": "short"}, 422),
    ],
)
def test_an_exception_beyond_its_bounds_is_refused(
    sqlite_session, test_client, body, status
):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    assert _ask(test_client, company.id, owner_h, **body).status_code == status


def test_a_manager_never_requests_or_approves_an_exception(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, manager_h = _person(sqlite_session, company.id, "Manager")
    assert _ask(test_client, company.id, manager_h).status_code == 403


def test_the_only_owner_confirms_alone_and_the_review_lists_it_flagged(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    _, alice_h = _person(sqlite_session, company.id, "Owner")
    eid = _ask(test_client, company.id, alice_h).json()["id"]
    done = test_client.post(
        _url(company.id, f"/exceptions/{eid}/confirm"),
        json={"reason": "The company has one Owner."},
        headers=alice_h,
    )
    assert done.status_code == 200 and done.json()["flagged"] is True
    listing = test_client.get(
        f"/v1/companies/{company.id}/access-review", headers=alice_h
    ).json()
    items = listing.get("listing", listing)["standards"]
    assert [(i["kind"], i["standard_id"], i["flagged"]) for i in items] == [
        ("standard_exception", "STD-SEC-1", True)
    ]


def test_revoking_ends_an_exception_and_the_review_may_revoke_it(
    sqlite_session, test_client
):
    from services.platform import access_review

    company = _company(sqlite_session)
    alice, alice_h = _person(sqlite_session, company.id, "Owner")
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    first = _ask(test_client, company.id, alice_h).json()["id"]
    second = _ask(test_client, company.id, alice_h, standard_id="STD-QA-1").json()["id"]
    for eid in (first, second):
        test_client.post(_url(company.id, f"/exceptions/{eid}/approve"), headers=bob_h)
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{first}/revoke"), headers=alice_h
        ).json()["state"]
        == "revoked"
    )
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{first}/revoke"), headers=alice_h
        ).status_code
        == 409
    )
    current = access_review.listing(sqlite_session, company.id)
    decisions = [
        {"kind": i["kind"], "id": i["id"], "decision": "confirm"}
        for i in current["members"] + current["tokens"] + current["sso"]
    ]
    decisions.append({"kind": "standard_exception", "id": second, "decision": "remove"})
    access_review.complete(sqlite_session, company.id, alice, decisions, None)
    assert exceptions.in_force(sqlite_session, company.id) == []


def test_a_request_is_withdrawn_by_its_requester_and_rejected_by_another(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    _, alice_h = _person(sqlite_session, company.id, "Owner")
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    a = _ask(test_client, company.id, alice_h).json()["id"]
    b = _ask(test_client, company.id, alice_h).json()["id"]
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{a}/reject"), headers=alice_h
        ).json()["state"]
        == "withdrawn"
    )
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{b}/reject"), headers=bob_h
        ).json()["state"]
        == "rejected"
    )
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{b}/approve"), headers=bob_h
        ).status_code
        == 409
    )


def test_an_undecided_request_expires_after_a_week(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, alice_h = _person(sqlite_session, company.id, "Owner")
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    eid = _ask(test_client, company.id, alice_h).json()["id"]
    row = sqlite_session.get(CompanyStandardException, eid)
    row.requested_at -= timedelta(days=exceptions.PENDING_DAYS)
    sqlite_session.add(row)
    sqlite_session.commit()
    assert (
        test_client.post(
            _url(company.id, f"/exceptions/{eid}/approve"), headers=bob_h
        ).status_code
        == 409
    )
    sqlite_session.refresh(row)
    assert row.state == "expired"


def test_an_exception_may_name_a_company_standard_in_force(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    assert (
        _ask(test_client, company.id, owner_h, standard_id="ACME-SEC-1").status_code
        == 422
    )
    draft = _draft(sqlite_session, company.id)
    test_client.post(
        _url(company.id, f"/versions/{draft.version}/activate"), headers=owner_h
    )
    assert (
        _ask(test_client, company.id, owner_h, standard_id="ACME-SEC-1").status_code
        == 201
    )
