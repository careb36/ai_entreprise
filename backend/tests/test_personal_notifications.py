"""Personal notifications (issue #683; ``services/platform/notifications.py``,
``services/integrations/person_notices.py``, ``routers/notifications.py``).

Each rule has its test:

- one row per receiver who is still a member of the company, holding a
  reference and allowlisted integer ids, never text (GDPR data minimisation);
- a producer that sees the same event twice writes one row per person;
- a person reads, marks and snoozes only their own rows; another person's
  row is a 404, an API token is refused, a company left hides its rows;
- snooze hides a row from the unread list, for at most 30 days, 24 hours for
  a security notice (ISO/IEC 27001:2022 A.5.24-A.5.26);
- preferences: a security notice cannot leave the app, a muted kind writes
  no row, email needs the app copy;
- the email copy is queued only when SMTP is configured and asked for, and
  says nothing of the subject;
- the producers: the SSO break-glass exclusion and its lifting to the other
  Owners, an SSO domain that lapses on a re-check to every Owner (the
  unconfirmed lapse's hook too), a risk hold and an integration waiting for
  a person to the Managers and Owners, and nothing for a change rolled back.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import pytest
from config import settings
from models import (
    BacklogItem,
    Company,
    CompanyMember,
    OutboxEvent,
    Sprint,
    SprintIntegration,
    SsoDomain,
    TaskEvent,
    Team,
    TeamMember,
    User,
)
from models.notification import NotificationPreference, PersonalNotification
from services.integrations import person_notices
from services.platform import api_tokens, notifications, sso_domains, sso_team_control
from sqlmodel import select


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _company(session) -> Company:
    row = Company(company_name=f"Notices {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def _person(session, company_id: int | None, role: str = "Viewer"):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(4)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _rows(session, **where) -> list[PersonalNotification]:
    session.expire_all()
    query = select(PersonalNotification)
    for name, value in where.items():
        query = query.where(getattr(PersonalNotification, name) == value)
    return list(session.exec(query.order_by(PersonalNotification.id)).all())


def _notice(session, company_id, user_id, kind="review_requested", **fields):
    row = PersonalNotification(
        user_id=user_id,
        company_id=company_id,
        kind=kind,
        subject_type=notifications.KINDS[kind].subject_type,
        subject_id=fields.pop("subject_id", 1),
        created_at=fields.pop("created_at", _now()),
        **fields,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture(autouse=True)
def _listening(monkeypatch, tmp_path):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    person_notices.install()


# --- one row per receiver, minimal content -------------------------------------


def test_one_row_per_member_receiver_with_ids_only(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Manager")
    bea, _ = _person(sqlite_session, acme.id, "Owner")
    stranger, _ = _person(sqlite_session, None)

    got = notifications.notify(
        sqlite_session,
        acme.id,
        [ana.id, bea.id, stranger.id],
        "review_requested",
        subject_id=42,
        context={"sprint_id": 7, "title": "Secret plan", "user_id": 3},
    )
    sqlite_session.commit()

    assert got == sorted([ana.id, bea.id])  # not someone outside the company
    rows = _rows(sqlite_session)
    assert [r.user_id for r in rows] == sorted([ana.id, bea.id])
    for row in rows:
        assert (row.subject_type, row.subject_id) == ("task", 42)
        # Only the kind's allowlisted integer ids: no text, no other id.
        assert row.context == {"sprint_id": 7}
        assert row.read_at is None and row.snoozed_until is None


def test_the_same_event_twice_writes_one_row_per_person(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Manager")
    for _ in range(2):
        notifications.notify(
            sqlite_session, acme.id, [ana.id], "review_requested", 1, dedupe_key="e:1"
        )
        sqlite_session.commit()
    assert len(_rows(sqlite_session)) == 1


def test_an_unknown_kind_is_a_programming_error(sqlite_session):
    with pytest.raises(ValueError):
        notifications.notify(sqlite_session, 1, [1], "gossip", 1)


# --- authorization --------------------------------------------------------------


def test_a_person_reads_and_changes_only_their_own_rows(
    sqlite_session, test_client, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    acme = _company(sqlite_session)
    ana, ana_h = _person(sqlite_session, acme.id, "Owner")
    _, bea_h = _person(sqlite_session, acme.id, "Owner")
    mine = _notice(sqlite_session, acme.id, ana.id)

    listed = test_client.get("/v1/notifications", headers=ana_h)
    assert listed.status_code == 200, listed.text
    assert [n["id"] for n in listed.json()["notifications"]] == [mine.id]
    assert listed.json()["unread"] == 1
    assert test_client.get("/v1/notifications", headers=bea_h).json() == {
        "notifications": [],
        "unread": 0,
        "kinds": sorted(notifications.KINDS),
    }
    # Another person's row does not exist for them.
    for path, body in (
        (f"/v1/notifications/{mine.id}/read", {}),
        (f"/v1/notifications/{mine.id}/snooze", {"until": None}),
    ):
        response = test_client.post(path, json=body, headers=bea_h)
        assert response.status_code == 404, path
    # Marking all read touches only one's own rows.
    response = test_client.post("/v1/notifications/read", json={}, headers=bea_h)
    assert response.json() == {"read": 0}
    assert _rows(sqlite_session)[0].read_at is None
    assert test_client.get("/v1/notifications").status_code == 401

    # An API token is not a person.
    _, raw = api_tokens.create_token(sqlite_session, acme.id, "ci", role="Viewer")
    token_h = {"Authorization": f"Bearer {raw}"}
    assert test_client.get("/v1/notifications", headers=token_h).status_code == 403

    read = test_client.post(f"/v1/notifications/{mine.id}/read", json={}, headers=ana_h)
    assert read.status_code == 200 and read.json()["read_at"]
    count = test_client.get("/v1/notifications/count", headers=ana_h)
    assert count.json() == {"unread": 0}
    unread = test_client.post(
        f"/v1/notifications/{mine.id}/read", json={"read": False}, headers=ana_h
    )
    assert unread.json()["read_at"] is None


def test_the_rows_of_a_company_left_are_hidden(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Owner")
    row = _notice(sqlite_session, acme.id, ana.id)
    membership = sqlite_session.exec(
        select(CompanyMember).where(CompanyMember.user_id == ana.id)
    ).one()
    sqlite_session.delete(membership)
    sqlite_session.commit()
    assert notifications.list_mine(sqlite_session, ana.id)["notifications"] == []
    with pytest.raises(notifications.NotificationRefused) as refused:
        notifications.mark_read(sqlite_session, ana.id, row.id)
    assert refused.value.status == 404


# --- snooze ---------------------------------------------------------------------


def test_snooze_hides_a_row_within_its_limit(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Owner")
    plain = _notice(sqlite_session, acme.id, ana.id)
    security = _notice(sqlite_session, acme.id, ana.id, "sso_domain_lapsed")

    def refused(row, until):
        with pytest.raises(notifications.NotificationRefused) as caught:
            notifications.snooze(sqlite_session, ana.id, row.id, until)
        return caught.value.code

    assert refused(plain, _now() - timedelta(minutes=1)) == "snooze_in_the_past"
    assert refused(plain, _now() + timedelta(days=31)) == "snooze_too_long"
    assert refused(security, _now() + timedelta(hours=25)) == "security_snooze_too_long"

    notifications.snooze(sqlite_session, ana.id, plain.id, _now() + timedelta(days=3))
    notifications.snooze(
        sqlite_session, ana.id, security.id, _now() + timedelta(hours=23)
    )
    unread = notifications.list_mine(sqlite_session, ana.id)
    assert unread["notifications"] == [] and unread["unread"] == 0
    snoozed = notifications.list_mine(sqlite_session, ana.id, state="snoozed")
    assert {n["id"] for n in snoozed["notifications"]} == {plain.id, security.id}

    # Snoozed until a moment now past: back in the unread list.
    plain.snoozed_until = _now() - timedelta(seconds=1)
    sqlite_session.add(plain)
    sqlite_session.commit()
    assert notifications.unread_count(sqlite_session, ana.id) == 1
    back = notifications.snooze(sqlite_session, ana.id, security.id, None)
    assert back["snoozed_until"] is None
    assert notifications.unread_count(sqlite_session, ana.id) == 2

    notifications.mark_read(sqlite_session, ana.id, plain.id)
    assert refused(plain, _now() + timedelta(hours=1)) == "already_read"
    with pytest.raises(notifications.NotificationRefused):
        notifications.list_mine(sqlite_session, ana.id, state="maybe")


def test_snooze_through_the_api_with_a_timezone(sqlite_session, test_client):
    acme = _company(sqlite_session)
    ana, ana_h = _person(sqlite_session, acme.id, "Owner")
    row = _notice(sqlite_session, acme.id, ana.id)
    until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    response = test_client.post(
        f"/v1/notifications/{row.id}/snooze", json={"until": until}, headers=ana_h
    )
    assert response.status_code == 200, response.text
    assert response.json()["snoozed_until"]
    too_long = (datetime.now(UTC) + timedelta(days=40)).isoformat()
    response = test_client.post(
        f"/v1/notifications/{row.id}/snooze", json={"until": too_long}, headers=ana_h
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "snooze_too_long"


# --- preferences ----------------------------------------------------------------


def test_preferences_never_mute_a_security_notice(sqlite_session, test_client):
    acme = _company(sqlite_session)
    ana, ana_h = _person(sqlite_session, acme.id, "Owner")

    got = test_client.get("/v1/notifications/preferences", headers=ana_h).json()
    by_kind = {p["kind"]: p for p in got["preferences"]}
    assert set(by_kind) == set(notifications.KINDS)
    assert got["email_available"] is False
    assert by_kind["sso_domain_lapsed"]["in_app_locked"] is True
    assert by_kind["review_requested"] == {
        "kind": "review_requested",
        "security": False,
        "in_app": True,
        "email": False,
        "in_app_locked": False,
    }

    url = "/v1/notifications/preferences/"
    muted = test_client.put(
        url + "sso_break_glass_exclusion",
        json={"in_app": False, "email": False},
        headers=ana_h,
    )
    assert muted.status_code == 409
    assert muted.json()["detail"] == "security_notice_in_app"
    mail_only = test_client.put(
        url + "review_requested", json={"in_app": False, "email": True}, headers=ana_h
    )
    assert mail_only.status_code == 422
    assert test_client.put(url + "gossip", json={}, headers=ana_h).status_code == 404
    off = test_client.put(
        url + "review_requested", json={"in_app": False, "email": False}, headers=ana_h
    )
    assert off.status_code == 200 and off.json()["in_app"] is False

    # A muted kind writes no row; a security notice is written whatever is stored.
    sqlite_session.add(
        NotificationPreference(user_id=ana.id, kind="sso_domain_lapsed", in_app=False)
    )
    sqlite_session.commit()
    plain = notifications.notify(
        sqlite_session, acme.id, [ana.id], "review_requested", 1
    )
    security = notifications.notify(
        sqlite_session, acme.id, [ana.id], "sso_domain_lapsed", 1
    )
    assert plain == [] and security == [ana.id]


# --- email ----------------------------------------------------------------------


def test_email_is_queued_only_with_smtp_and_says_nothing_of_the_subject(
    sqlite_session, monkeypatch
):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Owner")

    def emails():
        return sqlite_session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == notifications.EMAIL_EVENT
            )
        ).all()

    # No SMTP: the app copy is written and nothing waits on mail.
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    notifications.notify(sqlite_session, acme.id, [ana.id], "sso_domain_lapsed", 5)
    sqlite_session.commit()
    assert len(_rows(sqlite_session)) == 1 and emails() == []

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.invalid")
    # Off by default for a non-security kind; on for a security notice.
    notifications.notify(sqlite_session, acme.id, [ana.id], "review_requested", 6)
    notifications.notify(sqlite_session, acme.id, [ana.id], "sso_domain_lapsed", 7)
    sqlite_session.commit()
    (queued,) = emails()
    assert queued.payload == {
        "user_id": ana.id,
        "company_id": acme.id,
        "kind": "sso_domain_lapsed",
    }

    sent: list = []
    deliver = person_notices.deliver_email
    assert asyncio.run(deliver(sqlite_session, queued.payload, sent.append))
    (message,) = sent
    assert message["To"] == ana.email
    body = message.get_content()
    assert "security notice" in body and acme.company_name not in body
    assert acme.company_name not in message["Subject"]

    # Turned off since, or SMTP gone: dropped, never sent.
    notifications.set_preference(
        sqlite_session, ana.id, "sso_domain_lapsed", True, False
    )
    assert not asyncio.run(deliver(sqlite_session, queued.payload, sent.append))
    notifications.set_preference(
        sqlite_session, ana.id, "sso_domain_lapsed", True, True
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert not asyncio.run(deliver(sqlite_session, queued.payload, sent.append))
    assert len(sent) == 1


# --- producers ------------------------------------------------------------------


def test_a_break_glass_exclusion_notifies_the_other_owners(sqlite_session):
    acme = _company(sqlite_session)
    alba, _ = _person(sqlite_session, acme.id, "Owner")
    beto, _ = _person(sqlite_session, acme.id, "Owner")
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    team = Team(
        company_id=acme.id, name="platform", role="Viewer", membership_source="sso"
    )
    sqlite_session.add(team)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=team.id, user_id=viewer.id)
    )
    sqlite_session.commit()

    row = sso_team_control.create_exclusion(
        sqlite_session, team, alba, viewer.id, "Suspected account compromise"
    )
    sqlite_session.commit()
    (notice,) = _rows(sqlite_session)
    assert notice.user_id == beto.id  # every other Owner, not the actor
    assert notice.kind == "sso_break_glass_exclusion"
    assert (notice.subject_type, notice.subject_id) == ("sso_team_exclusion", row.id)
    assert notice.context == {"team_id": team.id, "user_id": viewer.id}
    assert notifications.serialize(notice)["security"] is True
    # The NATS notice is still queued, as before.
    queued = sqlite_session.exec(select(OutboxEvent)).first()
    assert queued.payload["subject"] == sso_team_control.NOTICE_SUBJECT

    sso_team_control.lift_exclusion(
        sqlite_session, team, beto, row.id, "Account recovered and checked"
    )
    sqlite_session.commit()
    lifted = _rows(sqlite_session, kind="sso_break_glass_lifted")
    assert [n.user_id for n in lifted] == [alba.id]


def _verified_domain(session, company_id) -> SsoDomain:
    row = SsoDomain(
        company_id=company_id,
        domain=f"{secrets.token_hex(3)}.test",
        token="unused",
        status="verified",
        verified_at=_now(),
    )
    session.add(row)
    session.commit()
    return row


def test_a_domain_that_lapses_on_a_recheck_notifies_every_owner(
    sqlite_session, monkeypatch
):
    acme = _company(sqlite_session)
    alba, _ = _person(sqlite_session, acme.id, "Owner")
    beto, _ = _person(sqlite_session, acme.id, "Owner")
    _person(sqlite_session, acme.id, "Manager")
    monkeypatch.setattr(sso_domains, "_lookup", lambda domain: ("not_found", [], None))

    asked = _verified_domain(sqlite_session, acme.id)
    sso_domains.check(sqlite_session, asked, trigger="owner", requested_by="owner")
    assert _rows(sqlite_session) == []  # the Owner who asked saw the answer

    row = _verified_domain(sqlite_session, acme.id)
    sso_domains.check(sqlite_session, row, trigger="recheck")
    notices = _rows(sqlite_session)
    assert sorted(n.user_id for n in notices) == sorted([alba.id, beto.id])
    assert {(n.kind, n.subject_id) for n in notices} == {("sso_domain_lapsed", row.id)}
    assert all(n.context == {} for n in notices)  # never the domain's name

    # The hook the 14-day unconfirmed lapse (#684) calls.
    other = _verified_domain(sqlite_session, acme.id)
    notified = sso_domains.notify_lapsed(sqlite_session, other, "lapsed_unconfirmed")
    assert notified == sorted([alba.id, beto.id])
    with pytest.raises(ValueError):
        sso_domains.notify_lapsed(sqlite_session, other, "maybe")


def _sprint(session, company_id) -> Sprint:
    sprint = Sprint(company_id=company_id, sprint_name="S")
    session.add(sprint)
    session.commit()
    return sprint


def test_a_risk_hold_asks_the_people_who_may_accept_it(sqlite_session):
    acme = _company(sqlite_session)
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    manager, _ = _person(sqlite_session, acme.id, "Manager")
    owner, _ = _person(sqlite_session, acme.id, "Owner")
    sprint = _sprint(sqlite_session, acme.id)
    item = BacklogItem(sprint_id=sprint.id, title="Risky change")
    sqlite_session.add(item)
    sqlite_session.commit()
    sqlite_session.add(
        TaskEvent(
            company_id=acme.id,
            sprint_id=sprint.id,
            task_id=item.id,
            event_type="risk_hold",
            payload={"text": "Waiting for a person"},
        )
    )
    sqlite_session.commit()
    notices = _rows(sqlite_session, kind="review_requested")
    assert sorted(n.user_id for n in notices) == sorted([manager.id, owner.id])
    assert viewer.id not in {n.user_id for n in notices}
    assert all(
        n.subject_id == item.id and n.context == {"sprint_id": sprint.id}
        for n in notices
    )


def _integration(session, company_id, sprint_id, status) -> SprintIntegration:
    row = SprintIntegration(
        company_id=company_id,
        sprint_id=sprint_id,
        trigger="manual",
        requested_by="system",
        status=status,
        branch=f"fabrique/sprint-{sprint_id}",
        base="main",
    )
    session.add(row)
    session.commit()
    return row


def test_an_integration_waiting_for_a_person_notifies_once_per_status(sqlite_session):
    acme = _company(sqlite_session)
    manager, _ = _person(sqlite_session, acme.id, "Manager")
    sprint = _sprint(sqlite_session, acme.id)

    row = _integration(sqlite_session, acme.id, sprint.id, "running")
    assert _rows(sqlite_session) == []
    row.status = "conflict"
    sqlite_session.add(row)
    sqlite_session.commit()
    row.details = {"reason": "again"}  # another change, same status
    sqlite_session.add(row)
    sqlite_session.commit()
    (notice,) = _rows(sqlite_session)
    assert (notice.user_id, notice.kind, notice.subject_id) == (
        manager.id,
        "integration_waiting",
        row.id,
    )
    passed = _integration(sqlite_session, acme.id, sprint.id, "tests_passed")
    failed = _integration(sqlite_session, acme.id, sprint.id, "tests_failed")
    subjects = {n.subject_id for n in _rows(sqlite_session)}
    assert passed.id not in subjects and failed.id in subjects


def test_a_change_rolled_back_notifies_no_one(sqlite_session):
    acme = _company(sqlite_session)
    _person(sqlite_session, acme.id, "Manager")
    sprint = _sprint(sqlite_session, acme.id)
    sqlite_session.add(
        SprintIntegration(
            company_id=acme.id,
            sprint_id=sprint.id,
            trigger="manual",
            requested_by="system",
            status="error",
            branch="b",
            base="main",
        )
    )
    sqlite_session.flush()
    sqlite_session.rollback()
    assert _rows(sqlite_session) == []
