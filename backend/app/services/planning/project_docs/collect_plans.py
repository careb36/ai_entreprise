"""Reading what the quality and communications management plans read into the snapshot (``collect``).

Two snapshot keys, both observations the backend makes itself:

- ``quality_basis``: the backend's own definitions of quality, read from the
  code that applies them, never from a document, a request or a model: the
  standards catalogue (``services/evidence/standards``), the quality areas a
  specification is expected to name (``standards.QUALITY_AREAS``), the checks
  whose refutation stops a delivery at the gate before QA
  (``services/evidence/delivery_gate``) and the risk tier the backend's
  classifier gives one path of each area (``services/evidence/risk.classify``,
  called here, so the tier shown is the one the code computes). Every entry
  carries the sha256 the backend computed over it (``digest``): a changed
  definition changes the digest and makes a compiled plan stale.
- ``communications``: the company's configuration of the channels the
  platform has, as stored: the notification webhook (``CompanyWebhook``: on or
  off and the events chosen; never its URL or secret), the Slack and Teams
  connections for approvals in chat (``ChatConnection``: on or off and the
  last test; never a workspace, channel, credential or key) with the number of
  members who linked a chat account (never who), the Manager policy that makes
  a company receive the client's brief, the deployment's mail settings that
  send it (only whether ``SMTP_HOST`` is set) and the audit anchors' settings
  (``AuditAnchorSettings``: on or off, cadence, whether the webhook carries
  them and whether a timestamp authority is set; never its URL or
  certificate). Delivery counters (``last_status``, the outbox rows) change at
  every post and are left out: the plan is about who is told what, and a
  delivery is not a change of plan.

Plain data only. The gate's table is read from ``delivery_gate``'s module
constant: the evidence is a protected path, so no public alias was added for
it there.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import settings
from models import ChatConnection, ChatIdentity, CompanyWebhook
from models.audit_anchor import AuditAnchorSettings
from models.manager import ManagerPolicy
from sqlalchemy import func
from sqlmodel import Session, select

from services.evidence import delivery_gate, risk, standards
from services.planning.project_docs.refs import digest
from services.platform.orchestration_metrics import GATE_UNCONFIRMED

# One path per area the classifier knows, and one it does not mark: the
# backend's answer for each is what the plan shows.
RISK_PROBES = (
    "src/auth/login.py",
    "config/secrets.yaml",
    "src/payments/charge.py",
    ".github/workflows/ci.yml",
    "scripts/deploy.sh",
    "migrations/0001_init.sql",
    "requirements.txt",
    "Dockerfile",
    "src/app/main.py",
)
CODE, DELIVERY = "code", "delivery"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _hashed(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "sha256": digest(entry)}


def _standards() -> list[dict[str, Any]]:
    return [
        _hashed(
            {
                "id": s.get("id"),
                "area": s.get("area"),
                "roles": list(s.get("roles") or []),
                "title": s.get("title"),
                "rule": s.get("rule"),
                "checked": s.get("checked"),
            }
        )
        for s in standards.catalogue()
    ]


def _gate() -> list[dict[str, Any]]:
    # The gate's own table, read where it is defined (see the module note).
    problems = dict(delivery_gate._GATE_PROBLEMS)
    rows = [
        _hashed(
            {
                "check": check,
                "problem": problem,
                "fixed_in": CODE
                if problem in delivery_gate.CODE_PROBLEMS
                else DELIVERY,
            }
        )
        for check, problem in problems.items()
    ]
    # The daemon never confirmed the write: sent back too, without a refutation.
    rows.append(
        _hashed(
            {
                "check": "files_written",
                "problem": GATE_UNCONFIRMED,
                "fixed_in": DELIVERY,
            }
        )
    )
    return rows


def _risk() -> list[dict[str, Any]]:
    rows = []
    for path in RISK_PROBES:
        found = risk.classify([path])
        reasons = found.get("reasons") or []
        area = str(reasons[0]).split(":", 1)[0] if reasons else None
        rows.append(_hashed({"probe": path, "tier": found.get("tier"), "area": area}))
    return rows


def quality_basis() -> dict[str, Any]:
    """The backend's definitions the quality plan reads; no database."""
    return {
        "standards": _standards(),
        "quality_areas": _hashed(
            {area: list(words) for area, words in standards.QUALITY_AREAS.items()}
        ),
        "gate": _gate(),
        "risk": _risk(),
    }


def _webhook(session: Session, company_id: int) -> dict[str, Any] | None:
    row = session.exec(
        select(CompanyWebhook).where(CompanyWebhook.company_id == company_id)
    ).first()
    if row is None:
        return None
    return _hashed(
        {
            "id": row.id,
            "enabled": bool(row.enabled),
            "events": sorted(str(e) for e in row.events or []),
            "updated_at": _iso(row.updated_at),
        }
    )


def _chat(session: Session, company_id: int) -> list[dict[str, Any]]:
    linked = dict(
        session.exec(
            select(ChatIdentity.provider, func.count())
            .where(ChatIdentity.company_id == company_id)
            .group_by(ChatIdentity.provider)
        ).all()
    )
    rows = session.exec(
        select(ChatConnection)
        .where(ChatConnection.company_id == company_id)
        .order_by(ChatConnection.provider)
    ).all()
    return [
        _hashed(
            {
                "id": row.id,
                "provider": row.provider,
                "enabled": bool(row.enabled),
                "tested_at": _iso(row.tested_at),
                "test_ok": row.test_ok,
                "updated_at": _iso(row.updated_at),
                "linked_members": int(linked.get(row.provider) or 0),
            }
        )
        for row in rows
    ]


def _brief(session: Session, company_id: int) -> dict[str, Any]:
    policy = session.exec(
        select(ManagerPolicy).where(ManagerPolicy.company_id == company_id)
    ).first()
    return _hashed(
        {
            "policy_id": policy.id if policy else None,
            "policy_updated_at": _iso(policy.updated_at) if policy else None,
            "manager_agent": bool(settings.MANAGER_AGENT_ENABLED),
            "mail_configured": bool(settings.SMTP_HOST.strip()),
            "interval_hours": float(settings.MANAGER_BRIEF_INTERVAL_HOURS),
        }
    )


def _anchors(session: Session, company_id: int) -> dict[str, Any] | None:
    row = session.exec(
        select(AuditAnchorSettings).where(AuditAnchorSettings.company_id == company_id)
    ).first()
    if row is None:
        return None
    return _hashed(
        {
            "id": row.id,
            "enabled": bool(row.enabled),
            "cadence_minutes": row.cadence_minutes,
            "publish_webhook": bool(row.publish_webhook),
            "tsa": bool(row.tsa_url and row.tsa_cert_pem),
            "updated_at": _iso(row.updated_at),
        }
    )


def communications(session: Session, company_id: int) -> dict[str, Any]:
    """The company's channel configuration, without an address, a secret or a name."""
    return {
        "webhook": _webhook(session, company_id),
        "chat": _chat(session, company_id),
        "brief": _brief(session, company_id),
        "anchors": _anchors(session, company_id),
    }
