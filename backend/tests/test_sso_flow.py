"""Signing in with SSO end to end (``routers/sso.py``, ``services/platform/sso_flow.py``;
docs/archive/design/SSO.md): Authorization Code with PKCE, a one-time ``state`` bound
to the browser by an HttpOnly cookie, the ID token validated by us, the person
resolved by ``(issuer, sub)``, the role from the membership (a new member gets
the default role), a one-time handoff code exchanged for the same browser
session the password login starts (HttpOnly cookies, issue #524), and an
audit row per completed callback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import auth_tokens
from models import AuditLog, CompanyMember, SsoFlowState, SsoIdentity, User
from sqlmodel import select
from sso_fake_idp import (
    CLIENT_ID,
    ISSUER,
    callback,
    company,
    connect,
    member,
    new_rsa_key,
    s256,
    sign_in,
    start,
)


def _exchange(client, code):
    return client.post("/v1/auth/sso/exchange", json={"code": code})


def _session(response):
    """The access token the exchange set as the HttpOnly session cookie."""
    assert "access_token" not in response.json()
    return response.cookies["fabrique_session"]


def _audits(session, company_id, action="sso_sign_in"):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def test_a_new_person_signs_in_and_joins_with_the_default_role(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, default_role="Viewer")
    params = start(test_client, fake_idp, conn.slug)
    assert params["_location"].startswith(f"{ISSUER}/authorize?")
    assert (
        params["client_id"] == CLIENT_ID and params["code_challenge_method"] == "S256"
    )
    assert params["scope"] == "openid email profile"
    landed = callback(test_client, state=params["state"], code="code-from-idp")
    assert "error" not in landed, landed
    # PKCE: the verifier sent to the token endpoint is the challenge's.
    sent = fake_idp.token_requests[-1]
    assert s256(sent["code_verifier"]) == params["code_challenge"]
    assert sent["code"] == "code-from-idp" and sent["authorization"] is None
    token = _exchange(test_client, landed["code"])
    assert token.status_code == 200, token.text
    claims = auth_tokens.decode_access_token(_session(token))
    user = sqlite_session.get(User, int(claims["sub"]))
    assert user.email == "ana@acme.test" and user.password_hash == ""
    role = sqlite_session.exec(
        select(CompanyMember.human_role).where(
            CompanyMember.user_id == user.id, CompanyMember.company_id == acme.id
        )
    ).one()
    assert role == "Viewer"
    me = test_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {_session(token)}"},
    )
    assert (
        me.status_code == 200 and me.json()["memberships"][0]["company_id"] == acme.id
    )
    # The handoff code is good once.
    assert _exchange(test_client, landed["code"]).status_code == 401
    # The audit row: what the provider asserted, and the outcome.
    (row,) = _audits(sqlite_session, acme.id)
    assert row.details["observed_claims"] == {
        "issuer": ISSUER,
        "subject": "subject-1",
        "email": "ana@acme.test",
        "email_verified": True,
    }
    assert row.result["outcome"] == "created" and row.result["user_id"] == user.id
    assert row.decision_signature and row.chain_hash


def test_the_login_page_may_name_an_email_or_a_domain(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    connect(sqlite_session, acme.id, allowed_domains=["corp-sso.test"])
    assert "state" in start(test_client, fake_idp, "someone@corp-sso.test")
    assert "state" in start(test_client, fake_idp, "corp-sso.test")
    unknown = test_client.get("/v1/auth/sso/nobody.test/start", follow_redirects=False)
    assert unknown.status_code == 302
    assert unknown.headers["location"].endswith("/login/sso?error=unknown_connection")


def test_a_disabled_connection_cannot_be_started(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id, enabled=False)
    response = test_client.get(
        f"/v1/auth/sso/{conn.slug}/start", follow_redirects=False
    )
    assert "error=unknown_connection" in response.headers["location"]


def test_a_replayed_or_missing_state_is_refused(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    params = start(test_client, fake_idp, conn.slug)
    assert "code" in callback(test_client, state=params["state"], code="c")
    assert callback(test_client, state=params["state"], code="c") == {
        "error": "invalid_state"
    }
    assert callback(test_client, code="c") == {"error": "missing_state"}
    assert callback(test_client, state="made-up", code="c") == {
        "error": "invalid_state"
    }


def test_a_state_from_another_browser_is_refused(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    params = start(test_client, fake_idp, conn.slug)
    test_client.cookies.clear()  # the victim's browser holds no binding
    assert callback(test_client, state=params["state"], code="c") == {
        "error": "state_mismatch"
    }
    params = start(test_client, fake_idp, conn.slug)
    test_client.cookies.clear()
    test_client.cookies.set("fabrique_sso_binding", "forged", domain="testserver.local")
    assert callback(test_client, state=params["state"], code="c") == {
        "error": "state_mismatch"
    }


def test_an_expired_state_is_refused(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    params = start(test_client, fake_idp, conn.slug)
    for row in sqlite_session.exec(select(SsoFlowState)).all():
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        sqlite_session.add(row)
    sqlite_session.commit()
    assert callback(test_client, state=params["state"], code="c") == {
        "error": "state_expired"
    }


def test_a_forged_token_is_refused_and_recorded(sqlite_session, test_client, fake_idp):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id)
    fake_idp.sign_key = new_rsa_key()
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "invalid_token"}
    (row,) = _audits(sqlite_session, acme.id)
    assert row.result["outcome"] == "refused:invalid_token"
    # Claims that failed validation are not recorded as observed.
    assert row.details["observed_claims"] is None
    assert sqlite_session.exec(select(SsoIdentity)).all() == [] or all(
        i.connection_id != conn.id for i in sqlite_session.exec(select(SsoIdentity))
    )


def test_a_wrong_nonce_and_an_unverified_email_are_refused(
    sqlite_session, test_client, fake_idp
):
    conn = connect(sqlite_session, company(sqlite_session).id)
    fake_idp.claims = {"nonce": "replayed-nonce"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "invalid_nonce"}
    fake_idp.claims = {"email_verified": False}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "email_unverified"}


def test_the_provider_refusing_ends_the_flow(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    params = start(test_client, fake_idp, conn.slug)
    assert callback(test_client, state=params["state"], error="access_denied") == {
        "error": "idp_denied"
    }


def test_a_domain_the_company_did_not_allow_is_refused(
    sqlite_session, test_client, fake_idp
):
    conn = connect(sqlite_session, company(sqlite_session).id)
    fake_idp.claims = {"email": "ana@elsewhere.test", "sub": "subject-x"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_not_allowed"}


def test_without_auto_join_a_stranger_is_refused(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id, auto_join=False)
    fake_idp.claims = {"email": "stranger@acme.test", "sub": "subject-s"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "not_a_member"}
    assert (
        sqlite_session.exec(
            select(User).where(User.email == "stranger@acme.test")
        ).first()
        is None
    )


def test_an_invited_member_is_linked_keeps_their_role_and_becomes_sso_only(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=False)
    user, _ = member(sqlite_session, acme.id, "Manager", email="Lin@Acme.test")
    fake_idp.claims = {"email": "lin@acme.test", "sub": "subject-lin"}
    landed = sign_in(test_client, fake_idp, conn.slug)
    token = _session(_exchange(test_client, landed["code"]))
    assert auth_tokens.decode_access_token(token)["sub"] == str(user.id)
    identity = sqlite_session.exec(
        select(SsoIdentity).where(SsoIdentity.subject == "subject-lin")
    ).one()
    assert identity.user_id == user.id and identity.issuer == ISSUER
    roles = sqlite_session.exec(
        select(CompanyMember.human_role).where(CompanyMember.user_id == user.id)
    ).all()
    assert roles == ["Manager"]  # never changed by a claim
    # Linked: the password goes (whoever registered the address first keeps
    # nothing), only an Owner keeps it as the break-glass path.
    sqlite_session.refresh(user)
    assert user.password_hash == ""
    row = _audits(sqlite_session, acme.id)[-1]
    assert row.result["outcome"] == "linked" and row.result["password_cleared"] is True


def test_an_owner_linked_by_sso_keeps_the_password(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=False)
    owner, _ = member(sqlite_session, acme.id, "Owner")
    fake_idp.claims = {"email": owner.email, "sub": f"subject-{owner.id}"}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    sqlite_session.refresh(owner)
    assert owner.password_hash


def test_an_existing_account_outside_the_company_is_never_adopted(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=True)
    # An account with that address exists, in no company or in another one:
    # a provider an Owner runs must not take it over through auto-join.
    loose, _ = member(sqlite_session, None, "Viewer", email="loose@acme.test")
    elsewhere, _ = member(
        sqlite_session, company(sqlite_session).id, "Owner", email="boss@acme.test"
    )
    for user in (loose, elsewhere):
        fake_idp.claims = {"email": user.email, "sub": f"subject-{user.id}"}
        assert sign_in(test_client, fake_idp, conn.slug) == {"error": "account_exists"}
        sqlite_session.refresh(user)
        assert user.password_hash
    assert (
        sqlite_session.exec(
            select(CompanyMember).where(CompanyMember.company_id == acme.id)
        ).all()
        == []
    )


def test_an_account_in_other_companies_too_is_not_linked(
    sqlite_session, test_client, fake_idp
):
    # The attack: an Owner invites someone else's account into their company
    # and asserts its email from a provider they run. Linking would hand them
    # the account, and with it every other company it belongs to.
    victim_home, attacker = company(sqlite_session), company(sqlite_session)
    conn = connect(sqlite_session, attacker.id, auto_join=False)
    victim, _ = member(sqlite_session, victim_home.id, "Owner", email="ceo@acme.test")
    sqlite_session.add(
        CompanyMember(company_id=attacker.id, user_id=victim.id, human_role="Viewer")
    )
    sqlite_session.commit()
    fake_idp.claims = {"email": victim.email, "sub": "attacker-chosen"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "link_refused"}
    assert (
        sqlite_session.exec(
            select(SsoIdentity).where(SsoIdentity.user_id == victim.id)
        ).all()
        == []
    )


def test_the_identity_follows_the_subject_not_the_email(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, allowed_domains=["acme.test", "acme2.test"])
    fake_idp.claims = {"email": "first@acme.test", "sub": "subject-f"}
    first = _exchange(test_client, sign_in(test_client, fake_idp, conn.slug)["code"])
    fake_idp.claims = {"email": "renamed@acme2.test", "sub": "subject-f"}
    second = _exchange(test_client, sign_in(test_client, fake_idp, conn.slug)["code"])
    sub = auth_tokens.decode_access_token(_session(first))["sub"]
    assert auth_tokens.decode_access_token(_session(second))["sub"] == sub
    # An account already linked to another subject at this issuer is not re-linked.
    fake_idp.claims = {"email": "first@acme.test", "sub": "subject-other"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "identity_conflict"}


def test_the_sign_in_routes_are_rate_limited(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    from config import settings

    monkeypatch.setattr(settings, "SSO_RATE_LIMIT", "2/minute")
    codes = [
        test_client.post("/v1/auth/sso/exchange", json={"code": "x"}).status_code
        for _ in range(3)
    ]
    assert codes[-1] == 429


def test_the_client_secret_goes_to_the_token_endpoint_only(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    import base64
    import secrets

    from config import settings
    from cryptography.fernet import Fernet
    from services.platform import sso_connections

    monkeypatch.setattr(settings, "FERNET_SECRET_KEY", Fernet.generate_key().decode())
    conn = connect(sqlite_session, company(sqlite_session).id)
    secret = secrets.token_urlsafe(24)
    sso_connections.set_secret(conn, secret)
    sqlite_session.add(conn)
    sqlite_session.commit()
    params = start(test_client, fake_idp, conn.slug)
    assert secret not in params["_location"]
    assert "code" in callback(test_client, state=params["state"], code="c")
    basic = base64.b64encode(f"{CLIENT_ID}:{secret}".encode()).decode()
    assert fake_idp.token_requests[-1]["authorization"] == f"Basic {basic}"
    # A secret the current key cannot read refuses the sign-in with a reason.
    monkeypatch.setattr(settings, "FERNET_SECRET_KEY", Fernet.generate_key().decode())
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "idp_misconfigured"}
