"""A fake OpenID Connect provider for the SSO tests: no network.

The RSA key is generated when the test runs; discovery, the JWKS and the
token endpoint are served through an ``httpx.MockTransport`` that the
``fake_idp`` fixture (``conftest.py``, through ``serve_fake_idp``) installs
as ``oidc.HTTP_TRANSPORT``. The token endpoint
answers with an ID token built from ``claims`` (overridable per test), signed
with ``sign_key`` (the provider's own key unless a test swaps it).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import auth_tokens
import httpx
import jwt
from config import settings
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from models import Company, CompanyMember, SsoConnection, SsoDomain, User
from services.platform import oidc

ISSUER = "https://idp.test"
CLIENT_ID = "test-client"


def new_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeIdp:
    def __init__(self):
        self.key = new_rsa_key()
        self.sign_key = self.key
        self.kid = "key-1"
        self.alg = "RS256"
        self.nonce: str | None = None
        self.claims: dict = {}
        self.discovery_overrides: dict = {}
        self.token_requests: list[dict] = []
        self.requests: list[str] = []

    def jwk(self, key=None, kid=None) -> dict:
        data = json.loads(RSAAlgorithm.to_jwk((key or self.key).public_key()))
        data.update(kid=kid or self.kid, use="sig", alg="RS256")
        return data

    def discovery(self) -> dict:
        doc = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }
        doc.update(self.discovery_overrides)
        return doc

    def id_token(self, **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "subject-1",
            "email": "ana@acme.test",
            "email_verified": True,
            "name": "Ana Example",
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
        }
        claims.update(self.claims)
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(
            claims, self.sign_key, algorithm=self.alg, headers={"kid": self.kid}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery())
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if path == "/token" and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            form["authorization"] = request.headers.get("authorization")
            self.token_requests.append(form)
            return httpx.Response(200, json={"id_token": self.id_token()})
        return httpx.Response(404, json={"error": "not_found"})


def serve_fake_idp(monkeypatch):
    """The ``fake_idp`` fixture's body: ``conftest.py`` imports this module
    only for the tests that ask for the fixture."""
    idp = FakeIdp()
    monkeypatch.setattr(oidc, "HTTP_TRANSPORT", httpx.MockTransport(idp.handler))
    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_ISSUERS", True)
    monkeypatch.setattr(settings, "SSO_RATE_LIMIT", "1000/minute")
    monkeypatch.setattr(settings, "SSO_BREAK_GLASS_EMAILS", "")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    oidc.clear_caches()
    yield idp
    oidc.clear_caches()


def company(session) -> Company:
    row = Company(company_name=f"SSO {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def connect(
    session, company_id: int, verified: bool = True, **overrides
) -> SsoConnection:
    """A connection; its domains verified by DNS unless *verified* is false."""
    values = {
        "slug": f"acme-{secrets.token_hex(3)}",
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "allowed_domains": ["acme.test"],
        "auto_join": True,
        "enabled": True,
    }
    values.update(overrides)
    row = SsoConnection(company_id=company_id, **values)
    session.add(row)
    session.commit()
    if verified:
        verify_domains(session, company_id)
    return row


def verify_domains(session, company_id: int) -> None:
    """Record the company's allowed domains as verified by DNS, as a check
    that found the expected TXT record would (``sso_domains.check``)."""
    from datetime import UTC, datetime

    from services.platform import sso_domains
    from sqlmodel import select

    conn = session.exec(
        select(SsoConnection).where(SsoConnection.company_id == company_id)
    ).one()
    sso_domains.sync(session, company_id, list(conn.allowed_domains or []))
    now = datetime.now(UTC).replace(tzinfo=None)
    for row in session.exec(
        select(SsoDomain).where(SsoDomain.company_id == company_id)
    ).all():
        row.status, row.verified_at, row.last_checked_at = "verified", now, now
        row.last_outcome = "verified"
        session.add(row)
    session.commit()


def member(session, company_id: int | None, role: str, email: str | None = None):
    """A user (with a password hash) and their bearer header."""
    from passwords import hash_password

    user = User(
        email=email or f"{role.lower()}-{secrets.token_hex(4)}@acme.test",
        full_name=role,
        password_hash=hash_password("correct-password"),
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


def start(client, idp: FakeIdp, key: str) -> dict:
    """Begin a sign-in: the provider's query parameters, the nonce recorded."""
    response = client.get(f"/v1/auth/sso/{key}/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    params = {k: v[0] for k, v in parse_qs(location.query).items()}
    if "nonce" in params:
        idp.nonce = params["nonce"]
    params["_location"] = response.headers["location"]
    return params


def callback(client, **params) -> dict:
    """The provider's redirect back: the frontend's query parameters."""
    response = client.get(
        "/v1/auth/sso/callback", params=params, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert location.path == "/login/sso"
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def sign_in(client, idp: FakeIdp, key: str) -> dict:
    params = start(client, idp, key)
    return callback(client, state=params["state"], code="code-from-idp")


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
