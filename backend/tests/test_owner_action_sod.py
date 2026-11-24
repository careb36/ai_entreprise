"""``self_host_container_run`` and segregation of duties (issue #701,
``docs/design/owner-actions.md``).

The run drafts and approves the specification and the architectures as the
Owner, so the company it uses must let the Owner approve what she drafted.
The preview observes that company's policy (the one an earlier run with the
same checkout name created, else a new one): a reused company that would
refuse is a blocker, and the digest covers the policy. A new company starts
with the default policy and no exception, so the run creates it and stops
``blocked_by_sod`` before any approval or model call. The action never
grants, changes or bypasses the policy.
"""

import asyncio
import secrets

import pytest
from models import Company, CompanyMember, OwnerActionRun, SodPolicy, User
from services.management import owner_action_runner as runner
from services.management import owner_action_sod, owner_actions
from services.platform import sod
from sqlmodel import Session, select
from test_git_workflow import _member, enforcement  # noqa: F401
from test_owner_action_runner import _audit, _job, solo_company  # noqa: F401
from test_owner_actions_router import PARAMS, _company, _preview, _start, launched  # noqa: F401

NAME = owner_actions.SELF_HOST


def _owner_of(session, company_id):
    """An Owner of *company_id* signed in, and her account."""
    headers = _member(session, company_id, "Owner")
    user = session.exec(select(User).order_by(User.id.desc())).first()
    return headers, user


def _reused(session, acting_company_id, owner, policy=None):
    """A company an earlier run with ``lf-701`` created, the Owner its only approver."""
    made = _company(session)
    session.add(CompanyMember(company_id=made.id, user_id=owner.id, human_role="Owner"))
    if policy is not None:
        session.add(SodPolicy(company_id=made.id, **policy))
    session.add(
        OwnerActionRun(
            company_id=acting_company_id,
            action=NAME,
            status="failed",
            params={**PARAMS},
            started_by_user_id=owner.id,
            observations={"state": {"company_id": made.id}},
        )
    )
    session.commit()
    return made


def _policies(session):
    """Every policy row, as plain values (to compare before and after)."""
    with Session(session.get_bind()) as s:
        return [r.model_dump() for r in s.exec(select(SodPolicy)).all()]


def test_a_reused_company_without_the_exception_is_a_blocker(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,  # noqa: F811
):
    acting = _company(sqlite_session)
    headers, owner = _owner_of(sqlite_session, acting.id)
    reused = _reused(sqlite_session, acting.id, owner)
    shown = _preview(test_client, acting.id, headers)
    seen = shown["facts"]["sod"]
    assert shown["blockers"] == [owner_action_sod.BLOCKER]
    assert seen["company_id"] == reused.id and seen["new_company"] is False
    assert seen["mode"] == "high_risk" and seen["refused"] == [sod.APPROVE_SPEC]
    # Not granted, but it could be: the Owner is the company's only approver.
    assert (seen["solo_exception"], seen["solo_eligible"]) == (False, True)
    refused = _start(
        test_client, acting.id, headers, confirmation=shown["confirmation"]
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["blockers"] == [owner_action_sod.BLOCKER]
    assert launched == []


def test_with_the_exception_in_force_there_is_no_blocker(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,  # noqa: F811
):
    acting = _company(sqlite_session)
    headers, owner = _owner_of(sqlite_session, acting.id)
    _reused(sqlite_session, acting.id, owner, {"solo_exception": True})
    shown = _preview(test_client, acting.id, headers)
    assert shown["blockers"] == []
    assert shown["facts"]["sod"]["solo_in_force"] is True
    assert shown["facts"]["sod"]["refused"] == []
    started = _start(
        test_client, acting.id, headers, confirmation=shown["confirmation"]
    )
    assert started.status_code == 202, started.text
    assert started.json()["observations"]["confirmed"] == shown["facts"]
    assert launched == [started.json()["id"]]


def test_the_digest_changes_when_the_policy_changes(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,  # noqa: F811
):
    acting = _company(sqlite_session)
    headers, owner = _owner_of(sqlite_session, acting.id)
    reused = _reused(sqlite_session, acting.id, owner, {"mode": "off"})
    before = _preview(test_client, acting.id, headers)
    assert before["blockers"] == []
    # The Owner turns the rule back on (in the duties page) after the preview.
    sod.update_policy(sqlite_session, reused.id, owner.email, {"mode": "all"})
    sqlite_session.commit()
    after = _preview(test_client, acting.id, headers)
    assert after["confirmation"] != before["confirmation"]
    assert after["facts"]["sod"]["refused"] == [sod.APPROVE_SPEC, sod.APPROVE_ARCH]
    stale = _start(test_client, acting.id, headers, confirmation=before["confirmation"])
    assert stale.status_code == 409
    assert launched == []


def test_a_new_company_is_created_then_the_run_stops_blocked_by_sod(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    launched,  # noqa: F811
    monkeypatch,
):
    acting = _company(sqlite_session)
    headers, _owner = _owner_of(sqlite_session, acting.id)
    shown = _preview(test_client, acting.id, headers)
    seen = shown["facts"]["sod"]
    # Not a blocker: the exception can be granted only once the company exists.
    assert shown["blockers"] == []
    assert (seen["new_company"], seen["company_id"], seen["mode"]) == (
        True,
        None,
        "high_risk",
    )
    assert seen["refused"] == [sod.APPROVE_SPEC] and seen["solo_exception"] is False

    job, world, _run = _job(sqlite_session, monkeypatch)
    world["after_company"] = lambda cid: solo_company(
        sqlite_session, cid, world, granted=False
    )
    finished = asyncio.run(runner.run(job))
    assert (finished.status, finished.reason) == ("failed", owner_action_sod.BLOCKED)
    observed = finished.observations
    assert observed["steps_done"] == ["checkout", "company"]
    assert observed["sod"]["company_id"] == world["company_id"]
    assert observed["sod"]["refused"] == [sod.APPROVE_SPEC]
    assert observed["sod"]["solo_eligible"] is True  # she can grant it now
    assert observed["tokens_observed"] == 0
    # Nothing after the company: no specification posted, no sprint.
    assert [p for _, p, _ in world["calls"]] == ["/v1/init_company"]


@pytest.mark.parametrize("granted", [False, True])
def test_the_run_never_touches_the_policy(sqlite_session, monkeypatch, granted):
    job, world, _run = _job(sqlite_session, monkeypatch)
    world["after_company"] = lambda cid: solo_company(
        sqlite_session, cid, world, granted=granted
    )
    seen = {}
    real = owner_action_sod.situation

    def observed(session, company_id, user_id):
        seen["before"] = _policies(sqlite_session)
        return real(session, company_id, user_id)

    monkeypatch.setattr(owner_action_sod, "situation", observed)
    finished = asyncio.run(runner.run(job))
    assert finished.status == ("succeeded" if granted else "failed"), finished.reason
    assert _policies(sqlite_session) == seen["before"]
    with Session(sqlite_session.get_bind()) as s:
        row = s.exec(
            select(SodPolicy).where(SodPolicy.company_id == world["company_id"])
        ).one()
    assert (row.solo_exception, row.mode, row.solo_granted_by) == (
        granted,
        "high_risk",
        None,
    )


def test_the_exception_counts_only_while_one_person_can_approve(sqlite_session):
    company = Company(company_name=f"Two approvers {secrets.token_hex(3)}")
    people = [
        User(
            email=f"{r}-{secrets.token_hex(4)}@example.com",
            full_name=r,
            password_hash="x",
        )
        for r in ("owner", "manager")
    ]
    sqlite_session.add_all([company, *people])
    sqlite_session.commit()
    for user, role in zip(people, ("Owner", "Manager"), strict=True):
        sqlite_session.add(
            CompanyMember(company_id=company.id, user_id=user.id, human_role=role)
        )
    sqlite_session.add(
        SodPolicy(company_id=company.id, mode="all", solo_exception=True)
    )
    sqlite_session.commit()
    owner_id = people[0].id
    seen = owner_action_sod.situation(sqlite_session, company.id, owner_id)
    assert (seen["solo_exception"], seen["solo_eligible"], seen["solo_in_force"]) == (
        True,
        False,
        False,
    )
    assert seen["refused"] == [sod.APPROVE_SPEC, sod.APPROVE_ARCH]
    sod.update_policy(sqlite_session, company.id, "o@example.com", {"mode": "off"})
    sqlite_session.commit()
    off = owner_action_sod.situation(sqlite_session, company.id, owner_id)
    assert off["refused"] == []
