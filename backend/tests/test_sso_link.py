"""Linking an SSO identity from inside a signed-in session (issue #512;
``docs/design/SSO.md``, T25–T31): a signed-in person starts an authorisation
at one of their companies' connections with a ``state`` bound to their
browser and their live session (PKCE and a nonce, as the sign-in); the
callback links the provider's subject to that person only when the
provider's email is theirs, in a domain the company verified by DNS, and the
subject is nobody else's; a person in several companies can link this way
(the sign-in's email linking cannot, T15). Unlinking: oneself only while
another way to sign in remains; an Owner for the identities of their own
connection. Every link attempt and every unlink is an audit row."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from config import settings
from models import AuditLog, SessionFamily, SsoDomain, SsoIdentity, User
from sqlmodel import select
from sso_fake_idp import company, connect, member, s256, sign_in

LINKED_SUBJECT = "subject-ana"


@pytest.fixture
def browser(test_client, fake_idp):
    """The test client over https, so the session's Secure cookies come back."""
    test_client.base_url = "https://testserver"
    test_client.cookies.clear()
    return test_client


def _csrf(client) -> dict[str, str]:
    return {"X-Fabrique-CSRF": client.cookies.get("fabrique_csrf", path="/")}


def _signed_in(client, session, company_id, email="ana@acme.test", role="Viewer"):
    user, _ = member(session, company_id, role, email=email)
    response = client.post(
        "/v1/auth/login", json={"email": email, "password": "correct-password"}
    )
    assert response.status_code == 200, response.text
    return user


def _start(client, idp, company_id) -> dict:
    response = client.post(
        "/v1/auth/sso/link/start",
        json={"company_id": company_id},
        headers=_csrf(client),
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    params = {
        k: v[0]
        for k, v in parse_qs(
            urlsplit(response.json()["authorization_url"]).query
        ).items()
    }
    idp.nonce = params["nonce"]
    return params


def _callback(client, **params) -> dict:
    response = client.get(
        "/v1/auth/sso/callback", params=params, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert location.path == "/account", location
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def _link(client, idp, company_id) -> dict:
    params = _start(client, idp, company_id)
    return _callback(client, state=params["state"], code="code-from-idp")


def _identities(session, user_id=None):
    session.expire_all()
    query = select(SsoIdentity)
    if user_id is not None:
        query = query.where(SsoIdentity.user_id == user_id)
    return session.exec(query).all()


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _acme(session, **overrides):
    acme = company(session)
    overrides.setdefault("auto_join", False)
    return acme, connect(session, acme.id, **overrides)


# Linking


def test_a_member_of_several_companies_links_their_identity(
    sqlite_session, browser, fake_idp
):
    acme, conn = _acme(sqlite_session)
    ana = _signed_in(browser, sqlite_session, acme.id)
    other = company(sqlite_session)
    from models import CompanyMember

    sqlite_session.add(
        CompanyMember(company_id=other.id, user_id=ana.id, human_role="Owner")
    )
    sqlite_session.commit()
    fake_idp.claims = {"email": "Ana@Acme.test", "sub": LINKED_SUBJECT}

    params = _start(browser, fake_idp, acme.id)
    assert params["code_challenge_method"] == "S256"
    assert _callback(browser, state=params["state"], code="c") == {"sso_link": "linked"}
    assert (
        s256(fake_idp.token_requests[-1]["code_verifier"]) == params["code_challenge"]
    )
    (identity,) = _identities(sqlite_session, ana.id)
    assert (identity.subject, identity.connection_id) == (LINKED_SUBJECT, conn.id)
    sqlite_session.refresh(ana)
    assert ana.password_hash  # an explicit link keeps the password
    (row,) = _audits(sqlite_session, acme.id, "sso_identity_link")
    assert row.result["outcome"] == "linked" and row.result["user_id"] == ana.id
    assert row.details["observed_claims"]["subject"] == LINKED_SUBJECT

    # The provider's sign-in now resolves to this account, by subject.
    browser.cookies.clear()
    landed = sign_in(browser, fake_idp, conn.slug)
    assert "code" in landed, landed
    exchanged = browser.post("/v1/auth/sso/exchange", json={"code": landed["code"]})
    me = browser.get("/v1/auth/me")
    assert exchanged.status_code == 200 and me.json()["email"] == ana.email


def test_a_replayed_link_state_is_refused(sqlite_session, browser, fake_idp):
    acme, _ = _acme(sqlite_session)
    _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"sub": LINKED_SUBJECT}
    params = _start(browser, fake_idp, acme.id)
    assert _callback(browser, state=params["state"], code="c") == {"sso_link": "linked"}
    replay = browser.get(
        "/v1/auth/sso/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    # Consumed: the state is no one's any more, so it lands on the login page.
    assert parse_qs(urlsplit(replay.headers["location"]).query) == {
        "error": ["invalid_state"]
    }


def test_a_link_state_from_another_session_is_refused(
    sqlite_session, browser, fake_idp
):
    acme, _ = _acme(sqlite_session)
    ana = _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"sub": LINKED_SUBJECT}

    # Another browser: Ana signs in there too, but holds no binding.
    params = _start(browser, fake_idp, acme.id)
    browser.cookies.clear()
    _login = browser.post(
        "/v1/auth/login", json={"email": ana.email, "password": "correct-password"}
    )
    assert _login.status_code == 200
    assert _callback(browser, state=params["state"], code="c") == {
        "sso_link_error": "state_mismatch"
    }

    # The same browser, but the session that began it has ended.
    params = _start(browser, fake_idp, acme.id)
    assert browser.post("/v1/auth/logout", headers=_csrf(browser)).status_code == 204
    assert _callback(browser, state=params["state"], code="c") == {
        "sso_link_error": "state_mismatch"
    }
    assert not _identities(sqlite_session)
    assert not fake_idp.token_requests  # refused before the provider is asked


def test_a_link_state_cannot_finish_in_another_persons_browser(
    sqlite_session, test_client, fake_idp
):
    acme, _ = _acme(sqlite_session)
    test_client.base_url = "https://testserver"
    _signed_in(test_client, sqlite_session, acme.id, email="mallory@acme.test")
    attacker = _start(test_client, fake_idp, acme.id)
    test_client.cookies.clear()
    _signed_in(test_client, sqlite_session, acme.id)
    _start(test_client, fake_idp, acme.id)  # the victim's own binding
    assert _callback(test_client, state=attacker["state"], code="c") == {
        "sso_link_error": "state_mismatch"
    }


def test_a_mismatched_email_is_refused_and_recorded(sqlite_session, browser, fake_idp):
    acme, _ = _acme(sqlite_session)
    ana = _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"email": "bob@acme.test", "sub": LINKED_SUBJECT}
    assert _link(browser, fake_idp, acme.id) == {"sso_link_error": "email_mismatch"}
    assert not _identities(sqlite_session)
    (row,) = _audits(sqlite_session, acme.id, "sso_identity_link")
    assert row.result == {"outcome": "refused:email_mismatch", "user_id": ana.id}


def test_an_unverified_email_is_refused(sqlite_session, browser, fake_idp):
    acme, _ = _acme(sqlite_session)
    _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"email_verified": False, "sub": LINKED_SUBJECT}
    assert _link(browser, fake_idp, acme.id) == {"sso_link_error": "email_unverified"}
    assert not _identities(sqlite_session)


def test_a_subject_linked_to_someone_else_is_refused(sqlite_session, browser, fake_idp):
    acme, conn = _acme(sqlite_session, auto_join=True)
    fake_idp.claims = {"email": "first@acme.test", "sub": LINKED_SUBJECT}
    assert "code" in sign_in(browser, fake_idp, conn.slug)
    (first,) = _identities(sqlite_session)

    browser.cookies.clear()
    ana = _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"email": "ana@acme.test", "sub": LINKED_SUBJECT}
    assert _link(browser, fake_idp, acme.id) == {"sso_link_error": "identity_taken"}
    (still,) = _identities(sqlite_session)
    assert still.user_id == first.user_id != ana.id


def test_a_second_subject_at_the_same_provider_is_refused(
    sqlite_session, browser, fake_idp
):
    acme, _ = _acme(sqlite_session)
    _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"sub": "subject-ana"}
    assert _link(browser, fake_idp, acme.id) == {"sso_link": "linked"}
    started = browser.post(
        "/v1/auth/sso/link/start", json={"company_id": acme.id}, headers=_csrf(browser)
    )
    assert started.status_code == 409
    assert started.json()["detail"] == "already_linked"


def test_an_unverified_or_legacy_domain_links_nothing(
    sqlite_session, browser, fake_idp, monkeypatch
):
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE", True)
    acme = company(sqlite_session)
    connect(sqlite_session, acme.id, verified=False, auto_join=False)
    from services.platform import sso_domains

    sso_domains.sync(sqlite_session, acme.id, ["acme.test"])
    sqlite_session.commit()
    _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"sub": LINKED_SUBJECT}
    assert _link(browser, fake_idp, acme.id) == {"sso_link_error": "domain_unverified"}
    # A legacy domain in its grace still does not let a session link: the
    # sign-in's rule that the account belongs to this company alone is not
    # applied here, so only a DNS proof will do.
    row = sqlite_session.exec(select(SsoDomain)).one()
    row.legacy = True
    sqlite_session.add(row)
    sqlite_session.commit()
    assert _link(browser, fake_idp, acme.id) == {"sso_link_error": "domain_unverified"}
    assert not _identities(sqlite_session)


def test_starting_needs_a_browser_session_and_a_membership(
    sqlite_session, browser, fake_idp
):
    acme, _ = _acme(sqlite_session)
    other = company(sqlite_session)
    _, bearer = member(sqlite_session, acme.id, "Owner")
    body = {"company_id": acme.id}
    assert browser.post("/v1/auth/sso/link/start", json=body).status_code == 401
    by_bearer = browser.post("/v1/auth/sso/link/start", json=body, headers=bearer)
    assert by_bearer.status_code == 403
    _signed_in(browser, sqlite_session, other.id, email="outsider@acme.test")
    outsider = browser.post(
        "/v1/auth/sso/link/start", json=body, headers=_csrf(browser)
    )
    assert outsider.status_code == 403
    # No CSRF header: refused before anything is stored.
    assert browser.post("/v1/auth/sso/link/start", json=body).status_code == 403
    no_sso = browser.post(
        "/v1/auth/sso/link/start", json={"company_id": other.id}, headers=_csrf(browser)
    )
    assert no_sso.status_code == 404


def test_an_expired_link_state_is_refused(sqlite_session, browser, fake_idp):
    from models import SsoFlowState

    acme, _ = _acme(sqlite_session)
    _signed_in(browser, sqlite_session, acme.id)
    params = _start(browser, fake_idp, acme.id)
    for row in sqlite_session.exec(select(SsoFlowState)).all():
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        sqlite_session.add(row)
    sqlite_session.commit()
    assert _callback(browser, state=params["state"], code="c") == {
        "sso_link_error": "state_expired"
    }


def test_the_person_sees_their_identities_and_what_they_can_link(
    sqlite_session, browser, fake_idp
):
    acme, conn = _acme(sqlite_session)
    _signed_in(browser, sqlite_session, acme.id)
    before = browser.get("/v1/auth/sso/identities").json()
    assert before["identities"] == [] and before["has_password"] is True
    assert [c["company_id"] for c in before["linkable"]] == [acme.id]
    fake_idp.claims = {"sub": LINKED_SUBJECT}
    _link(browser, fake_idp, acme.id)
    after = browser.get("/v1/auth/sso/identities").json()
    (shown,) = after["identities"]
    assert shown["company_id"] == acme.id and shown["email"] == "ana@acme.test"
    assert "subject" not in shown and after["linkable"] == []
    assert conn.slug not in str(after)  # nothing the person did not need


# Unlinking


def _linked(session, browser, idp, role="Viewer"):
    acme, conn = _acme(session)
    user = _signed_in(browser, session, acme.id, role=role)
    idp.claims = {"sub": LINKED_SUBJECT}
    assert _link(browser, idp, acme.id) == {"sso_link": "linked"}
    (identity,) = _identities(session, user.id)
    return acme, conn, user, identity


def test_a_person_with_a_password_unlinks_themselves(sqlite_session, browser, fake_idp):
    acme, _, ana, identity = _linked(sqlite_session, browser, fake_idp)
    response = browser.delete(
        f"/v1/auth/sso/identities/{identity.id}", headers=_csrf(browser)
    )
    assert response.status_code == 200, response.text
    assert not _identities(sqlite_session)
    (row,) = _audits(sqlite_session, acme.id, "sso_identity_unlink")
    assert row.result["by"] == "self" and row.result["user_id"] == ana.id
    assert row.details["subject"] == LINKED_SUBJECT


def test_the_last_way_to_sign_in_cannot_be_unlinked(sqlite_session, browser, fake_idp):
    acme, _, ana, identity = _linked(sqlite_session, browser, fake_idp)
    ana.password_hash = ""  # SSO-only
    sqlite_session.add(ana)
    sqlite_session.commit()
    response = browser.delete(
        f"/v1/auth/sso/identities/{identity.id}", headers=_csrf(browser)
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "last_sign_in_method"
    assert _identities(sqlite_session)
    assert not _audits(sqlite_session, acme.id, "sso_identity_unlink")


def test_a_password_that_sso_requirement_refuses_does_not_count(
    sqlite_session, browser, fake_idp
):
    _, conn, _, identity = _linked(sqlite_session, browser, fake_idp)
    conn.require_sso = True
    sqlite_session.add(conn)
    sqlite_session.commit()
    response = browser.delete(
        f"/v1/auth/sso/identities/{identity.id}", headers=_csrf(browser)
    )
    assert response.status_code == 409
    assert _identities(sqlite_session)


def test_a_person_cannot_unlink_someone_elses_identity(
    sqlite_session, browser, fake_idp
):
    acme, _, _, identity = _linked(sqlite_session, browser, fake_idp)
    browser.cookies.clear()
    _signed_in(browser, sqlite_session, acme.id, email="bob@acme.test")
    response = browser.delete(
        f"/v1/auth/sso/identities/{identity.id}", headers=_csrf(browser)
    )
    assert response.status_code == 404
    assert _identities(sqlite_session)


def test_an_owner_unlinks_a_members_identity_and_ends_its_sso_sessions(
    sqlite_session, browser, fake_idp
):
    acme, conn, ana, identity = _linked(sqlite_session, browser, fake_idp)
    ana.password_hash = ""  # even the last way: the Owner's decision
    sqlite_session.add(ana)
    sqlite_session.commit()
    browser.cookies.clear()
    landed = sign_in(browser, fake_idp, conn.slug)
    exchanged = browser.post("/v1/auth/sso/exchange", json={"code": landed["code"]})
    assert exchanged.status_code == 200  # an SSO-started session
    owner, owner_headers = member(sqlite_session, acme.id, "Owner")
    manager, manager_headers = member(sqlite_session, acme.id, "Manager")
    path = f"/v1/companies/{acme.id}/sso/identities/{identity.id}"

    listed = browser.get(
        f"/v1/companies/{acme.id}/sso/identities", headers=owner_headers
    )
    assert [i["user_id"] for i in listed.json()["identities"]] == [ana.id]
    assert browser.delete(path, headers=manager_headers).status_code == 403
    response = browser.delete(path, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["other_sign_in"] is False
    assert not _identities(sqlite_session)
    (row,) = _audits(sqlite_session, acme.id, "sso_identity_unlink")
    assert row.result["by"] == "owner" and row.agent_id == f"human:{owner.email}"
    families = sqlite_session.exec(
        select(SessionFamily).where(
            SessionFamily.user_id == ana.id, SessionFamily.method == "sso"
        )
    ).all()
    assert families and all(f.revoked_reason == "sso_unlinked" for f in families)
    assert manager.id != owner.id


def test_an_owner_cannot_unlink_another_companys_identity(
    sqlite_session, browser, fake_idp
):
    _, _, _, identity = _linked(sqlite_session, browser, fake_idp)
    rival = company(sqlite_session)
    connect(sqlite_session, rival.id, slug="rival", allowed_domains=["rival.test"])
    _, owner_headers = member(sqlite_session, rival.id, "Owner")
    response = browser.delete(
        f"/v1/companies/{rival.id}/sso/identities/{identity.id}", headers=owner_headers
    )
    assert response.status_code == 404
    assert _identities(sqlite_session)
    assert sqlite_session.get(User, identity.user_id) is not None
