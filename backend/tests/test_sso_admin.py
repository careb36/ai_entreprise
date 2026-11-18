"""A company's SSO connection, managed by its Owners (``routers/sso_admin.py``;
docs/design/SSO.md, T5–T8, T11, T12, T14, T19): only a signed-in human Owner
reads or changes it; the client secret is encrypted and never returned; the
issuer, domains and default role are checked (never Owner, never a public mail
domain); Google and Entra are presets of the issuer; the test reports what the
provider answered; requiring SSO waits for an Owner's real sign-in, then
blocks password login except the operator's break-glass Owners; every change
is audited without the secret."""

from __future__ import annotations

import secrets

from config import settings
from models import AuditLog, SsoConnection
from services.platform import api_tokens
from sqlmodel import select
from sso_fake_idp import (
    CLIENT_ID,
    ISSUER,
    company,
    connect,
    member,
    sign_in,
    verify_domains,
)


def _url(company_id, suffix=""):
    return f"/v1/companies/{company_id}/sso{suffix}"


def _body(**overrides):
    body = {
        "slug": f"acme-{secrets.token_hex(3)}",
        "preset": "generic",
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": secrets.token_urlsafe(24),  # generated: no literal secret
        # A fresh domain per call: a domain names one company's connection.
        "allowed_domains": [f"d{secrets.token_hex(4)}.test"],
        "default_role": "Viewer",
        "auto_join": True,
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_an_owner_connects_and_the_secret_never_comes_back(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "FERNET_SECRET_KEY", Fernet.generate_key().decode())
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    body = _body()
    created = test_client.put(_url(acme.id), json=body, headers=owner)
    assert created.status_code == 200, created.text
    assert created.json()["has_client_secret"] is True
    assert body["client_secret"] not in created.text
    row = sqlite_session.exec(
        select(SsoConnection).where(SsoConnection.company_id == acme.id)
    ).one()
    assert row.client_secret.startswith("fernet:")
    assert body["client_secret"] not in row.client_secret
    read = test_client.get(_url(acme.id), headers=owner)
    assert read.status_code == 200 and body["client_secret"] not in read.text
    assert read.json()["redirect_uri"] == settings.SSO_CALLBACK_URL
    tested = test_client.post(_url(acme.id, "/test"), headers=owner)
    assert tested.status_code == 200 and body["client_secret"] not in tested.text
    # An omitted secret keeps the stored one.
    kept = test_client.put(
        _url(acme.id), json={**body, "client_secret": None}, headers=owner
    )
    assert kept.json()["has_client_secret"] is True
    audits = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == acme.id, AuditLog.action.like("sso_connection_%")
        )
    ).all()
    assert [a.action for a in audits] == [
        "sso_connection_created",
        "sso_connection_updated",
    ]
    assert "client_secret" in audits[0].result["changed"]
    assert all(body["client_secret"] not in str(a.result) for a in audits)


def test_only_a_signed_in_owner_reaches_the_settings(
    sqlite_session, test_client, fake_idp
):
    acme, other = company(sqlite_session), company(sqlite_session)
    _, manager = member(sqlite_session, acme.id, "Manager")
    _, viewer = member(sqlite_session, acme.id, "Viewer")
    _, outsider = member(sqlite_session, other.id, "Owner")
    for headers in (manager, viewer, outsider):
        assert (
            test_client.put(_url(acme.id), json=_body(), headers=headers).status_code
            == 403
        )
        assert test_client.get(_url(acme.id), headers=headers).status_code == 403
    assert test_client.get(_url(acme.id)).status_code == 401
    _, raw = api_tokens.create_token(sqlite_session, acme.id, "ci", role="Manager")
    token = sqlite_session.exec(select(api_tokens.ApiToken)).all()[-1]
    token.role = "Owner"  # even a token carrying the Owner role is refused
    sqlite_session.add(token)
    sqlite_session.commit()
    response = test_client.get(
        _url(acme.id), headers={"Authorization": f"Bearer {raw}"}
    )
    assert response.status_code == 403


def test_the_configuration_is_checked(sqlite_session, test_client, fake_idp):
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    for bad in (
        {"default_role": "Owner"},
        {"allowed_domains": ["gmail.com"]},
        {"allowed_domains": []},
        {"issuer": "http://idp.test"},
        {"slug": "Not A Slug"},
        {"preset": "entra", "tenant": "contoso.onmicrosoft.com"},
        {"preset": "unknown"},
    ):
        response = test_client.put(_url(acme.id), json=_body(**bad), headers=owner)
        assert response.status_code == 422, (bad, response.text)


def test_google_and_entra_are_presets_of_the_issuer(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    google = test_client.put(
        _url(acme.id), json=_body(preset="google", issuer=None), headers=owner
    )
    assert google.json()["issuer"] == "https://accounts.google.com"
    tenant = "00000000-0000-4000-8000-000000000000"
    entra = test_client.put(
        _url(acme.id),
        json=_body(preset="entra", tenant=tenant, issuer=None),
        headers=owner,
    )
    assert entra.json()["issuer"] == f"https://login.microsoftonline.com/{tenant}/v2.0"


def test_a_slug_belongs_to_one_company(sqlite_session, test_client, fake_idp):
    acme, other = company(sqlite_session), company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    _, other_owner = member(sqlite_session, other.id, "Owner")
    assert (
        test_client.put(
            _url(acme.id), json=_body(slug="taken"), headers=owner
        ).status_code
        == 200
    )
    response = test_client.put(
        _url(other.id), json=_body(slug="taken"), headers=other_owner
    )
    assert response.status_code == 409


def test_the_test_reports_what_the_provider_answered(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    test_client.put(_url(acme.id), json=_body(), headers=owner)
    ok = test_client.post(_url(acme.id, "/test"), headers=owner).json()
    assert ok["ok"] is True and ok["observed"]["key_count"] == 1
    assert ok["observed"]["token_endpoint"] == f"{ISSUER}/token"
    assert ok["connection"]["test_ok"] is True
    fake_idp.discovery_overrides = {"issuer": "https://elsewhere.test"}
    bad = test_client.post(_url(acme.id, "/test"), headers=owner).json()
    assert bad["ok"] is False and "issuer" in bad["detail"]


def test_requiring_sso_waits_for_an_owners_sign_in(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    body = _body(slug=f"lock-{secrets.token_hex(3)}")
    domain = body["allowed_domains"][0]
    owner_user, owner = member(sqlite_session, acme.id, "Owner", email=f"own@{domain}")
    refused = test_client.put(
        _url(acme.id), json={**body, "require_sso": True}, headers=owner
    )
    assert refused.status_code == 409 and "Owner" in refused.json()["detail"]
    assert test_client.put(_url(acme.id), json=body, headers=owner).status_code == 200
    verify_domains(sqlite_session, acme.id)  # the domain's TXT record was found
    # The Owner signs in through the connection: now it is proven to work.
    fake_idp.claims = {"email": owner_user.email, "sub": "subject-owner"}
    assert "code" in sign_in(test_client, fake_idp, body["slug"])
    required = test_client.put(
        _url(acme.id), json={**body, "require_sso": True}, headers=owner
    )
    assert required.status_code == 200 and required.json()["require_sso"] is True
    sqlite_session.refresh(owner_user)
    assert owner_user.password_hash  # an Owner keeps the break-glass password
    # Changing the issuer voids the proof.
    google = {**body, "require_sso": True, "preset": "google", "issuer": None}
    assert test_client.put(_url(acme.id), json=google, headers=owner).status_code == 409


def _require_sso(session, company_id):
    return connect(session, company_id, require_sso=True)


def _password_login(client, email):
    return client.post(
        "/v1/auth/login", json={"email": email, "password": "correct-password"}
    )


def test_requiring_sso_blocks_password_login(sqlite_session, test_client, fake_idp):
    acme = company(sqlite_session)
    viewer, _ = member(sqlite_session, acme.id, "Viewer")
    outsider, _ = member(sqlite_session, company(sqlite_session).id, "Viewer")
    assert _password_login(test_client, viewer.email).status_code == 200
    _require_sso(sqlite_session, acme.id)
    blocked = _password_login(test_client, viewer.email)
    assert blocked.status_code == 403 and "single sign-on" in blocked.json()["detail"]
    # A wrong password still says only that the credentials are wrong.
    wrong = test_client.post(
        "/v1/auth/login", json={"email": viewer.email, "password": "wrong-password"}
    )
    assert wrong.status_code == 401
    assert _password_login(test_client, outsider.email).status_code == 200


def test_break_glass_is_for_listed_owners_and_is_audited(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    acme = company(sqlite_session)
    owner, _ = member(sqlite_session, acme.id, "Owner")
    manager, _ = member(sqlite_session, acme.id, "Manager")
    _require_sso(sqlite_session, acme.id)
    assert _password_login(test_client, owner.email).status_code == 403
    monkeypatch.setattr(
        settings, "SSO_BREAK_GLASS_EMAILS", f"{owner.email},{manager.email}"
    )
    assert _password_login(test_client, owner.email).status_code == 200
    # Listed, but not an Owner: refused.
    assert _password_login(test_client, manager.email).status_code == 403
    (row,) = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == acme.id, AuditLog.action == "sso_break_glass_login"
        )
    ).all()
    assert row.details["user_id"] == owner.id


def test_a_disabled_connection_does_not_require_sso(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    viewer, _ = member(sqlite_session, acme.id, "Viewer")
    connect(sqlite_session, acme.id, require_sso=True, enabled=False)
    assert _password_login(test_client, viewer.email).status_code == 200


def test_deleting_the_connection_is_audited(sqlite_session, test_client, fake_idp):
    acme = company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    test_client.put(_url(acme.id), json=_body(), headers=owner)
    assert test_client.delete(_url(acme.id), headers=owner).status_code == 204
    assert test_client.get(_url(acme.id), headers=owner).json()["connection"] is None
    assert test_client.delete(_url(acme.id), headers=owner).status_code == 404
    actions = sqlite_session.exec(
        select(AuditLog.action).where(AuditLog.company_id == acme.id)
    ).all()
    assert "sso_connection_deleted" in actions


def test_a_domain_verified_by_one_company_is_held_by_it(
    sqlite_session, test_client, fake_idp
):
    acme, other = company(sqlite_session), company(sqlite_session)
    _, owner = member(sqlite_session, acme.id, "Owner")
    _, other_owner = member(sqlite_session, other.id, "Owner")
    body = _body()
    assert test_client.put(_url(acme.id), json=body, headers=owner).status_code == 200
    squat = _body(allowed_domains=["unique-x.test", body["allowed_domains"][0]])
    # Unverified, the first listing holds nothing (docs/design/SSO.md, T19).
    assert (
        test_client.put(_url(other.id), json=squat, headers=other_owner).status_code
        == 200
    )
    verify_domains(sqlite_session, acme.id)
    response = test_client.put(_url(other.id), json=squat, headers=other_owner)
    assert response.status_code == 409
    assert "verified by another company" in response.json()["detail"]
