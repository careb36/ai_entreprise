"""A company's standards set: drafts, the version in force, and who changes it
(issue #700; ``docs/design/COMPANY_STANDARDS.md`` §7, §12 decisions 4 and 8).

- ``create_draft`` stores what an import read (the planning context reads it
  through the daemon): the bytes, the sha256 computed here over them, the
  commit, the parsed standards with their refusals, the guards' hashes and
  the dry run. Importing never changes what is in force; it is audited as
  ``company_standards_imported``.
- ``activate`` is a signed-in human Owner's (the router refuses an API token;
  ``change_standards`` is in ``ALWAYS_ESCALATE``, so the Manager agent never
  does it). **Four-eyes, whatever the company's SoD mode** (ISO/IEC
  27001:2022 A.5.3): the person who imported a draft does not activate it.
  A company with exactly one Owner, and no Owner removed or demoted in the
  last 30 days, may activate its own import with a reason; the version is
  then flagged in the access review until a second Owner confirms it there.
  A draft with any refusal cannot be activated, and a draft holding a
  ``command`` standard cannot be activated unless commands run isolated
  (``container`` or ``bwrap``: the daemon's reported floor, or the sandbox
  the company asks for, which the daemon refuses to run without). The
  refusal names the sandbox it saw (fail closed: OWASP ASVS 4.0.3 V1.4).
- ``deactivate`` leaves the catalogue alone in force; ``reject`` closes a
  draft. Each is audited with the account, both versions and both sha256.

The catalogue is never touched here: a company only adds (§12 decision 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import CompanyStandardSet, User
from sqlmodel import Session, func, select

from services.contracts import company_standards as schema
from services.platform import sod
from services.platform import sso_group_approvals as owners
from services.platform.agent_service import AgentService

ISOLATED = ("container", "bwrap")
REASON_RANGE = (10, 500)
STATUSES = ("draft", "active", "superseded", "reverted", "rejected")


class StandardsRefused(ValueError):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def audit(
    session: Session, company_id: int, by: str, action: str, **result: Any
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:standards",
        agent_id=by if by.startswith("agent:") else f"human:{by}",
        action=action,
        details={"company_id": str(company_id)},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def in_force(session: Session, company_id: int | None) -> CompanyStandardSet | None:
    """The company's active version, or None: the catalogue alone."""
    if company_id is None:
        return None
    return session.exec(
        select(CompanyStandardSet).where(
            CompanyStandardSet.company_id == company_id,
            CompanyStandardSet.status == "active",
        )
    ).first()


def versions(
    session: Session, company_id: int, limit: int = 50
) -> list[CompanyStandardSet]:
    return list(
        session.exec(
            select(CompanyStandardSet)
            .where(CompanyStandardSet.company_id == company_id)
            .order_by(CompanyStandardSet.version.desc())
            .limit(limit)
        ).all()
    )


def find(session: Session, company_id: int, version: int) -> CompanyStandardSet:
    row = session.exec(
        select(CompanyStandardSet).where(
            CompanyStandardSet.company_id == company_id,
            CompanyStandardSet.version == version,
        )
    ).first()
    if row is None:
        raise StandardsRefused(404, "No such standards version")
    return row


def refusals_of(row: CompanyStandardSet) -> list[dict[str, Any]]:
    parsed = row.parsed or {}
    return [
        *(parsed.get("file_refusals") or []),
        *(parsed.get("refusals") or []),
        *(row.import_refusals or []),
    ]


def create_draft(
    session: Session,
    company_id: int,
    *,
    raw: bytes,
    source_commit: str,
    source_base: str | None,
    parsed: dict[str, Any],
    guard_hashes: dict[str, Any],
    baselines: dict[str, Any],
    import_refusals: list[dict[str, Any]],
    dry_run: dict[str, Any],
    sandbox_seen: str | None,
    by: str,
    user_id: int | None,
    proposed_by: str | None = None,
) -> CompanyStandardSet:
    """Store a draft; the sha256 is computed here over *raw*, never received."""
    last = session.exec(
        select(func.max(CompanyStandardSet.version)).where(
            CompanyStandardSet.company_id == company_id
        )
    ).one()
    row = CompanyStandardSet(
        company_id=company_id,
        version=int(last or 0) + 1,
        status="draft",
        source_path=schema.PATH,
        source_commit=source_commit,
        source_base=source_base,
        sha256=schema.sha256_hex(raw),
        raw=raw,
        parsed=parsed,
        guard_hashes=guard_hashes,
        baselines=baselines,
        import_refusals=import_refusals,
        dry_run=dry_run,
        sandbox_seen=sandbox_seen or None,
        proposed_by=proposed_by or by,
        imported_by=None if by.startswith("agent:") else by,
        imported_by_user_id=user_id,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        company_id,
        by,
        "company_standards_imported",
        version=row.version,
        sha256=row.sha256,
        source_commit=source_commit,
        source_base=source_base,
        standards=len(parsed.get("standards") or []),
        refusals=len(refusals_of(row)),
        proposed_by=row.proposed_by,
        fails_on_source=(dry_run or {}).get("fails_on_source", []),
    )
    session.commit()
    session.refresh(row)
    return row


def sandbox_refusal(row: CompanyStandardSet, company_sandbox: str) -> str | None:
    """Pure: why a draft with a ``command`` standard cannot run isolated, or None."""
    if not schema.has_command((row.parsed or {}).get("standards") or []):
        return None
    floor = (row.sandbox_seen or "").strip().lower()
    asked = (company_sandbox or "").strip().lower()
    if floor in ISOLATED or asked in ISOLATED:
        return None
    return (
        "command_on_host: a command standard runs only in the container or bwrap "
        f"sandbox; the daemon reported {floor or 'no floor'!r} and the company "
        f"asks for {asked or 'host'!r}"
    )


def _summary(row: CompanyStandardSet | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "version": row.version,
        "sha256": row.sha256,
        "source_commit": row.source_commit,
    }


def activate(
    session: Session,
    company_id: int,
    version: int,
    actor: User,
    *,
    company_sandbox: str,
    single_owner_reason: str | None = None,
) -> CompanyStandardSet:
    """Put *version* in force (module docstring for every condition)."""
    row = find(session, company_id, version)
    if row.status != "draft":
        raise StandardsRefused(409, f"Version {version} is {row.status}, not a draft")
    refusals = refusals_of(row)
    if refusals:
        raise StandardsRefused(
            409,
            {
                "message": "The draft has refusals; fix the file and import it again",
                "refusals": refusals,
            },
        )
    refusal = sandbox_refusal(row, company_sandbox)
    if refusal:
        raise StandardsRefused(409, refusal)
    me = sod.actor_of(session, actor) or sod.Person(actor.id, actor.email)
    importer = sod.Person(row.imported_by_user_id, row.imported_by)
    single = False
    clean = None
    if importer.known() and sod.same_person(me, importer):
        if owners.owner_count(session, company_id) != 1:
            audit(
                session,
                company_id,
                actor.email,
                "sod_refused",
                refused="activate_standards",
                version=version,
                rule="four_eyes",
            )
            session.commit()
            raise StandardsRefused(
                403, "four_eyes: another Owner activates what you imported"
            )
        if owners.recent_owner_change(session, company_id, _now()):
            raise StandardsRefused(
                409,
                "recent_owner_change: an Owner was removed or demoted in the last 30 days; another Owner activates it",
            )
        clean = " ".join((single_owner_reason or "").split())
        if not (REASON_RANGE[0] <= len(clean) <= REASON_RANGE[1]):
            raise StandardsRefused(
                422,
                "single_owner_reason: the only Owner activating their own import gives a reason of 10 to 500 characters",
            )
        single = True
    previous = in_force(session, company_id)
    now = _now()
    if previous is not None:
        previous.status, previous.ended_at, previous.ended_by = (
            "superseded",
            now,
            actor.email,
        )
        session.add(previous)
    row.status, row.activated_at = "active", now
    row.activated_by, row.activated_by_user_id = actor.email, actor.id
    row.supersedes_id = previous.id if previous else None
    row.single_owner, row.single_owner_reason = single, clean
    session.add(row)
    audit(
        session,
        company_id,
        actor.email,
        "company_standards_activated",
        **{"from": _summary(previous), "to": _summary(row)},
        single_owner=single,
        reason=clean,
        dry_run={
            k: (row.dry_run or {}).get(k, [])
            for k in ("fails_on_source", "unverifiable", "not_run")
        },
        imported_by=row.imported_by,
        proposed_by=row.proposed_by,
    )
    session.commit()
    session.refresh(row)
    return row


def deactivate(
    session: Session, company_id: int, actor: User
) -> CompanyStandardSet | None:
    """No company set in force: the catalogue alone. Audited; a no-op is not."""
    row = in_force(session, company_id)
    if row is None:
        return None
    row.status, row.ended_at, row.ended_by = "superseded", _now(), actor.email
    session.add(row)
    audit(
        session,
        company_id,
        actor.email,
        "company_standards_deactivated",
        **{"from": _summary(row), "to": None},
    )
    session.commit()
    return row


def reject(
    session: Session, company_id: int, version: int, actor: User
) -> CompanyStandardSet:
    row = find(session, company_id, version)
    if row.status != "draft":
        raise StandardsRefused(409, f"Version {version} is {row.status}, not a draft")
    row.status, row.ended_at, row.ended_by = "rejected", _now(), actor.email
    session.add(row)
    audit(
        session,
        company_id,
        actor.email,
        "company_standards_rejected",
        version=version,
        sha256=row.sha256,
    )
    session.commit()
    session.refresh(row)
    return row


def serialize(row: CompanyStandardSet, full: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": row.version,
        "status": row.status,
        "sha256": row.sha256,
        "source_path": row.source_path,
        "source_commit": row.source_commit,
        "source_base": row.source_base,
        "proposed_by": row.proposed_by,
        "imported_by": row.imported_by,
        "imported_at": _iso(row.imported_at),
        "activated_by": row.activated_by,
        "activated_at": _iso(row.activated_at),
        "single_owner": row.single_owner,
        "flagged": bool(row.single_owner and row.reviewed_in is None),
        "ended_by": row.ended_by,
        "ended_at": _iso(row.ended_at),
        "sandbox_seen": row.sandbox_seen,
        "refusals": refusals_of(row),
        "fails_on_source": (row.dry_run or {}).get("fails_on_source", []),
        "result": row.result,
    }
    if full:
        out.update(
            standards=(row.parsed or {}).get("standards") or [],
            guard_hashes=row.guard_hashes or {},
            dry_run=row.dry_run or {},
            measurement=row.measurement,
        )
    return out
