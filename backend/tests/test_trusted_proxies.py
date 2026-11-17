"""The client's address behind a reverse proxy (``TRUSTED_PROXY_IPS``).

The per-address limits (slowapi, ``LOGIN_RATE_LIMIT``...) key on
``request.client``. Behind nginx or Traefik every request comes from the
proxy, so they would key on the proxy: one bucket for everybody.
``security.TrustedProxyMiddleware`` takes the client from
``X-Forwarded-For`` only when the connection comes from a listed proxy, and
then the right-most address that is not itself a listed proxy; otherwise the
header is ignored. Empty (the default) trusts nobody.
"""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from config import settings
from fastapi.testclient import TestClient

PROXY = "10.0.0.5"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _forwarded(trusted, peer, header):
    import security

    return security.forwarded_client(
        peer, header, security.parse_trusted_proxies(trusted)
    )


@pytest.mark.parametrize(
    "trusted, peer, header, expected",
    [
        # Nobody trusted (the default): the header is ignored.
        ("", PROXY, "198.51.100.7", PROXY),
        # A trusted proxy: its client.
        (PROXY, PROXY, "198.51.100.7", "198.51.100.7"),
        # A spoofed left part: the right-most untrusted address wins.
        (PROXY, PROXY, "203.0.113.9, 198.51.100.7", "198.51.100.7"),
        # Two proxies in a row, by network.
        ("10.0.0.0/8", PROXY, "198.51.100.7, 10.0.0.9", "198.51.100.7"),
        # Not from a trusted proxy: the header is the client's own claim.
        (PROXY, "192.0.2.44", "198.51.100.7", "192.0.2.44"),
        # No header from the proxy: the proxy itself.
        (PROXY, PROXY, None, PROXY),
        # A malformed entry: never trusted, the last proxy is kept.
        (PROXY, PROXY, "not-an-address", PROXY),
        ("10.0.0.0/8", PROXY, "junk, 10.0.0.9", "10.0.0.9"),
        # IPv6.
        ("2001:db8::1", "2001:db8::1", "2001:db8::42", "2001:db8::42"),
    ],
)
def test_the_client_address(trusted, peer, header, expected):
    assert _forwarded(trusted, peer, header) == expected


def test_a_malformed_setting_is_refused():
    import security

    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        security.parse_trusted_proxies("10.0.0.5, proxy.internal")


def _login(client, forwarded_for):
    return client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password-here"},
        headers={"X-Forwarded-For": forwarded_for},
    )


@pytest.fixture
def via_proxy(test_client, monkeypatch):
    """The app reached from the proxy's address."""
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT", "2/minute")
    from app.main import app

    # test_client ran the lifespan and holds the session override; this one
    # only changes the address the requests come from.
    return TestClient(app, raise_server_exceptions=False, client=(PROXY, 4000))


def test_behind_a_trusted_proxy_each_client_has_its_own_limit(via_proxy, monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", PROXY)
    codes = [_login(via_proxy, f"198.51.100.{n}").status_code for n in range(1, 5)]
    assert 429 not in codes
    # The same client still meets its own limit.
    same = [_login(via_proxy, "198.51.100.99").status_code for _ in range(3)]
    assert same[-1] == 429


def test_without_trusted_proxies_the_header_is_ignored(via_proxy, monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    codes = [_login(via_proxy, f"198.51.100.{n}").status_code for n in range(1, 4)]
    assert codes[-1] == 429


def test_the_setting_is_empty_by_default():
    from config import Settings

    assert Settings().TRUSTED_PROXY_IPS == ""
