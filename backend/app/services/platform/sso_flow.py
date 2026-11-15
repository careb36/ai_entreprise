"""The SSO sign-in: start, callback, handoff, and the password policy.

``start`` finds the company's connection (by its slug or an email domain),
stores a one-time ``state`` (hashed) with the PKCE verifier, the nonce and a
hash of a browser binding the router puts in an HttpOnly cookie, and returns
the provider's authorization URL. ``complete`` consumes that state exactly
once, checks the binding, exchanges the code, has ``oidc`` validate the ID
token, resolves the person and returns a one-time handoff code the frontend
exchanges for the same access token the password login issues.

Identity is ``(issuer, sub)``, never the email (after AixleHQ/flow's
identity resolver, AD-3): the email links an existing account once, only in
an allowed domain and only when the account belongs to this company alone
(``resolve_identity`` says why). The role always comes from the company's
membership, never from a claim; a new member gets the connection's default
role, never Owner. Each completed callback writes one audit row with what
the provider asserted and the outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from models import CompanyMember, SsoConnection, SsoFlowState, SsoIdentity, User
from sqlalchemy import delete, func
from sqlmodel import Session, select

from services.platform import oidc, sso_connections
from services.platform.agent_service import AgentService

STATE_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(seconds=60)
SCOPES = "openid email profile"
MAX_NAME_CHARS = 200


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _settings():
    from config import settings

    return settings


def find_connection(session: Session, key: str) -> SsoConnection:
    """The enabled connection a slug, an email or a domain names."""
    key = (key or "").strip().lower()
    if not key or len(key) > 254:
        raise oidc.SsoError("unknown_connection")
    row = session.exec(
        select(SsoConnection).where(
            SsoConnection.slug == key,
            SsoConnection.enabled == True,
        )
    ).first()
    if row is not None:
        return row
    domain = key.rsplit("@", 1)[-1]
    matches = [
        conn
        for conn in session.exec(
            select(SsoConnection).where(SsoConnection.enabled == True)
        ).all()
        if domain in (conn.allowed_domains or [])
    ]
    if len(matches) != 1:
        raise oidc.SsoError("unknown_connection")
    return matches[0]


def start(session: Session, key: str) -> tuple[str, str]:
    """The provider's authorization URL and the browser binding to set."""
    conn = find_connection(session, key)
    discovery = oidc.discover(conn.issuer)
    session.exec(delete(SsoFlowState).where(SsoFlowState.expires_at < _now()))
    state, nonce, binding = (secrets.token_urlsafe(32) for _ in range(3))
    verifier, challenge = oidc.pkce_pair()
    session.add(
        SsoFlowState(
            kind="login",
            key_hash=_hash(state),
            connection_id=conn.id,
            nonce=nonce,
            code_verifier=verifier,
            binding_hash=_hash(binding),
            expires_at=_now() + STATE_TTL,
        )
    )
    session.commit()
    endpoint = discovery["authorization_endpoint"]
    query = urlencode(
        {
            "response_type": "code",
            "client_id": conn.client_id,
            "redirect_uri": sso_connections.redirect_uri(),
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}", binding


def _consume(session: Session, kind: str, value: str | None) -> SsoFlowState | None:
    """The row for *value*, deleted: whoever deletes it is the only one to use it."""
    if not value or len(value) > 512:
        return None
    row = session.exec(
        select(SsoFlowState).where(
            SsoFlowState.kind == kind, SsoFlowState.key_hash == _hash(value)
        )
    ).first()
    if row is None:
        return None
    deleted = session.exec(delete(SsoFlowState).where(SsoFlowState.id == row.id))
    session.commit()
    return row if deleted.rowcount == 1 else None


def _audit(
    session: Session,
    conn: SsoConnection,
    outcome: str,
    claims: dict | None = None,
    user_id: int | None = None,
    **extra,
) -> None:
    observed = None
    if claims is not None:
        observed = {
            "issuer": claims.get("iss"),
            "subject": claims.get("sub"),
            "email": claims.get("email"),
            "email_verified": claims.get("email_verified"),
        }
    AgentService().audit_interaction(
        session=session,
        company_id=conn.company_id,
        interaction_id=f"sso:{conn.id}",
        agent_id=f"sso:{conn.issuer}",
        action="sso_sign_in",
        details={"connection_id": conn.id, "observed_claims": observed},
        result={"outcome": outcome, "user_id": user_id, **extra},
        decision_signature=None,
        cost_tokens=0,
    )


def complete(
    session: Session,
    *,
    state: str | None,
    code: str | None,
    error: str | None,
    binding: str | None,
) -> str:
    """The handoff code for a valid callback; ``SsoError`` otherwise."""
    if not state:
        raise oidc.SsoError("missing_state")
    row = _consume(session, "login", state)
    if row is None:
        raise oidc.SsoError("invalid_state", "state unknown or already used")
    if row.expires_at < _now():
        raise oidc.SsoError("state_expired")
    if not binding or not hmac.compare_digest(_hash(binding), row.binding_hash or ""):
        raise oidc.SsoError("state_mismatch", "state was issued to another browser")
    conn = session.get(SsoConnection, row.connection_id)
    if conn is None or not conn.enabled:
        raise oidc.SsoError("unknown_connection")
    claims = None
    try:
        if error:
            raise oidc.SsoError("idp_denied", "the provider returned an error")
        if not code:
            raise oidc.SsoError("missing_code")
        discovery = oidc.discover(conn.issuer)
        answer = oidc.exchange_code(
            discovery,
            code=code,
            redirect_uri=sso_connections.redirect_uri(),
            code_verifier=row.code_verifier or "",
            client_id=conn.client_id,
            client_secret=sso_connections.client_secret(conn),
        )
        claims = oidc.validate_id_token(
            answer["id_token"],
            discovery=discovery,
            client_id=conn.client_id,
            nonce=row.nonce or "",
        )
        user, outcome, cleared = resolve_identity(session, conn, claims)
    except oidc.SsoError as exc:
        session.rollback()
        _audit(session, conn, f"refused:{exc.code}", claims)
        raise
    handoff = secrets.token_urlsafe(32)
    session.add(
        SsoFlowState(
            kind="handoff",
            key_hash=_hash(handoff),
            user_id=user.id,
            connection_id=conn.id,
            expires_at=_now() + HANDOFF_TTL,
        )
    )
    _audit(session, conn, outcome, claims, user.id, password_cleared=cleared)
    return handoff


def _memberships(session: Session, user_id: int) -> dict[int, str]:
    rows = session.exec(
        select(CompanyMember.company_id, CompanyMember.human_role).where(
            CompanyMember.user_id == user_id
        )
    ).all()
    return {company_id: role for company_id, role in rows}


def resolve_identity(
    session: Session, conn: SsoConnection, claims: dict
) -> tuple[User, str, bool]:
    """The account behind validated *claims*, its outcome, whether its
    password was cleared. Nothing is written until every refusal is past.

    A company's provider is trusted only for that company: any Owner can
    point a connection at a provider they run and list any domain. So an
    existing account is linked by email only when it belongs to this company
    alone (an Owner invited it, and no other company would be reached
    through it), and it becomes SSO-only (its password cleared) unless it is
    an Owner here, whose password is the break-glass path. Auto-join creates
    new accounts; it never adopts an existing one.
    """
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise oidc.SsoError("email_missing", "the provider sent no email")
    email = email.strip().lower()
    if email.rsplit("@", 1)[1] not in (conn.allowed_domains or []):
        raise oidc.SsoError("domain_not_allowed")
    # The connection's issuer, which the token was validated against: one
    # spelling per provider even where it issues two (Google).
    issuer, subject = conn.issuer, str(claims["sub"])
    identity = session.exec(
        select(SsoIdentity).where(
            SsoIdentity.issuer == issuer, SsoIdentity.subject == subject
        )
    ).first()
    user = None
    if identity is not None:
        user = session.get(User, identity.user_id)
        if user is None:
            raise oidc.SsoError("account_missing")
        outcome = "signed_in"
    else:
        user = session.exec(select(User).where(func.lower(User.email) == email)).first()
        outcome = "linked" if user is not None else "created"
    roles = _memberships(session, user.id) if user is not None else {}
    member = conn.company_id in roles
    if outcome == "linked":
        if not member:
            raise oidc.SsoError(
                "account_exists",
                "an account with this email exists outside the company",
            )
        if set(roles) != {conn.company_id}:
            raise oidc.SsoError(
                "link_refused", "the account belongs to other companies too"
            )
        if session.exec(
            select(SsoIdentity.id).where(
                SsoIdentity.user_id == user.id, SsoIdentity.issuer == issuer
            )
        ).first():
            raise oidc.SsoError(
                "identity_conflict", "the account is linked to another subject"
            )
    if not member and not conn.auto_join:
        raise oidc.SsoError("not_a_member", "not a member and auto-join is off")

    cleared = False
    if user is None:
        name = claims.get("name")
        user = User(
            email=email,
            full_name=(name if isinstance(name, str) and name.strip() else email)[
                :MAX_NAME_CHARS
            ],
            password_hash="",  # SSO-only: no password verifies against it
        )
        session.add(user)
        session.flush()
    if identity is None:
        identity = SsoIdentity(
            user_id=user.id, connection_id=conn.id, issuer=issuer, subject=subject
        )
    if not member:
        session.add(
            CompanyMember(
                company_id=conn.company_id,
                user_id=user.id,
                human_role=conn.default_role,
            )
        )
    if outcome == "linked" and user.password_hash and roles[conn.company_id] != "Owner":
        user.password_hash = ""
        cleared = True
        session.add(user)
    identity.email = email
    identity.last_login_at = _now()
    session.add(identity)
    return user, outcome, cleared


def exchange_handoff(session: Session, code: str | None) -> User:
    """The account a one-time handoff code was issued to."""
    row = _consume(session, "handoff", code)
    if row is None or row.expires_at < _now() or row.user_id is None:
        raise oidc.SsoError("invalid_handoff")
    user = session.get(User, row.user_id)
    if user is None:
        raise oidc.SsoError("invalid_handoff")
    return user


def owner_has_signed_in(session: Session, conn: SsoConnection) -> bool:
    """Whether an Owner of the company has an identity through *conn*: the
    proof the connection works before the company may depend on it."""
    owners = select(CompanyMember.user_id).where(
        CompanyMember.company_id == conn.company_id,
        CompanyMember.human_role == "Owner",
    )
    return (
        session.exec(
            select(SsoIdentity.id).where(
                SsoIdentity.connection_id == conn.id,
                SsoIdentity.issuer == conn.issuer,
                SsoIdentity.user_id.in_(owners),
            )
        ).first()
        is not None
    )


def _break_glass(user: User) -> bool:
    listed = {
        e.strip().lower()
        for e in str(_settings().SSO_BREAK_GLASS_EMAILS).split(",")
        if e.strip()
    }
    return (user.email or "").lower() in listed


def check_password_login(session: Session, user: User) -> None:
    """Refuse a password sign-in for a member of a company that requires SSO,
    unless the operator listed this Owner for break-glass; audit that use."""
    rows = session.exec(
        select(SsoConnection, CompanyMember.human_role)
        .join(CompanyMember, CompanyMember.company_id == SsoConnection.company_id)
        .where(
            CompanyMember.user_id == user.id,
            SsoConnection.enabled == True,
            SsoConnection.require_sso == True,
        )
    ).all()
    if not rows:
        return
    if not _break_glass(user) or any(role != "Owner" for _, role in rows):
        raise oidc.SsoError("sso_required")
    for conn, _ in rows:
        AgentService().audit_interaction(
            session=session,
            company_id=conn.company_id,
            interaction_id=f"sso:{conn.id}",
            agent_id=f"human:{user.email}",
            action="sso_break_glass_login",
            details={"connection_id": conn.id, "user_id": user.id},
            result={"outcome": "password_sign_in_allowed"},
            decision_signature=None,
            cost_tokens=0,
        )
