"""OpenID Connect, judged by us (single sign-on, ``docs/archive/design/SSO.md``).

Discovery (``/.well-known/openid-configuration``) and the JWKS are fetched
through ``oidc_client`` and cached; the ID token is validated here, never
taken on the provider's word: the signature against the issuer's JWKS with an
asymmetric algorithm only (no ``none``, no HS256 confusion), ``iss``, ``aud``
(and ``azp`` when there are several audiences), ``exp`` and ``iat`` with
``SSO_CLOCK_SKEW_SECONDS``, ``sub``, the ``nonce`` this backend issued, and
``email_verified`` when the provider sends it. What passes is an observation
of the provider: who the person is there, nothing about what they may do here.

A failure is ``SsoError`` with a short ``code`` the frontend translates and
the audit row records; the message is for the logs and the Owner's test.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import jwt

from services.platform import egress, oidc_client

# The httpx transport every request uses; the tests put a fake IdP here.
HTTP_TRANSPORT: Any = None

ALLOWED_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
)
DISCOVERY_TTL_SECONDS = 3600
JWKS_TTL_SECONDS = 3600
JWKS_REFRESH_MIN_SECONDS = 60
MAX_URL_CHARS = 2000
GOOGLE_ISSUER = "https://accounts.google.com"

_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class SsoError(Exception):
    """A sign-in or a test that cannot go on; ``code`` names why."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _settings():
    from config import settings

    return settings


_PRIVATE = "the host resolves to a private or unreachable address"


def _allow_private() -> bool:
    return bool(_settings().SSO_ALLOW_PRIVATE_ISSUERS)


def _public_address(host: str) -> bool:
    return egress.all_public(host)


def check_url(url: str, *, field: str = "issuer", resolve: bool = True) -> str:
    """*url* as an address of the provider, or ``SsoError("invalid_config")``.

    https only, no credentials, and (unless ``SSO_ALLOW_PRIVATE_ISSUERS``) a
    host that resolves only to public addresses: an Owner's issuer must not
    turn the backend into a reader of its own network.
    """
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_CHARS or any(ch.isspace() for ch in url):
        raise SsoError("invalid_config", f"{field}: not a URL")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SsoError("invalid_config", f"{field}: must be an https URL")
    if parts.username or parts.password or parts.fragment:
        raise SsoError("invalid_config", f"{field}: no credentials or fragment")
    if resolve and not _allow_private() and not _public_address(parts.hostname):
        raise SsoError(
            "invalid_config",
            f"{field}: the host resolves to a private or unreachable address "
            "(set SSO_ALLOW_PRIVATE_ISSUERS to allow it)",
        )
    return url


def _get(url: str) -> dict[str, Any]:
    timeout = float(_settings().SSO_HTTP_TIMEOUT_SECONDS)
    try:
        return oidc_client.get_json(
            url, timeout, transport=HTTP_TRANSPORT, allow_private=_allow_private()
        )
    except egress.EgressRefused as exc:
        raise SsoError("invalid_config", f"{url}: {_PRIVATE}") from exc
    except oidc_client.OidcUnreachable as exc:
        raise SsoError("idp_unreachable", f"{url}: unreachable ({exc})") from exc
    except oidc_client.OidcHttpError as exc:
        raise SsoError("idp_error", f"{url}: {exc}") from exc


def discover(issuer: str, *, fresh: bool = False) -> dict[str, Any]:
    """The issuer's discovery document, checked: its ``issuer`` is the one
    configured and every endpoint we call is https on an allowed host."""
    issuer = issuer.rstrip("/")
    cached = _discovery_cache.get(issuer)
    if cached and not fresh and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    # Checked again at every fetch, not only when saved: a name can resolve
    # elsewhere later.
    check_url(issuer, field="issuer")
    doc = _get(f"{issuer}/.well-known/openid-configuration")
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise SsoError(
            "idp_misconfigured", "discovery: its issuer is not the configured one"
        )
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(field), str):
            raise SsoError("idp_misconfigured", f"discovery: no {field}")
        check_url(doc[field], field=field)
    methods = doc.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" not in methods:
        raise SsoError("idp_misconfigured", "discovery: PKCE S256 not supported")
    _discovery_cache[issuer] = (time.monotonic(), doc)
    return doc


def jwks(jwks_uri: str, *, fresh: bool = False) -> dict[str, Any]:
    """The issuer's key set, cached for an hour; *fresh* refetches it, at
    most once a minute (a key rotated since the cache was filled)."""
    cached = _jwks_cache.get(jwks_uri)
    now = time.monotonic()
    if cached:
        age = now - cached[0]
        if age < JWKS_TTL_SECONDS and (not fresh or age < JWKS_REFRESH_MIN_SECONDS):
            return cached[1]
    doc = _get(jwks_uri)
    if not isinstance(doc.get("keys"), list):
        raise SsoError("idp_misconfigured", "jwks: no keys")
    _jwks_cache[jwks_uri] = (now, doc)
    return doc


def clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


def signing_algorithms(discovery: dict[str, Any]) -> list[str]:
    """The asymmetric algorithms both sides accept."""
    offered = discovery.get("id_token_signing_alg_values_supported")
    if not isinstance(offered, list):
        return ["RS256"]
    return [alg for alg in ALLOWED_ALGORITHMS if alg in offered]


def _key_for(token: str, jwks_uri: str, algorithms: list[str]):
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SsoError("invalid_token", "id_token: not a JWT") from exc
    alg = header.get("alg")
    if alg not in algorithms:
        raise SsoError("invalid_token", f"id_token: algorithm {alg!r} not accepted")
    kid = header.get("kid")
    for fresh in (False, True):
        keys = [
            k
            for k in jwks(jwks_uri, fresh=fresh)["keys"]
            if isinstance(k, dict) and k.get("use") in (None, "sig")
        ]
        matching = [k for k in keys if kid is None or k.get("kid") == kid]
        if kid is None and len(matching) != 1:
            matching = []
        if matching:
            try:
                return jwt.PyJWK(matching[0], algorithm=alg).key, alg
            except jwt.PyJWTError as exc:
                raise SsoError("invalid_token", "id_token: unusable key") from exc
    raise SsoError("invalid_token", "id_token: no key in the JWKS matches")


def validate_id_token(
    token: str,
    *,
    discovery: dict[str, Any],
    client_id: str,
    nonce: str,
) -> dict[str, Any]:
    """The ID token's claims, once every check above has passed."""
    key, alg = _key_for(token, discovery["jwks_uri"], signing_algorithms(discovery))
    issuers = [discovery["issuer"]]
    if discovery["issuer"] == GOOGLE_ISSUER:
        issuers.append("accounts.google.com")  # Google issues both forms
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=client_id,
            issuer=issuers,
            leeway=int(_settings().SSO_CLOCK_SKEW_SECONDS),
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise SsoError("token_expired", "id_token: expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise SsoError("invalid_token", "id_token: wrong audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise SsoError("invalid_token", "id_token: wrong issuer") from exc
    except jwt.PyJWTError as exc:
        raise SsoError("invalid_token", f"id_token: {type(exc).__name__}") from exc
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise SsoError("invalid_token", "id_token: azp is not this client")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(
        claims["nonce"], nonce
    ):
        raise SsoError("invalid_nonce", "id_token: nonce does not match")
    verified = claims.get("email_verified")
    if verified is not None and verified not in (True, "true"):
        raise SsoError("email_unverified", "the provider has not verified the email")
    return claims


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def exchange_code(
    discovery: dict[str, Any],
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str,
    client_secret: str | None,
) -> dict[str, Any]:
    """The token endpoint's answer for *code*; it must hold an ``id_token``."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id,
    }
    methods = discovery.get("token_endpoint_auth_methods_supported")
    auth = None
    if client_secret:
        if isinstance(methods, list) and "client_secret_basic" not in methods:
            data["client_secret"] = client_secret
        else:
            auth = (client_id, client_secret)
    timeout = float(_settings().SSO_HTTP_TIMEOUT_SECONDS)
    try:
        answer = oidc_client.post_form(
            discovery["token_endpoint"],
            data,
            timeout,
            auth=auth,
            transport=HTTP_TRANSPORT,
            allow_private=_allow_private(),
        )
    except egress.EgressRefused as exc:
        raise SsoError("invalid_config", f"token endpoint: {_PRIVATE}") from exc
    except oidc_client.OidcUnreachable as exc:
        raise SsoError("idp_unreachable", f"token endpoint: {exc}") from exc
    except oidc_client.OidcHttpError as exc:
        raise SsoError("idp_error", f"token endpoint: {exc}") from exc
    if not isinstance(answer.get("id_token"), str):
        raise SsoError("idp_error", "token endpoint: no id_token")
    return answer
