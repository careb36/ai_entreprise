"""Who puts a company's standards in force (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §7, §10 T1, T8, T12, §12 decisions 4
and 8).

Negative first: a Manager, an API token and the Manager agent never
activate (T8); the person who imported a draft never activates it while
another Owner exists (four-eyes, whatever the SoD mode); a draft with a
refusal, or a ``command`` standard without an isolated sandbox, is refused
with the reason. Then the single-Owner path, flagged in the access review
until a second Owner confirms it. The exceptions are
``test_standard_exceptions.py``.
"""

from __future__ import annotations

import pytest
from models import AuditLog, CompanyStandardSet
from services.contracts import company_standards as cs
from services.management import manager_policy
from services.platform import api_tokens
from services.platform import company_standards_setting as setting
from sod_helpers import _company, _person, auth_on  # noqa: F401 - an autouse fixture
from sqlmodel import select

SEC = b"""format = 1
[[standard]]
id = "ACME-SEC-1"
title = "No env file"
rule = "Never deliver a .env file."
binds = ["*"]
kind = "forbid_path"
files = ["**/.env"]
severity = "blocking"
owner = "SecOps"
"""
RUN = (
    SEC
    + b"""
[[standard]]
id = "ACME-RUN-1"
title = "Lint"
rule = "The linter passes."
binds = ["Developer"]
kind = "command"
command = "make lint"
guards = ["Makefile"]
severity = "blocking"
owner = "Architect"
"""
)


def _draft(session, company_id, importer=None, raw=SEC, sandbox_seen="", refusals=None):
    parsed = cs.parse(raw)
    return setting.create_draft(
        session,
        company_id,
        raw=raw,
        source_commit="a" * 40,
        source_base="main",
        parsed=parsed.as_dict(),
        guard_hashes={},
        baselines={},
        import_refusals=refusals or [],
        dry_run={"fails_on_source": []},
        sandbox_seen=sandbox_seen,
        by=importer.email if importer else "agent:ScrumMaster",
        user_id=importer.id if importer else None,
    )


def _url(company_id, rest=""):
    return f"/v1/companies/{company_id}/standards{rest}"


def _actions(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


# -- T8: only a signed-in human Owner ------------------------------------------


def test_a_manager_a_token_and_anonymous_never_activate(sqlite_session, test_client):
    company = _company(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    _, manager = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    draft = _draft(sqlite_session, company.id)
    url = _url(company.id, f"/versions/{draft.version}/activate")
    for headers in (manager, {"Authorization": f"Bearer {raw}"}):
        assert test_client.post(url, headers=headers).status_code == 403
        assert (
            test_client.post(
                _url(company.id, "/deactivate"), headers=headers
            ).status_code
            == 403
        )
    assert test_client.post(url).status_code == 401
    assert setting.in_force(sqlite_session, company.id) is None


def test_no_policy_hands_change_standards_to_the_manager_agent():
    assert "change_standards" in manager_policy.ALWAYS_ESCALATE
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"rules": ["change_standards"]})


# -- Four-eyes ------------------------------------------------------------------


def test_the_importer_does_not_activate_while_another_owner_exists(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    alice, alice_h = _person(sqlite_session, company.id, "Owner")
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, importer=alice)
    url = _url(company.id, f"/versions/{draft.version}/activate")
    refused = test_client.post(
        url, json={"single_owner_reason": "I am in a hurry today."}, headers=alice_h
    )
    assert refused.status_code == 403 and "four_eyes" in refused.json()["detail"]
    assert _actions(sqlite_session, company.id, "sod_refused")
    done = test_client.post(url, headers=bob_h)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "active" and done.json()["single_owner"] is False
    audit = _actions(sqlite_session, company.id, "company_standards_activated")[0]
    assert audit.result["to"] == {
        "version": 1,
        "sha256": draft.sha256,
        "source_commit": "a" * 40,
    }
    assert audit.result["from"] is None


def test_a_manager_imports_and_an_owner_activates(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    manager, _ = _person(sqlite_session, company.id, "Manager")
    draft = _draft(sqlite_session, company.id, importer=manager)
    assert (
        test_client.post(
            _url(company.id, f"/versions/{draft.version}/activate"), headers=owner_h
        ).status_code
        == 200
    )


def test_a_new_version_supersedes_the_one_in_force(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    first = _draft(sqlite_session, company.id)
    second = _draft(sqlite_session, company.id, raw=SEC + b"\n")
    for row in (first, second):
        assert (
            test_client.post(
                _url(company.id, f"/versions/{row.version}/activate"), headers=owner_h
            ).status_code
            == 200
        )
    sqlite_session.refresh(first)
    assert (
        first.status == "superseded"
        and setting.in_force(sqlite_session, company.id).version == 2
    )
    audit = _actions(sqlite_session, company.id, "company_standards_activated")[-1]
    assert audit.result["from"]["version"] == 1 and audit.result["to"]["version"] == 2
    again = test_client.post(_url(company.id, "/versions/1/activate"), headers=owner_h)
    assert again.status_code == 409


# -- The single Owner ------------------------------------------------------------


def test_the_only_owner_activates_their_import_with_a_reason_and_is_flagged(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    alice, alice_h = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, importer=alice)
    url = _url(company.id, f"/versions/{draft.version}/activate")
    assert test_client.post(url, headers=alice_h).status_code == 422
    done = test_client.post(
        url,
        json={"single_owner_reason": "The company has one Owner for now."},
        headers=alice_h,
    )
    assert done.status_code == 200 and done.json()["flagged"] is True

    review = test_client.get(
        f"/v1/companies/{company.id}/access-review", headers=alice_h
    ).json()
    items = (
        review["listing"]["standards"] if "listing" in review else review["standards"]
    )
    assert [(i["kind"], i["flagged"]) for i in items] == [
        ("standards_single_owner", True)
    ]


def test_a_second_owner_confirms_a_single_owner_activation_in_the_review(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    alice, alice_h = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, importer=alice)
    test_client.post(
        _url(company.id, f"/versions/{draft.version}/activate"),
        json={"single_owner_reason": "The company has one Owner for now."},
        headers=alice_h,
    )
    _, bob_h = _person(sqlite_session, company.id, "Owner")
    listing = test_client.get(
        f"/v1/companies/{company.id}/access-review", headers=bob_h
    ).json()
    listing = listing.get("listing", listing)
    items = [
        *listing["members"],
        *listing["tokens"],
        *listing["sso"],
        *listing["standards"],
    ]
    decisions = [
        {"kind": i["kind"], "id": i["id"], "decision": "confirm"} for i in items
    ]
    done = test_client.post(
        f"/v1/companies/{company.id}/access-review",
        json={"decisions": decisions},
        headers=bob_h,
    )
    assert done.status_code in (200, 201), done.text
    sqlite_session.refresh(draft)
    assert (
        draft.reviewed_in is not None and setting.serialize(draft)["flagged"] is False
    )


def test_removing_a_flagged_set_in_the_review_deactivates_it(sqlite_session):
    from services.platform import access_review

    company = _company(sqlite_session)
    alice, _ = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, importer=alice)
    setting.activate(
        sqlite_session,
        company.id,
        draft.version,
        alice,
        company_sandbox="host",
        single_owner_reason="The company has one Owner for now.",
    )
    current = access_review.listing(sqlite_session, company.id)
    decisions = [
        {"kind": i["kind"], "id": i["id"], "decision": "confirm"}
        for i in current["members"] + current["tokens"] + current["sso"]
    ]
    decisions.append(
        {"kind": "standards_single_owner", "id": draft.id, "decision": "remove"}
    )
    access_review.complete(sqlite_session, company.id, alice, decisions, None)
    assert setting.in_force(sqlite_session, company.id) is None


def test_a_recent_owner_change_closes_the_single_owner_path(
    sqlite_session, monkeypatch
):
    from services.platform import sso_group_approvals

    company = _company(sqlite_session)
    alice, _ = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, importer=alice)
    monkeypatch.setattr(sso_group_approvals, "recent_owner_change", lambda *a: True)
    with pytest.raises(setting.StandardsRefused) as raised:
        setting.activate(
            sqlite_session,
            company.id,
            draft.version,
            alice,
            company_sandbox="host",
            single_owner_reason="The company has one Owner for now.",
        )
    assert raised.value.status == 409 and "recent_owner_change" in raised.value.detail


# -- What blocks activation ------------------------------------------------------


def test_a_draft_with_a_refusal_is_not_activated(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    draft = _draft(
        sqlite_session, company.id, refusals=[{"code": "guard_missing", "message": "x"}]
    )
    refused = test_client.post(
        _url(company.id, f"/versions/{draft.version}/activate"), headers=owner_h
    )
    assert (
        refused.status_code == 409
        and refused.json()["detail"]["refusals"][0]["code"] == "guard_missing"
    )


@pytest.mark.parametrize(
    "seen,asked,ok",
    [
        ("", "host", False),
        ("", None, False),
        ("container", "host", True),
        ("bwrap", None, True),
        ("", "container", True),
    ],
)
def test_a_command_standard_runs_only_isolated(
    sqlite_session, test_client, seen, asked, ok
):
    company = _company(sqlite_session)
    company.delivery_sandbox = asked
    sqlite_session.add(company)
    sqlite_session.commit()
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    draft = _draft(sqlite_session, company.id, raw=RUN, sandbox_seen=seen)
    answer = test_client.post(
        _url(company.id, f"/versions/{draft.version}/activate"), headers=owner_h
    )
    if ok:
        assert answer.status_code == 200, answer.text
    else:
        assert answer.status_code == 409 and answer.json()["detail"].startswith(
            "command_on_host"
        )
        assert "'host'" in answer.json()["detail"]


def test_without_a_command_standard_the_sandbox_does_not_matter(sqlite_session):
    row = CompanyStandardSet(parsed=cs.parse(SEC).as_dict(), sandbox_seen="")
    assert setting.sandbox_refusal(row, "host") is None


# -- Deactivate, reject, read -----------------------------------------------------


def test_deactivate_and_reject_are_audited(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, company.id, "Owner")
    _, viewer_h = _person(sqlite_session, company.id, "Viewer")
    first, second = (
        _draft(sqlite_session, company.id),
        _draft(sqlite_session, company.id),
    )
    test_client.post(
        _url(company.id, f"/versions/{first.version}/activate"), headers=owner_h
    )
    shown = test_client.get(_url(company.id), headers=viewer_h).json()
    assert shown["in_force"]["version"] == 1 and shown["catalogue"][0]["id"].startswith(
        "STD-"
    )
    assert (
        test_client.post(
            _url(company.id, f"/versions/{second.version}/reject"), headers=owner_h
        ).json()["status"]
        == "rejected"
    )
    assert (
        test_client.post(_url(company.id, "/deactivate"), headers=owner_h).json()[
            "deactivated"
        ]["version"]
        == 1
    )
    assert (
        test_client.get(_url(company.id), headers=viewer_h).json()["in_force"] is None
    )
    history = test_client.get(_url(company.id, "/versions"), headers=viewer_h).json()[
        "versions"
    ]
    assert [(v["version"], v["status"]) for v in history] == [
        (2, "rejected"),
        (1, "superseded"),
    ]
    assert _actions(sqlite_session, company.id, "company_standards_rejected")
    assert _actions(sqlite_session, company.id, "company_standards_deactivated")


def test_a_manager_imports_through_the_route(sqlite_session, test_client, monkeypatch):
    from services.planning import company_standards_import as importer

    company = _company(sqlite_session)
    _, manager_h = _person(sqlite_session, company.id, "Manager")
    _, viewer_h = _person(sqlite_session, company.id, "Viewer")
    seen = {}

    async def fake(session, nats, company_row, *, by, user_id, proposed_by=None):
        seen.update(by=by, user_id=user_id)
        return _draft(session, company_row.id)

    monkeypatch.setattr(importer, "import_draft", fake)
    assert (
        test_client.post(_url(company.id, "/import"), headers=viewer_h).status_code
        == 403
    )
    answer = test_client.post(_url(company.id, "/import"), headers=manager_h)
    assert answer.status_code == 202 and answer.json()["status"] == "draft"
    assert seen["by"].startswith("manager-")


def test_the_route_answers_409_without_the_daemon(sqlite_session, test_client):
    company = _company(sqlite_session)
    _, manager_h = _person(sqlite_session, company.id, "Manager")
    answer = test_client.post(_url(company.id, "/import"), headers=manager_h)
    assert answer.status_code == 409
    assert sqlite_session.exec(select(CompanyStandardSet)).all() == []
