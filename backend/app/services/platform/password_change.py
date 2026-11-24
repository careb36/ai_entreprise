"""Change a password, by the person or by an operator (``docs/design/change-password.md``).

Two ways in, one set of rules:

- ``change_own`` — the signed-in person, from ``POST /v1/auth/password``. The
  current password is checked with bcrypt under the sign-in's per-account
  backoff (``login_throttle``: a lock refuses before bcrypt runs, a failure
  counts towards it). The new one follows ``check_new_password`` and must
  not verify against the stored hash.
- ``reset_by_operator`` — someone with a shell on the server, for a person
  who forgot the password (``backend/app/reset_password.py``). No current
  password; the new one follows the same rule.

Either way every live session family of the person is revoked, and the
change, the revocations and the audit rows are one commit: if the audit row
cannot be written, nothing changes. The audit row goes through
``AgentService`` (signed, and in each of the person's companies' hash chain;
a person in no company gets one signed row outside any chain). It carries
the user id and how many sessions ended, never a password or a hash.
"""

from __future__ import annotations

from dataclasses import dataclass

from models import CompanyMember, User
from passwords import (
    PasswordTooLong,
    PasswordTooShort,
    check_new_password,
    hash_password,
    verify_password,
)
from sqlmodel import Session, select

from services.contracts.emails import normalize_email
from services.platform import login_throttle, sessions
from services.platform.agent_service import AgentService

# The longest current password bcrypt is asked to check; longer is a failure
# (as in step_up.py), so a huge body costs nothing.
MAX_CURRENT = 1024


class PasswordChangeRefused(Exception):
    """Why a change was refused: ``code`` for the page, ``status`` for HTTP.

    ``no_password`` (an SSO-only account), ``too_short``, ``too_long``,
    ``same_password``, ``locked`` (``retry_after`` seconds),
    ``wrong_password``, ``unknown_user`` (operator only).
    """

    def __init__(self, code: str, message: str, status: int, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass
class Changed:
    user_id: int
    sessions_ended: int
    audit_rows: int


def _rule(new: str) -> None:
    try:
        check_new_password(new)
    except PasswordTooShort as exc:
        raise PasswordChangeRefused("too_short", str(exc), 422) from exc
    except PasswordTooLong as exc:
        raise PasswordChangeRefused("too_long", str(exc), 422) from exc


def _no_password() -> PasswordChangeRefused:
    return PasswordChangeRefused(
        "no_password",
        "This account signs in with single sign-on only: it has no password to change.",
        409,
    )


def _audit(session: Session, user: User, action: str, by: str, result: dict) -> int:
    """One audit row per company of *user* (each enters that company's
    chain), or one outside any chain when they have none; not committed."""
    companies = sorted(
        set(
            session.exec(
                select(CompanyMember.company_id).where(CompanyMember.user_id == user.id)
            ).all()
        )
    )
    for company_id in companies or [None]:
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"user:{user.id}",
            agent_id=by,
            action=action,
            details={"user_id": user.id},
            result=result,
            decision_signature=None,
            cost_tokens=0,
            commit=False,
        )
    return len(companies) or 1


def _apply(
    session: Session, user: User, new: str, action: str, by: str, extra: dict
) -> Changed:
    try:
        user.password_hash = hash_password(new)
        session.add(user)
        ended = sessions.revoke_users(session, [user.id], action, commit=False)
        rows = _audit(session, user, action, by, {"sessions_ended": ended, **extra})
        session.commit()
    except Exception:
        session.rollback()
        raise
    return Changed(user_id=user.id, sessions_ended=ended, audit_rows=rows)


def change_own(
    session: Session, user: User, current: str, new: str, *, reissue: bool
) -> Changed:
    """The person changes their own password; ``PasswordChangeRefused`` otherwise.

    *reissue* says whether the caller will start a new session for this
    browser once every family is revoked (recorded in the audit row).
    """
    if not user.password_hash:
        raise _no_password()
    _rule(new)
    wait = login_throttle.retry_after(user.email)
    if wait:
        raise PasswordChangeRefused(
            "locked", "Too many attempts: try again later.", 429, retry_after=wait
        )
    if len(current) > MAX_CURRENT or not verify_password(current, user.password_hash):
        login_throttle.failure(user.email)
        raise PasswordChangeRefused(
            "wrong_password", "The current password is not right.", 403
        )
    login_throttle.success(user.email)
    if verify_password(new, user.password_hash):
        raise PasswordChangeRefused(
            "same_password", "The new password must differ from the current one.", 422
        )
    return _apply(
        session,
        user,
        new,
        "password_changed",
        f"human:{user.email}",
        {"session_reissued": reissue},
    )


def reset_by_operator(session: Session, email: str, new: str) -> Changed:
    """An operator on the server sets a new password for *email*."""
    user = session.exec(
        select(User).where(User.email == normalize_email(email))
    ).first()
    if user is None:
        raise PasswordChangeRefused("unknown_user", "No account has this email.", 404)
    if not user.password_hash:
        raise _no_password()
    _rule(new)
    return _apply(session, user, new, "password_reset_by_operator", "operator:cli", {})
