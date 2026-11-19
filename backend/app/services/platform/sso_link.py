"""Linking an SSO identity from inside a signed-in session, and unlinking it.

The sign-in links an existing account by email only when it belongs to the
connection's company alone (``sso_flow.resolve_identity``, T15), so a person
in several companies could not link at all. Here the person proves both
sides at once: the account, by a live browser session, and the identity, by
signing in at the company's provider in that same browser.

``start`` (a signed-in member, cookie session only) stores a one-time
``state`` of kind ``link`` whose binding is the hash of a random browser
value (an HttpOnly cookie, as the sign-in's) joined to the session's public
id, with the PKCE verifier and nonce (``sso_flow.authorization_url``). The
provider's callback is the sign-in's; ``complete`` takes over for a link
state: the binding must match the cookie and one of the user's live
sessions (the session cookie is ``SameSite=Strict`` and never reaches the
callback, so the session is found from the user the state names), the code
is exchanged and the ID token validated as at sign-in, and ``link`` applies
the rules: the provider's email is the account's, its domain is allowed and
verified by DNS for this company (a legacy domain's grace does not count:
the single-company rule is not applied here, so only a proof will do), the
subject is nobody else's and the account has no other subject at this
issuer. The password is kept. Each attempt is an ``sso_identity_link``
audit row with what the provider asserted.

Unlinking: oneself only while another way to sign in remains (a password no
SSO requirement refuses, or another identity through an enabled
connection); an Owner for the identities of their own company's connection,
which also ends the person's SSO-started sessions. Each is an
``sso_identity_unlink`` audit row. Design: ``docs/design/SSO.md``.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from models import (
    Company,
    CompanyMember,
    SessionFamily,
    SsoConnection,
    SsoFlowState,
    SsoIdentity,
    User,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from services.platform import oidc, sessions, sso_domains, sso_flow
from services.platform.agent_service import AgentService


class LinkRefused(Exception):
    """A request the link routes refuse; ``status`` and ``code`` say why."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def binding_hash(binding: str, session_id: str) -> str:
    """What a link state stores: the browser's value joined to its session."""
    return hashlib.sha256(f"{binding}:{session_id}".encode()).hexdigest()


def _member(session: Session, company_id: int, user_id: int) -> bool:
    return (
        session.exec(
            select(CompanyMember.id).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == user_id,
            )
        ).first()
        is not None
    )


def _connection(session: Session, company_id: int) -> SsoConnection | None:
    return session.exec(
        select(SsoConnection).where(
            SsoConnection.company_id == company_id, SsoConnection.enabled == True
        )
    ).first()


def start(
    session: Session, user: User, company_id: int, session_id: str, binding: str
) -> str:
    """The provider's authorization URL for linking *user* at *company_id*'s
    connection; ``LinkRefused`` when the user may not."""
    if not _member(session, company_id, user.id):
        raise LinkRefused(403, "not_a_member")
    conn = _connection(session, company_id)
    if conn is None:
        raise LinkRefused(404, "no_connection")
    if sso_flow.linked_at(session, user.id, conn.issuer) is not None:
        raise LinkRefused(409, "already_linked")
    return sso_flow.authorization_url(
        session, conn, "link", binding_hash(binding, session_id), user_id=user.id
    )


def _bound(session: Session, row: SsoFlowState, binding: str | None) -> bool:
    """Whether *binding* and one of the state's user's live sessions are the
    ones the state was issued to."""
    if not binding or row.user_id is None or not row.binding_hash:
        return False
    live = session.exec(
        select(SessionFamily.public_id).where(
            SessionFamily.user_id == row.user_id,
            SessionFamily.revoked_at.is_(None),
            SessionFamily.expires_at > _now(),
        )
    ).all()
    return any(
        hmac.compare_digest(binding_hash(binding, public_id), row.binding_hash)
        for public_id in live
    )


def _audit(
    session: Session,
    conn: SsoConnection,
    user: User,
    outcome: str,
    claims: dict | None,
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
        agent_id=f"human:{user.email}",
        action="sso_identity_link",
        details={"connection_id": conn.id, "observed_claims": observed},
        result={"outcome": outcome, "user_id": user.id},
        decision_signature=None,
        cost_tokens=0,
    )


def complete(
    session: Session,
    row: SsoFlowState,
    *,
    code: str | None,
    error: str | None,
    binding: str | None,
) -> str:
    """Finish the consumed link *row*: ``linked`` or ``already_linked``;
    ``SsoError`` otherwise."""
    sso_flow.fresh(row)
    if not _bound(session, row, binding):
        raise oidc.SsoError(
            "state_mismatch", "state was issued to another browser or session"
        )
    conn = session.get(SsoConnection, row.connection_id)
    user = session.get(User, row.user_id)
    if conn is None or not conn.enabled or user is None:
        raise oidc.SsoError("unknown_connection")
    claims = None
    try:
        if not _member(session, conn.company_id, user.id):
            raise oidc.SsoError("not_a_member")
        claims = sso_flow.exchange_claims(conn, row, code, error)
        outcome = link(session, conn, user, claims)
        session.flush()
    except IntegrityError:
        # Another link won a unique index since the checks: of this subject
        # (``identity_taken``) or of this account at this provider.
        session.rollback()
        code = _race_code(session, conn, user, claims)
        _audit(session, conn, user, f"refused:{code}", claims)
        raise oidc.SsoError(code) from None
    except oidc.SsoError as exc:
        session.rollback()
        _audit(session, conn, user, f"refused:{exc.code}", claims)
        raise
    # The identity and its audit row are one commit: if the row cannot be
    # written, nothing is linked.
    try:
        _audit(session, conn, user, outcome, claims)
    except Exception:
        session.rollback()
        raise
    return outcome


def _race_code(
    session: Session, conn: SsoConnection, user: User, claims: dict | None
) -> str:
    subject = str((claims or {}).get("sub", ""))
    taken = session.exec(
        select(SsoIdentity.user_id).where(
            SsoIdentity.issuer == conn.issuer, SsoIdentity.subject == subject
        )
    ).first()
    return "identity_taken" if taken not in (None, user.id) else "identity_conflict"


def link(session: Session, conn: SsoConnection, user: User, claims: dict) -> str:
    """Link the validated *claims*' subject to *user*. Nothing is written
    until every refusal is past."""
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise oidc.SsoError("email_missing", "the provider sent no email")
    email = email.strip().lower()
    if email != (user.email or "").strip().lower():
        raise oidc.SsoError("email_mismatch", "the provider's email is not yours")
    domain = email.rsplit("@", 1)[1]
    if domain not in (conn.allowed_domains or []):
        raise oidc.SsoError("domain_not_allowed")
    record = sso_domains.find(session, conn.company_id, domain)
    if record is None or sso_domains.status(session, record) != "verified":
        raise oidc.SsoError(
            "domain_unverified", "the email's domain is not verified by DNS"
        )
    issuer, subject = conn.issuer, str(claims["sub"])
    identity = session.exec(
        select(SsoIdentity).where(
            SsoIdentity.issuer == issuer, SsoIdentity.subject == subject
        )
    ).first()
    if identity is not None:
        if identity.user_id != user.id:
            raise oidc.SsoError(
                "identity_taken", "this identity is linked to another account"
            )
        return "already_linked"
    if sso_flow.linked_at(session, user.id, issuer) is not None:
        raise oidc.SsoError(
            "identity_conflict", "the account is linked to another subject"
        )
    session.add(
        SsoIdentity(
            user_id=user.id,
            connection_id=conn.id,
            issuer=issuer,
            subject=subject,
            email=email,
        )
    )
    return "linked"


def _usable_identities(session: Session, user_id: int) -> list[SsoIdentity]:
    """The user's identities a sign-in can still use: their connection is
    enabled and still at the same issuer."""
    rows = session.exec(
        select(SsoIdentity, SsoConnection)
        .join(SsoConnection, SsoConnection.id == SsoIdentity.connection_id)
        .where(SsoIdentity.user_id == user_id, SsoConnection.enabled == True)
    ).all()
    return [identity for identity, conn in rows if identity.issuer == conn.issuer]


def other_sign_in(session: Session, user: User, identity: SsoIdentity) -> bool:
    """Whether *user* can still sign in without *identity*."""
    if any(i.id != identity.id for i in _usable_identities(session, user.id)):
        return True
    return bool(user.password_hash) and not sso_flow.password_refused(session, user)


def _company_of(session: Session, identity: SsoIdentity) -> int | None:
    conn = (
        session.get(SsoConnection, identity.connection_id)
        if identity.connection_id is not None
        else None
    )
    return conn.company_id if conn is not None else None


def unlink(
    session: Session,
    actor: User,
    identity_id: int,
    company_id: int | None = None,
) -> dict[str, Any]:
    """Remove an identity: *actor*'s own (``company_id`` None), or, for an
    Owner of *company_id* (checked by the router), one through that
    company's connection. ``LinkRefused`` otherwise."""
    identity = session.get(SsoIdentity, identity_id)
    if identity is None:
        raise LinkRefused(404, "not_found")
    owning = _company_of(session, identity)
    if company_id is None:
        if identity.user_id != actor.id:
            raise LinkRefused(404, "not_found")
    elif owning != company_id or not _member(session, company_id, identity.user_id):
        raise LinkRefused(404, "not_found")
    user = session.get(User, identity.user_id)
    if user is None:
        raise LinkRefused(404, "not_found")
    remaining = other_sign_in(session, user, identity)
    by = "self" if user.id == actor.id else "owner"
    if by == "self" and not remaining:
        raise LinkRefused(409, "last_sign_in_method")
    details = {
        "identity_id": identity.id,
        "connection_id": identity.connection_id,
        "issuer": identity.issuer,
        "subject": identity.subject,
    }
    # The deletion, the revoked sessions and the audit row are one commit
    # (the audit write's): if the row cannot be written, nothing changes.
    try:
        session.delete(identity)
        ended = 0
        if by == "owner":
            ended = sessions.revoke_users(
                session, [user.id], "sso_unlinked", method="sso", commit=False
            )
        AgentService().audit_interaction(
            session=session,
            company_id=owning,
            interaction_id=f"sso-identity:{details['identity_id']}",
            agent_id=f"human:{actor.email}",
            action="sso_identity_unlink",
            details=details,
            result={
                "by": by,
                "user_id": user.id,
                "other_sign_in": remaining,
                "sessions_ended": ended,
            },
            decision_signature=None,
            cost_tokens=0,
        )
    except Exception:
        session.rollback()
        raise
    return {"unlinked": True, "other_sign_in": remaining, "sessions_ended": ended}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def mine(session: Session, user: User) -> dict[str, Any]:
    """What the person's own page shows: their identities (never the subject),
    the companies they can still link and whether they have a password."""
    names = {
        c.id: c.company_name
        for c in session.exec(
            select(Company)
            .join(CompanyMember, CompanyMember.company_id == Company.id)
            .where(CompanyMember.user_id == user.id)
        ).all()
    }
    identities = session.exec(
        select(SsoIdentity).where(SsoIdentity.user_id == user.id)
    ).all()
    shown, issuers = [], set()
    for identity in identities:
        issuers.add(identity.issuer)
        company_id = _company_of(session, identity)
        shown.append(
            {
                "id": identity.id,
                "company_id": company_id,
                "company_name": names.get(company_id),
                "email": identity.email,
                "linked_at": _iso(identity.created_at),
                "last_login_at": _iso(identity.last_login_at),
            }
        )
    linkable = []
    for company_id, name in sorted(names.items()):
        conn = _connection(session, company_id)
        if conn is not None and conn.issuer not in issuers:
            linkable.append(
                {"company_id": company_id, "company_name": name, "preset": conn.preset}
            )
    return {
        "identities": shown,
        "linkable": linkable,
        "has_password": bool(user.password_hash),
    }


def of_company(session: Session, company_id: int) -> dict[str, Any]:
    """The identities through *company_id*'s connection, for its Owner."""
    conn = session.exec(
        select(SsoConnection).where(SsoConnection.company_id == company_id)
    ).first()
    if conn is None:
        return {"identities": []}
    rows = session.exec(
        select(SsoIdentity, User)
        .join(User, User.id == SsoIdentity.user_id)
        .where(SsoIdentity.connection_id == conn.id)
        .order_by(SsoIdentity.id)
    ).all()
    return {
        "identities": [
            {
                "id": identity.id,
                "user_id": user.id,
                "user_email": user.email,
                "email": identity.email,
                "linked_at": _iso(identity.created_at),
                "last_login_at": _iso(identity.last_login_at),
            }
            for identity, user in rows
            if _member(session, company_id, user.id)
        ]
    }
