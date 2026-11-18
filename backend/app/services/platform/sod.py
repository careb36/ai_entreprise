"""Segregation of duties: who asked for a change does not approve it (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

The rule is one pure function, ``decide``: an approval by *actor* of work that
*requesters* asked for or wrote is refused when the actor is one of them, for
the actions the company's mode holds:

- ``high_risk`` (the default): accepting a held delivery (the risk hold, from
  the web or a chat button), approving a specification, releasing (a local
  merge of a run or of a sprint's integration into the base branch);
- ``all``: those, and approving another producer's run and an architecture;
- ``off``: none (turning it off is an Owner's audited choice).

The single-person exception is explicit, never a silent bypass: an Owner
grants it with a reason (audited), and it is honoured only while the company
has exactly one person who can approve (one member whose effective role is
Manager or Owner). Each use is an audit row, ``sod_exception_applied``; each
refusal is one too, ``sod_refused``, committed before the refusal is raised.

Who requested the work is found by the caller from records, never claims
(the sprint's ``started_by_user_id``, a specification's ``drafted_by``);
this module knows nothing of tasks or runs. A person is matched by account
id, else by email; an API token acts for the person who created it, so a
token minted to approve one's own work is refused like its creator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models import ApiToken, SodPolicy, User
from sqlmodel import Session, select

from services.platform import teams
from services.platform.agent_service import AgentService

MODES = ("off", "high_risk", "all")
DEFAULT_MODE = "high_risk"
ACCEPT_HELD = "accept_held_delivery"
APPROVE_SPEC = "approve_specification"
RELEASE = "release"
APPROVE_RUN = "approve_run"
APPROVE_ARCH = "approve_architecture"
HIGH_RISK_ACTIONS = frozenset({ACCEPT_HELD, APPROVE_SPEC, RELEASE})
ALL_ACTIONS = HIGH_RISK_ACTIONS | {APPROVE_RUN, APPROVE_ARCH}
REASON_MAX = 500
INTERVAL_RANGE = (7, 366)
INACTIVE_RANGE = (7, 730)


@dataclass(frozen=True)
class Person:
    """An account, by id and email; either may be unknown."""

    user_id: int | None = None
    email: str | None = None
    label: str = ""
    token_id: int | None = None

    def known(self) -> bool:
        return self.user_id is not None or bool(self.email)


@dataclass(frozen=True)
class Requester:
    person: Person
    why: str  # "started the sprint", "drafted the specification"


@dataclass
class Decision:
    outcome: str  # "not_held" | "separated" | "exception" | "refused" | "unattributed"
    conflicts: list[Requester] = field(default_factory=list)


class SodRefused(Exception):
    """The approval is refused by the rule; ``status`` 403 and a ``detail``."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.status = 403
        self.detail = detail


def same_person(a: Person, b: Person) -> bool:
    """Pure: the same account, by id when both have one, else by email."""
    if a.user_id is not None and b.user_id is not None:
        return a.user_id == b.user_id
    if a.email and b.email:
        return a.email.strip().casefold() == b.email.strip().casefold()
    return False


def held(mode: str, action: str) -> bool:
    """Pure: whether *mode* holds *action* to the rule."""
    if mode == "all":
        return action in ALL_ACTIONS
    if mode == "off":
        return False
    return action in HIGH_RISK_ACTIONS  # high_risk, and any unknown mode


def decide(
    mode: str,
    action: str,
    actor: Person | None,
    requesters: list[Requester],
    solo_exception: bool,
) -> Decision:
    """Pure: what the rule says of *actor* approving work *requesters* asked for."""
    if not held(mode, action):
        return Decision("not_held")
    known = [r for r in requesters if r.person.known()]
    if actor is None or not actor.known() or not known:
        return Decision("unattributed")
    conflicts = [r for r in known if same_person(actor, r.person)]
    if not conflicts:
        return Decision("separated")
    if solo_exception:
        return Decision("exception", conflicts)
    return Decision("refused", conflicts)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def policy_of(session: Session, company_id: int) -> SodPolicy:
    """The company's policy row, or an unsaved one with the defaults."""
    row = session.exec(
        select(SodPolicy).where(SodPolicy.company_id == company_id)
    ).first()
    return row or SodPolicy(company_id=company_id, mode=DEFAULT_MODE)


def approvers(session: Session, company_id: int) -> dict[int, str]:
    """``{user_id: effective role}`` of the people who can approve (Manager+)."""
    return teams.members_with_role(session, company_id, "Manager")


def solo_eligible(session: Session, company_id: int) -> bool:
    """Exactly one person can approve: the single-person exception may apply."""
    return len(approvers(session, company_id)) == 1


def actor_of(session: Session, user: User | None) -> Person | None:
    """The person behind *user*: an API token stands for its creator.

    The creator is the account ``created_by_user_id`` names (issue #512), as
    in ``api_tokens.creator_role``: an address that changed or passed to
    another account matters not. An id with no account stays that id and is
    never resolved by email (``api_tokens.verify`` refuses such a token
    before it gets here). Only a token without the id, one migration 0048
    could not match, is found by its email."""
    if user is None:
        return None
    token_company = getattr(user, "_api_token_company_id", None)
    if token_company is None:
        return Person(user.id, user.email, user.full_name or user.email)
    token = session.get(ApiToken, -(user.id or 0))
    if token is not None and token.created_by_user_id is not None:
        label = f"API token {token.name}"
        account = session.get(User, token.created_by_user_id)
        if account is None:
            return Person(token.created_by_user_id, None, label, token.id)
        return Person(account.id, account.email, label, token.id)
    creator = (token.created_by or "") if token else ""
    owner = (
        session.exec(select(User).where(User.email == creator)).first()
        if creator
        else None
    )
    return Person(
        owner.id if owner else None,
        creator or None,
        f"API token {token.name if token else ''}".strip(),
        token.id if token else None,
    )


def person_of_user_id(session: Session, user_id: int | None) -> Person:
    if user_id is None:
        return Person()
    user = session.get(User, user_id)
    if user is None:
        return Person(user_id)
    return Person(user.id, user.email, user.full_name or user.email)


def person_of_email(session: Session, value: str | None) -> Person:
    """A person named by email in a record (``drafted_by``); an agent's name
    (``PO``, ``Architect``) or a demo label is nobody."""
    if not value or "@" not in value:
        return Person()
    user = session.exec(select(User).where(User.email == value)).first()
    if user is None:
        return Person(None, value, value)
    return Person(user.id, user.email, user.full_name or user.email)


def _audit(session, company_id, actor, action, details, result) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"sod:{company_id}",
        agent_id=f"human:{(actor.email or actor.label) if actor else 'anonymous'}",
        action=action,
        details={"company_id": company_id, **details},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def enforce(
    session: Session,
    company_id: int,
    action: str,
    subject: dict[str, Any],
    actor: Person | None,
    requesters: list[Requester],
) -> str:
    """Apply the rule at an approval point; the outcome, or raise ``SodRefused``.

    A refusal is audited (the audit write commits) before it is raised, so the
    caller's rollback keeps it; call this before adding anything to the
    session. An exception's audit row is written the same way, before the
    approval: the report counts it only against an approval that was then
    recorded (``services/management/sod_report.py``).
    """
    policy = policy_of(session, company_id)
    solo = bool(policy.solo_exception) and solo_eligible(session, company_id)
    decision = decide(policy.mode, action, actor, requesters, solo)
    details = {
        "action": action,
        "subject": subject,
        "actor_user_id": actor.user_id if actor else None,
        "actor_token_id": actor.token_id if actor else None,
        "requesters": [{"user_id": r.person.user_id, "why": r.why} for r in requesters],
        "mode": policy.mode,
    }
    why = ", ".join(sorted({c.why for c in decision.conflicts}))
    if decision.outcome == "refused":
        _audit(
            session,
            company_id,
            actor,
            "sod_refused",
            details,
            {"outcome": "refused", "conflict": why},
        )
        session.commit()  # audit_interaction commits; kept explicit for clarity
        hint = (
            " The single-person exception is granted but the company has more "
            "than one person who can approve."
            if policy.solo_exception
            else ""
        )
        raise SodRefused(
            f"Segregation of duties: you {why}, so another person approves it.{hint}"
        )
    if decision.outcome == "exception":
        _audit(
            session,
            company_id,
            actor,
            "sod_exception_applied",
            details,
            {"outcome": "exception", "conflict": why, "reason": policy.solo_reason},
        )
    return decision.outcome


# ---------------------------------------------------------------------------
# The policy, written by an Owner
# ---------------------------------------------------------------------------


def serialize(session: Session, policy: SodPolicy) -> dict[str, Any]:
    eligible = solo_eligible(session, policy.company_id)
    return {
        "company_id": policy.company_id,
        "mode": policy.mode,
        "modes": list(MODES),
        "high_risk_actions": sorted(HIGH_RISK_ACTIONS),
        "all_actions": sorted(ALL_ACTIONS),
        "solo_exception": policy.solo_exception,
        "solo_reason": policy.solo_reason,
        "solo_granted_by": policy.solo_granted_by,
        "solo_granted_at": _iso(policy.solo_granted_at),
        "solo_eligible": eligible,
        "solo_in_force": bool(policy.solo_exception) and eligible,
        "approvers": len(approvers(session, policy.company_id)),
        "review_interval_days": policy.review_interval_days,
        "inactive_days": policy.inactive_days,
        "updated_by": policy.updated_by,
        "updated_at": _iso(policy.updated_at) if policy.id else None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class PolicyRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def update_policy(
    session: Session, company_id: int, by: str, changes: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Apply *changes* (mode, review_interval_days, inactive_days); what changed."""
    policy = policy_of(session, company_id)
    if "mode" in changes and changes["mode"] not in MODES:
        raise PolicyRefused(422, f"The mode is one of {', '.join(MODES)}.")
    for name, (low, high) in (
        ("review_interval_days", INTERVAL_RANGE),
        ("inactive_days", INACTIVE_RANGE),
    ):
        if name in changes and not low <= int(changes[name]) <= high:
            raise PolicyRefused(422, f"{name} is between {low} and {high}.")
    changed = {}
    for name in ("mode", "review_interval_days", "inactive_days"):
        if name in changes and changes[name] != getattr(policy, name):
            changed[name] = {"from": getattr(policy, name), "to": changes[name]}
            setattr(policy, name, changes[name])
    if changed or policy.id is None:
        policy.updated_by, policy.updated_at = by, _now()
        session.add(policy)
    return changed


def grant_solo(session: Session, company_id: int, by: str, reason: str) -> SodPolicy:
    clean = " ".join((reason or "").split())
    if not 10 <= len(clean) <= REASON_MAX:
        raise PolicyRefused(
            422, f"Say why in 10 to {REASON_MAX} characters: it is kept in the audit."
        )
    if not solo_eligible(session, company_id):
        raise PolicyRefused(
            409,
            "The exception is for a company with one person who can approve; "
            "this one has more, so another person approves.",
        )
    policy = policy_of(session, company_id)
    policy.solo_exception, policy.solo_reason = True, clean
    policy.solo_granted_by, policy.solo_granted_at = by, _now()
    policy.updated_by, policy.updated_at = by, _now()
    session.add(policy)
    return policy


def revoke_solo(session: Session, company_id: int, by: str) -> SodPolicy:
    policy = policy_of(session, company_id)
    policy.solo_exception = False
    policy.updated_by, policy.updated_at = by, _now()
    session.add(policy)
    return policy
