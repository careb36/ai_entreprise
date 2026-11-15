"""Browser sessions in HttpOnly cookies (issue #524, ``docs/design/SESSIONS.md``):
the cookies' flags, no token in the browser's login body, refresh rotation,
reuse revoking the family, the two-tab grace, absolute expiry, logout, CSRF on
unsafe cookie requests and never on Bearer, and the revocation hooks (SSO
becoming required, a member removed)."""

from __future__ import annotations

import secrets
from datetime import timedelta

import auth_tokens
import pytest
from config import settings
from models import AuditLog, CompanyMember, RefreshToken, SessionFamily
from services.platform import sessions
from sqlmodel import select
from sso_fake_idp import CLIENT_ID, ISSUER, company, member, sign_in


@pytest.fixture
def browser(test_client, fake_idp):
    """The test client over https, so Secure cookies come back as a browser
    would send them; ``fake_idp`` turns authentication on with a key."""
    test_client.base_url = "https://testserver"
    test_client.cookies.clear()
    return test_client


def _login(client, email):
    return client.post(
        "/v1/auth/login", json={"email": email, "password": "correct-password"}
    )


def _cookies(response) -> dict[str, str]:
    """Each Set-Cookie header by cookie name, lower-cased."""
    return {
        header.split("=", 1)[0]: header.lower()
        for header in response.headers.get_list("set-cookie")
    }


def _csrf(client) -> dict[str, str]:
    return {"X-Fabrique-CSRF": client.cookies.get("fabrique_csrf", path="/")}


def _family(session, client) -> SessionFamily:
    token = client.cookies.get("fabrique_session", path="/")
    sid = auth_tokens.decode_access_token(token)["sid"]
    session.expire_all()
    return session.exec(
        select(SessionFamily).where(SessionFamily.public_id == sid)
    ).one()


def _jar_set(client, name, value, path):
    """Replace a cookie in the client's jar, as a browser holding it would."""
    client.cookies.set(name, value, domain="testserver.local", path=path)


def _refresh(client, csrf=True):
    return client.post("/v1/auth/refresh", headers=_csrf(client) if csrf else {})


def test_login_sets_httponly_strict_cookies_and_no_token_in_the_body(
    sqlite_session, browser
):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    response = _login(browser, user.email)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "access_token" not in body and body["token_type"] == "cookie"
    assert body["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 == 900
    assert body["session_expires_in"] > 6 * 24 * 3600
    assert response.headers["cache-control"] == "no-store"
    cookies = _cookies(response)
    session_cookie = cookies["fabrique_session"]
    for flag in ("httponly", "secure", "samesite=strict", "path=/;"):
        assert flag in session_cookie + ";", (flag, session_cookie)
    refresh_cookie = cookies["fabrique_refresh"]
    for flag in ("httponly", "secure", "samesite=strict", "path=/v1/auth"):
        assert flag in refresh_cookie, (flag, refresh_cookie)
    csrf_cookie = cookies["fabrique_csrf"]
    assert "httponly" not in csrf_cookie and "samesite=strict" in csrf_cookie
    assert body["csrf_token"] == browser.cookies.get("fabrique_csrf", path="/")
    # Only digests are stored.
    family = _family(sqlite_session, browser)
    assert family.method == "password" and family.user_id == user.id
    raw = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    assert raw not in {family.csrf_hash, family.public_id}
    assert sqlite_session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == sessions.digest(raw))
    ).one()
    me = browser.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == user.email


def test_secure_can_be_relaxed_for_plain_http_development(
    sqlite_session, browser, monkeypatch
):
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", False)
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    cookies = _cookies(_login(browser, user.email))
    assert "secure" not in cookies["fabrique_session"]
    assert "httponly" in cookies["fabrique_session"]


def test_unsafe_cookie_requests_need_the_csrf_header(sqlite_session, browser):
    acme = company(sqlite_session)
    owner, _ = member(sqlite_session, acme.id, "Owner")
    guest, _ = member(sqlite_session, None, "Viewer")
    _login(browser, owner.email)
    invite = {"company_id": acme.id, "email": guest.email, "human_role": "Viewer"}
    missing = browser.post("/v1/company/invite", json=invite)
    assert missing.status_code == 403 and "CSRF" in missing.json()["detail"]
    # Tells a page that could not read the cookie to ask /v1/auth/csrf once.
    assert missing.headers["x-fabrique-csrf"] == "required"
    wrong = browser.post(
        "/v1/company/invite",
        json=invite,
        headers={"X-Fabrique-CSRF": secrets.token_urlsafe(32)},
    )
    assert wrong.status_code == 403
    ok = browser.post("/v1/company/invite", json=invite, headers=_csrf(browser))
    assert ok.status_code == 200, ok.text


def test_a_planted_csrf_cookie_does_not_pass(sqlite_session, browser):
    """Header and cookie agree but are not the session's: the signed digest
    in the access token refuses them (a sibling host tossing a cookie)."""
    acme = company(sqlite_session)
    owner, _ = member(sqlite_session, acme.id, "Owner")
    guest, _ = member(sqlite_session, None, "Viewer")
    _login(browser, owner.email)
    planted = secrets.token_urlsafe(32)
    _jar_set(browser, "fabrique_csrf", planted, "/")
    response = browser.post(
        "/v1/company/invite",
        json={"company_id": acme.id, "email": guest.email, "human_role": "Viewer"},
        headers={"X-Fabrique-CSRF": planted},
    )
    assert response.status_code == 403


def test_bearer_requests_skip_csrf_and_win_over_the_cookie(sqlite_session, browser):
    acme = company(sqlite_session)
    owner, bearer = member(sqlite_session, acme.id, "Owner")
    first, _ = member(sqlite_session, None, "Viewer")
    second, _ = member(sqlite_session, None, "Viewer")
    # Bearer alone: no CSRF header, as before.
    alone = browser.post(
        "/v1/company/invite",
        json={"company_id": acme.id, "email": first.email, "human_role": "Viewer"},
        headers=bearer,
    )
    assert alone.status_code == 200, alone.text
    # Both: treated as Bearer, so the missing CSRF header does not matter...
    _login(browser, owner.email)
    both = browser.post(
        "/v1/company/invite",
        json={"company_id": acme.id, "email": second.email, "human_role": "Viewer"},
        headers=bearer,
    )
    assert both.status_code == 200, both.text
    # ...and a bad Bearer is refused even beside a good cookie.
    bad = browser.get(
        "/v1/auth/me", headers={"Authorization": "Bearer " + secrets.token_urlsafe(16)}
    )
    assert bad.status_code == 401


def test_api_tokens_keep_working_without_csrf(sqlite_session, browser):
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    created = browser.post(
        "/v1/auth/tokens",
        json={"company_id": acme.id, "name": "ci", "role": "Viewer"},
        headers=owner,
    )
    assert created.status_code == 200, created.text
    raw = created.json()["token"]
    listed = browser.get(
        f"/v1/auth/tokens?company_id={acme.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert listed.status_code in (200, 403)  # resolved as the token, not refused as 401


def test_a_bearer_login_starts_no_family(sqlite_session, browser):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    before = len(sqlite_session.exec(select(SessionFamily)).all())
    response = browser.post(
        "/v1/auth/login",
        json={
            "email": user.email,
            "password": "correct-password",
            "token_delivery": "bearer",
        },
    )
    assert response.status_code == 200 and "set-cookie" not in response.headers
    claims = auth_tokens.decode_access_token(response.json()["access_token"])
    assert "sid" not in claims
    assert len(sqlite_session.exec(select(SessionFamily)).all()) == before


def test_refresh_rotates_the_token(sqlite_session, browser):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    old = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    assert _refresh(browser, csrf=False).status_code == 403
    response = _refresh(browser)
    assert response.status_code == 200, response.text
    assert "access_token" not in response.json()
    new = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    assert new != old
    sqlite_session.expire_all()
    used = sqlite_session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == sessions.digest(old))
    ).one()
    assert used.used_at is not None
    assert browser.get("/v1/auth/me").status_code == 200


def test_a_reused_refresh_token_revokes_the_family(
    sqlite_session, browser, monkeypatch
):
    monkeypatch.setattr(settings, "REFRESH_REUSE_GRACE_SECONDS", -1)
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    stolen = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    assert _refresh(browser).status_code == 200  # the owner rotates
    fresh = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    family = _family(sqlite_session, browser)
    _jar_set(browser, "fabrique_refresh", stolen, "/v1/auth")
    replay = _refresh(browser)
    assert replay.status_code == 401
    assert "fabrique_session" in _cookies(replay)  # cleared
    sqlite_session.refresh(family)
    assert family.revoked_at is not None and family.revoked_reason == "reuse"
    # The new token and every access token of the family die with it.
    with pytest.raises(sessions.SessionError):
        sessions.rotate(sqlite_session, fresh)
    access = auth_tokens.create_access_token(
        user_id=user.id, email=user.email, session_id=family.public_id
    )
    assert (
        browser.get("/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401


def test_two_tabs_refreshing_at_once_is_not_reuse(sqlite_session, browser):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    first_tab = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    assert _refresh(browser).status_code == 200
    rotated = browser.cookies.get("fabrique_refresh", path="/v1/auth")
    _jar_set(browser, "fabrique_refresh", first_tab, "/v1/auth")
    race = _refresh(browser)
    assert race.status_code == 409
    assert "set-cookie" not in race.headers  # nothing issued, nothing cleared
    family = _family(sqlite_session, browser)
    assert family.revoked_at is None
    _jar_set(browser, "fabrique_refresh", rotated, "/v1/auth")
    assert _refresh(browser).status_code == 200


def test_a_family_ends_at_its_absolute_expiry(sqlite_session, browser):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    family = _family(sqlite_session, browser)
    family.expires_at = sessions._now() - timedelta(seconds=1)
    sqlite_session.add(family)
    sqlite_session.commit()
    assert browser.get("/v1/auth/me").status_code == 401
    assert _refresh(browser).status_code == 401


def test_logout_revokes_the_family_and_clears_the_cookies(sqlite_session, browser):
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    family = _family(sqlite_session, browser)
    access = browser.cookies.get("fabrique_session", path="/")
    assert browser.post("/v1/auth/logout").status_code == 403  # no CSRF header
    response = browser.post("/v1/auth/logout", headers=_csrf(browser))
    assert response.status_code == 204
    cleared = _cookies(response)
    assert {"fabrique_session", "fabrique_refresh", "fabrique_csrf"} <= set(cleared)
    assert all("max-age=0" in value for value in cleared.values())
    sqlite_session.refresh(family)
    assert family.revoked_reason == "logout"
    # The access token stops at once, not when it expires.
    assert (
        browser.get("/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401


def test_the_refresh_route_is_rate_limited(sqlite_session, browser, monkeypatch):
    monkeypatch.setattr(settings, "REFRESH_RATE_LIMIT", "2/minute")
    codes = [browser.post("/v1/auth/refresh").status_code for _ in range(3)]
    assert codes[-1] == 429


def test_the_sso_exchange_sets_the_session_cookies(sqlite_session, browser, fake_idp):
    from sso_fake_idp import connect

    conn = connect(sqlite_session, company(sqlite_session).id)
    tag = secrets.token_hex(4)
    fake_idp.claims = {"email": f"s-{tag}@acme.test", "sub": f"subject-{tag}"}
    landed = sign_in(browser, fake_idp, conn.slug)
    response = browser.post("/v1/auth/sso/exchange", json={"code": landed["code"]})
    assert response.status_code == 200, response.text
    assert "access_token" not in response.json()
    cookies = _cookies(response)
    assert "httponly" in cookies["fabrique_session"]
    assert "samesite=strict" in cookies["fabrique_refresh"]
    assert _family(sqlite_session, browser).method == "sso"
    assert browser.get("/v1/auth/me").status_code == 200


def _sso_body(slug: str, domain: str) -> dict:
    return {
        "slug": slug,
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": secrets.token_urlsafe(24),
        "allowed_domains": [domain],
        "enabled": True,
    }


def test_requiring_sso_ends_password_sessions(sqlite_session, browser, fake_idp):
    acme = company(sqlite_session)
    tag = secrets.token_hex(4)
    domain = f"d{tag}.test"
    owner, owner_bearer = member(
        sqlite_session, acme.id, "Owner", email=f"owner@{domain}"
    )
    viewer, _ = member(sqlite_session, acme.id, "Viewer")
    outsider, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    body = _sso_body(f"req-{tag}", domain)
    url = f"/v1/companies/{acme.id}/sso"
    assert browser.put(url, json=body, headers=owner_bearer).status_code == 200
    fake_idp.claims = {"email": owner.email, "sub": f"subject-owner-{tag}"}
    sso_owner = sign_in(browser, fake_idp, body["slug"])
    browser.post("/v1/auth/sso/exchange", json={"code": sso_owner["code"]})
    owner_family = _family(sqlite_session, browser)
    browser.cookies.clear()
    _login(browser, viewer.email)
    viewer_family = _family(sqlite_session, browser)
    browser.cookies.clear()
    _login(browser, outsider.email)
    outsider_family = _family(sqlite_session, browser)
    browser.cookies.clear()

    required = browser.put(
        url, json={**body, "require_sso": True}, headers=owner_bearer
    )
    assert required.status_code == 200, required.text
    for family in (owner_family, viewer_family, outsider_family):
        sqlite_session.refresh(family)
    assert viewer_family.revoked_reason == "sso_required"
    assert owner_family.revoked_at is None  # started through SSO
    assert outsider_family.revoked_at is None  # another company
    row = sqlite_session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == acme.id)
        .where(AuditLog.action == "sso_connection_updated")
        .order_by(AuditLog.id.desc())
    ).first()
    assert row.result["sessions_ended"] == 1


def test_removing_a_member_ends_their_sessions(sqlite_session, browser):
    acme = company(sqlite_session)
    owner, owner_bearer = member(sqlite_session, acme.id, "Owner")
    viewer, viewer_bearer = member(sqlite_session, acme.id, "Viewer")
    _login(browser, viewer.email)
    family = _family(sqlite_session, browser)
    url = f"/v1/company/{acme.id}/members"
    assert browser.delete(f"{url}/{owner.id}", headers=viewer_bearer).status_code == 403
    last = browser.delete(f"{url}/{owner.id}", headers=owner_bearer)
    assert last.status_code == 409  # the last Owner stays
    assert browser.delete(f"{url}/{viewer.id}", headers=owner_bearer).status_code == 204
    sqlite_session.refresh(family)
    assert family.revoked_reason == "member_removed"
    sqlite_session.expire_all()
    assert (
        sqlite_session.exec(
            select(CompanyMember).where(
                CompanyMember.company_id == acme.id,
                CompanyMember.user_id == viewer.id,
            )
        ).first()
        is None
    )
    assert browser.get("/v1/auth/me").status_code == 401
    assert browser.delete(f"{url}/{viewer.id}", headers=owner_bearer).status_code == 404


def test_the_csrf_route_echoes_the_cookie_for_a_page_on_another_host(
    sqlite_session, browser
):
    assert browser.get("/v1/auth/csrf").json() == {"csrf_token": None}
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    body = _login(browser, user.email).json()
    assert browser.get("/v1/auth/csrf").json() == {"csrf_token": body["csrf_token"]}


def test_the_glassroom_ticket_is_issued_on_the_cookie(sqlite_session, browser):
    from models import Sprint

    acme = company(sqlite_session)
    viewer, _ = member(sqlite_session, acme.id, "Viewer")
    sprint = Sprint(company_id=acme.id, sprint_name="s", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    _login(browser, viewer.email)
    ticket = browser.post(
        f"/v1/sprints/{sprint.id}/stream_ticket", headers=_csrf(browser)
    )
    assert ticket.status_code == 200, ticket.text
    assert (
        auth_tokens.decode_stream_ticket(ticket.json()["ticket"], sprint.id)
        == viewer.id
    )


def test_rotating_the_signing_key_ends_every_family(
    sqlite_session, browser, monkeypatch
):
    """Refresh tokens are stored as HMAC under JWT_SECRET_KEY: after a
    rotation they match nothing, so a session cannot outlive the key."""
    user, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    _login(browser, user.email)
    rotated = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: rotated)
    assert browser.get("/v1/auth/me").status_code == 401
    assert _refresh(browser).status_code == 401
