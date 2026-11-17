"""Access tokens and stream tickets name their issuer and audience, and only
an HMAC algorithm is accepted (issue #541; WSTG-SESS-10 at latest@87cf4a4).

Negative cases: no ``iss``/``aud`` (a token from before the change), a wrong
``aud`` or ``iss``, ``alg: none``, an RS256 token (algorithm confusion), a
configuration that names ``none`` or an asymmetric algorithm, and a stream
ticket and an access token swapped for each other.
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import jwt
import pytest
from config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from models import User


@pytest.fixture
def key(monkeypatch):
    value = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: value)
    return value


def _claims(**extra):
    now = datetime.now(UTC)
    claims = {
        "sub": "1",
        "email": "jwt@example.com",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "typ": "access",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    claims.update(extra)
    return {k: v for k, v in claims.items() if v is not None}


def _b64(data: dict) -> str:
    raw = json.dumps(data, default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_an_access_token_names_its_issuer_and_audience(key):
    token = auth_tokens.create_access_token(user_id=7, email="a@example.com")
    claims = auth_tokens.decode_access_token(token)
    assert claims["iss"] == settings.JWT_ISSUER == "la-fabrique"
    assert claims["aud"] == settings.JWT_AUDIENCE == "la-fabrique-api"


def test_the_well_formed_token_is_accepted(key):
    token = jwt.encode(_claims(), key, algorithm="HS256")
    assert auth_tokens.decode_access_token(token)["sub"] == "1"


@pytest.mark.parametrize(
    "change",
    [
        {"iss": None, "aud": None},  # issued before the change
        {"aud": None},
        {"iss": None},
        {"aud": "another-service"},
        {"iss": "https://someone-else.example"},
        {"aud": "la-fabrique-api:glassroom"},  # a stream ticket's audience
    ],
)
def test_a_token_with_a_missing_or_wrong_issuer_or_audience_is_refused(key, change):
    token = jwt.encode(_claims(**change), key, algorithm="HS256")
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(token)


def test_an_unsigned_token_is_refused(key):
    header = _b64({"alg": "none", "typ": "JWT"})
    token = f"{header}.{_b64(_claims())}."
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(token)
    unsigned = jwt.encode(_claims(), None, algorithm="none")
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(unsigned)


def test_an_rs256_token_is_refused(key):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), private, algorithm="RS256")
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(token)


def test_an_hs256_token_signed_with_a_public_key_is_refused(key):
    """The classic confusion: a token HMAC-signed with a public key the
    attacker knows. Only the configured secret verifies."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # PyJWT refuses to HMAC with a PEM key, so the token is built by hand.
    signing_input = f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(_claims())}"
    mac = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(token)


@pytest.mark.parametrize("algorithm", ["none", "RS256", "ES256", ""])
def test_a_configuration_naming_another_algorithm_refuses_to_sign_or_verify(
    key, monkeypatch, algorithm
):
    token = auth_tokens.create_access_token(user_id=1, email="a@example.com")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", algorithm)
    with pytest.raises(auth_tokens.AuthNotConfiguredError):
        auth_tokens.create_access_token(user_id=1, email="a@example.com")
    with pytest.raises(auth_tokens.AuthNotConfiguredError):
        auth_tokens.decode_access_token(token)
    assert auth_tokens.session_id_of(token) is None


def test_a_session_id_is_read_only_from_a_token_for_this_audience(key):
    good = jwt.encode(_claims(sid="family-1"), key, algorithm="HS256")
    other = jwt.encode(_claims(sid="family-1", aud="elsewhere"), key, algorithm="HS256")
    assert auth_tokens.session_id_of(good) == "family-1"
    assert auth_tokens.session_id_of(other) is None


def test_a_stream_ticket_and_an_access_token_are_not_interchangeable(key):
    ticket = auth_tokens.create_stream_ticket(user_id=3, sprint_id=9)
    assert auth_tokens.decode_stream_ticket(ticket, 9) == 3
    assert jwt.decode(ticket, options={"verify_signature": False})["aud"] == (
        "la-fabrique-api:glassroom"
    )
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_access_token(ticket)
    access = auth_tokens.create_access_token(user_id=3, email="a@example.com")
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_stream_ticket(access, 9)
    # A ticket from before the change (no iss/aud) is refused.
    old = jwt.encode(
        {
            "sub": "3",
            "sprint": 9,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "typ": "glassroom",
        },
        key,
        algorithm="HS256",
    )
    with pytest.raises(auth_tokens.InvalidTokenError):
        auth_tokens.decode_stream_ticket(old, 9)


def test_a_bearer_token_without_audience_gets_401(
    test_client, sqlite_session, key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    user = User(
        email=f"jwt-{secrets.token_hex(4)}@example.com",
        full_name="J",
        password_hash="x",
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    legacy = jwt.encode(
        _claims(sub=str(user.id), iss=None, aud=None), key, algorithm="HS256"
    )
    fresh = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    assert (
        test_client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}
        ).status_code
        == 401
    )
    assert (
        test_client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"}
        ).status_code
        == 200
    )
