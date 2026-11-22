"""Creation and validation of signed human-user access tokens.

Every token names who issued it and for whom (``iss``, ``aud``; issue #541,
WSTG-SESS-10 at latest@87cf4a4): access tokens are for ``JWT_AUDIENCE``, the
Glassroom stream tickets for ``<JWT_AUDIENCE>:glassroom``, both issued by
``JWT_ISSUER``, and validation requires both, so a token minted for another
service under a shared key, or a ticket presented as an access token, is
refused. Only an HMAC algorithm is accepted (``HMAC_ALGORITHMS``): a
configuration naming ``none`` or an asymmetric algorithm with the shared
secret refuses to sign or verify anything. A key shorter than the hash
(``MIN_KEY_BYTES``) counts as no key: login answers 503 until it is replaced.

Tokens issued before ``iss`` and ``aud`` existed are refused: an access token
lives ``ACCESS_TOKEN_EXPIRE_MINUTES`` and a browser session renews it from its
refresh cookie (which is not a JWT), so a browser does not notice; a script's
Bearer token from before the upgrade logs in again.
"""

from datetime import UTC, datetime, timedelta

import jwt

HMAC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
# A key at least as long as the hash's output (RFC 7518 §3.2): a shorter one
# can be guessed offline from a single token, and the guess forges any other
# (WSTG-SESS-10, weak HMAC keys). A shorter JWT_SECRET_KEY counts as unset.
MIN_KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}
GLASSROOM_AUDIENCE_SUFFIX = ":glassroom"


class AuthTokenError(Exception):
    """Base class for authentication-token failures."""


class AuthNotConfiguredError(AuthTokenError):
    """Raised when the JWT signing key is not configured."""


class InvalidTokenError(AuthTokenError):
    """Raised when a token is malformed, invalid, or expired."""


def _configured_key() -> str | None:
    """Return the configured JWT key, or ``None`` when unset, blank or too
    short for the algorithm (``MIN_KEY_BYTES``)."""
    from config import settings

    key = getattr(settings, "JWT_SECRET_KEY", None)
    if key is None:
        return None
    key = key.strip()
    if not key:
        return None
    algorithm = str(settings.JWT_ALGORITHM or "").strip()
    if len(key.encode("utf-8")) < MIN_KEY_BYTES.get(algorithm, 32):
        return None
    return key


def is_auth_configured() -> bool:
    """True when a JWT signing key is configured."""
    return _configured_key() is not None


def _algorithm() -> str:
    """The configured algorithm, which must be an HMAC one."""
    from config import settings

    algorithm = str(settings.JWT_ALGORITHM or "").strip()
    if algorithm not in HMAC_ALGORITHMS:
        raise AuthNotConfiguredError(
            f"JWT_ALGORITHM must be one of {sorted(HMAC_ALGORITHMS)}"
        )
    return algorithm


def _issuer() -> str:
    from config import settings

    return settings.JWT_ISSUER


def _audience(kind: str = "access") -> str:
    from config import settings

    if kind == "glassroom":
        return settings.JWT_AUDIENCE + GLASSROOM_AUDIENCE_SUFFIX
    return settings.JWT_AUDIENCE


def create_access_token(
    *,
    user_id: int,
    email: str,
    expires_delta: timedelta | None = None,
    session_id: str | None = None,
    csrf_hash: str | None = None,
) -> str:
    """Create a signed access token for a human user.

    A browser session's token carries its family's public id (``sid``) and
    the digest of its CSRF token (``csrf``); a token without ``sid`` is a
    Bearer token for a non-browser client (``docs/archive/design/SESSIONS.md``).
    """
    from config import settings

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")

    now = datetime.now(UTC)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    claims = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": expires_at,
        "typ": "access",
        "iss": _issuer(),
        "aud": _audience(),
    }
    if session_id is not None:
        claims["sid"] = session_id
        claims["csrf"] = csrf_hash or ""
    return jwt.encode(claims, key, algorithm=_algorithm())


def decode_access_token(token: str) -> dict:
    """Verify an access token's signature, expiration, and token type."""

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[_algorithm()],
            audience=_audience(),
            issuer=_issuer(),
            options={"require": ["sub", "exp", "typ", "iss", "aud"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidTokenError("Invalid or expired token") from exc

    if claims.get("typ") != "access":
        raise InvalidTokenError("Invalid token type")
    return claims


def session_id_of(token: str) -> str | None:
    """The ``sid`` of a correctly signed access token, even an expired one
    (logout ends the family it names); ``None`` for anything else."""

    key = _configured_key()
    if key is None or not token:
        return None
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[_algorithm()],
            audience=_audience(),
            issuer=_issuer(),
            options={"verify_exp": False, "require": ["sub", "typ", "sid"]},
        )
    except (AuthNotConfiguredError, jwt.PyJWTError, TypeError, ValueError):
        return None
    if claims.get("typ") != "access":
        return None
    sid = claims.get("sid")
    return sid if isinstance(sid, str) else None


STREAM_TICKET_MINUTES = 5


def create_stream_ticket(
    *, user_id: int, sprint_id: int, expires_delta: timedelta | None = None
) -> str:
    """A short-lived token that opens one sprint's Glassroom stream for *user_id*.

    A browser EventSource cannot send an Authorization header, so the stream
    takes this in its query string instead of the access token: it is good
    for one sprint and a few minutes, and membership is checked again when it
    is used.
    """

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "sprint": sprint_id,
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=STREAM_TICKET_MINUTES)),
        "typ": "glassroom",
        "iss": _issuer(),
        "aud": _audience("glassroom"),
    }
    return jwt.encode(claims, key, algorithm=_algorithm())


def decode_stream_ticket(ticket: str, sprint_id: int) -> int:
    """The user a valid ticket for *sprint_id* was issued to; raises otherwise."""

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")
    try:
        claims = jwt.decode(
            ticket,
            key,
            algorithms=[_algorithm()],
            audience=_audience("glassroom"),
            issuer=_issuer(),
            options={"require": ["sub", "exp", "typ", "sprint", "iss", "aud"]},
        )
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidTokenError("Invalid or expired stream ticket") from exc
    if claims.get("typ") != "glassroom" or claims.get("sprint") != sprint_id:
        raise InvalidTokenError("The ticket is not for this sprint's stream")
    return user_id
