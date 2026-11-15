"""The ID token is judged by us (``services/platform/oidc.py``; docs/design/SSO.md,
T1, T9): the signature against the issuer's JWKS with an asymmetric algorithm,
``iss``, ``aud``/``azp``, ``exp`` with a small skew, the nonce we issued and
``email_verified`` when present; discovery must name the configured issuer;
an issuer must be https on a public host. The provider is a fake served
through an injected transport (``sso_fake_idp.py``), keys generated here."""

from __future__ import annotations

import secrets
import time

import jwt
import pytest
from config import settings
from services.platform import oidc
from sso_fake_idp import CLIENT_ID, ISSUER, new_rsa_key, s256


def _validate(idp, token, nonce="nonce-1"):
    return oidc.validate_id_token(
        token, discovery=oidc.discover(ISSUER), client_id=CLIENT_ID, nonce=nonce
    )


def test_a_valid_token_passes_and_its_claims_come_back(fake_idp):
    fake_idp.nonce = "nonce-1"
    claims = _validate(fake_idp, fake_idp.id_token())
    assert claims["sub"] == "subject-1" and claims["email"] == "ana@acme.test"


def test_a_token_signed_by_another_key_is_refused(fake_idp):
    fake_idp.nonce = "nonce-1"
    fake_idp.sign_key = new_rsa_key()
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, fake_idp.id_token())
    assert err.value.code == "invalid_token"


@pytest.mark.parametrize(
    "overrides",
    [{"aud": "another-client"}, {"iss": "https://evil.test"}],
    ids=["wrong-aud", "wrong-iss"],
)
def test_a_token_for_another_client_or_issuer_is_refused(fake_idp, overrides):
    fake_idp.nonce = "nonce-1"
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, fake_idp.id_token(**overrides))
    assert err.value.code == "invalid_token"


def test_several_audiences_need_azp_to_name_this_client(fake_idp):
    fake_idp.nonce = "nonce-1"
    token = fake_idp.id_token(aud=[CLIENT_ID, "other"])
    with pytest.raises(oidc.SsoError):
        _validate(fake_idp, token)
    assert _validate(
        fake_idp, fake_idp.id_token(aud=[CLIENT_ID, "other"], azp=CLIENT_ID)
    )


def test_an_expired_token_is_refused_beyond_the_skew(fake_idp):
    fake_idp.nonce = "nonce-1"
    now = int(time.time())
    skew = settings.SSO_CLOCK_SKEW_SECONDS
    assert _validate(fake_idp, fake_idp.id_token(exp=now - skew // 2))
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, fake_idp.id_token(exp=now - skew - 30, iat=now - 600))
    assert err.value.code == "token_expired"


def test_hs256_and_none_are_never_accepted(fake_idp):
    fake_idp.nonce = "nonce-1"
    secret = secrets.token_bytes(32)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "nonce": "nonce-1"},
        secret,
        algorithm="HS256",
        headers={"kid": fake_idp.kid},
    )
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, forged)
    assert err.value.code == "invalid_token"
    unsigned = jwt.encode({"iss": ISSUER, "aud": CLIENT_ID}, None, algorithm="none")
    with pytest.raises(oidc.SsoError):
        _validate(fake_idp, unsigned)


def test_a_nonce_other_than_ours_is_refused(fake_idp):
    fake_idp.nonce = "someone-elses"
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, fake_idp.id_token(), nonce="nonce-1")
    assert err.value.code == "invalid_nonce"
    fake_idp.nonce = None
    with pytest.raises(oidc.SsoError):
        _validate(fake_idp, fake_idp.id_token(), nonce="nonce-1")


def test_an_email_the_provider_has_not_verified_is_refused(fake_idp):
    fake_idp.nonce = "nonce-1"
    with pytest.raises(oidc.SsoError) as err:
        _validate(fake_idp, fake_idp.id_token(email_verified=False))
    assert err.value.code == "email_unverified"
    # Absent (Entra does not send it): accepted; the issuer is the guarantee.
    fake_idp.claims = {"email_verified": None}
    assert _validate(fake_idp, fake_idp.id_token())


def test_a_rotated_key_is_fetched_again(fake_idp):
    fake_idp.nonce = "nonce-1"
    _validate(fake_idp, fake_idp.id_token())
    # The provider rotates: a new key and kid the cache has not seen.
    fake_idp.key = fake_idp.sign_key = new_rsa_key()
    fake_idp.kid = "key-2"
    oidc._jwks_cache[f"{ISSUER}/jwks"] = (time.monotonic() - 120, {"keys": []})
    assert _validate(fake_idp, fake_idp.id_token())


def test_discovery_must_name_the_configured_issuer(fake_idp):
    fake_idp.discovery_overrides = {"issuer": "https://other.test"}
    with pytest.raises(oidc.SsoError) as err:
        oidc.discover(ISSUER, fresh=True)
    assert err.value.code == "idp_misconfigured"


def test_discovery_without_pkce_s256_is_refused(fake_idp):
    fake_idp.discovery_overrides = {"code_challenge_methods_supported": ["plain"]}
    with pytest.raises(oidc.SsoError):
        oidc.discover(ISSUER, fresh=True)


def test_an_issuer_must_be_https_on_a_public_host(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_ISSUERS", False)
    for url in (
        "http://idp.example.com",
        "https://user:pw@idp.example.com",
        "https://127.0.0.1",
        "https://localhost",
    ):
        with pytest.raises(oidc.SsoError) as err:
            oidc.check_url(url)
        assert err.value.code == "invalid_config"


def test_the_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = oidc.pkce_pair()
    assert len(verifier) >= 43 and challenge == s256(verifier)


def test_the_adapter_reads_a_bounded_body_and_follows_no_redirect(monkeypatch):
    import httpx
    from egress_fakes import FakeResolver
    from services.platform import egress, oidc_client

    # idp.test resolves to a public address (the adapter pins it).
    monkeypatch.setattr(egress, "RESOLVER", FakeResolver())

    huge = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * (oidc_client.MAX_BYTES + 10))
    )
    with pytest.raises(oidc_client.OidcHttpError, match="too large"):
        oidc_client.get_json("https://idp.test/jwks", 5, transport=huge)
    moved = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "http://10.0.0.1/"})
    )
    with pytest.raises(oidc_client.OidcHttpError) as err:
        oidc_client.get_json("https://idp.test/jwks", 5, transport=moved)
    assert err.value.status == 302
    # A provider's error body is reduced to its error code, never echoed.
    failing = httpx.MockTransport(
        lambda request: httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "secret stuff"}
        )
    )
    with pytest.raises(oidc_client.OidcHttpError) as err:
        oidc_client.post_form("https://idp.test/token", {}, 5, transport=failing)
    assert str(err.value) == "HTTP 400: invalid_grant"
