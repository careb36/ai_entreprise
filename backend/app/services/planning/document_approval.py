"""Approving a specification, an architecture or a documentation pack, and taking an approval back.

One code path for a Manager's ``POST /v1/specifications/{id}/approve`` and
``POST /v1/architectures/{id}/approve`` and for the Manager agent when the
company's policy names ``approve_specification`` or ``approve_architecture``
(issue #394): the deterministic check must not block, and the version it
revises (any other approved architecture of the specification) becomes
superseded. The ids superseded are returned, so the agent's override can put
them back. The caller commits.

Taking an approval back (``unapprove``, the client's override of the agent)
returns the document to ``draft`` and restores what it superseded, only while
nothing was planned from it: a specification no sprint names, an
architecture of a specification no sprint was started for since it was
approved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import Architecture, DocumentationPack, Specification, Sprint
from sqlalchemy import func
from sqlmodel import Session, select

from services.planning import arch_author, arch_check, spec_check, spec_planning


class ApprovalRefused(Exception):
    """The check blocks, or the approval cannot be taken back: ``status`` and ``detail``."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _blocked(result: dict[str, Any]) -> None:
    if not result["ok"]:
        raise ApprovalRefused(
            409,
            {
                "message": f"{result['blocking']} finding(s) block the approval.",
                "findings": [
                    f for f in result["findings"] if f["severity"] == "blocking"
                ],
            },
        )


def last_sprint_id(session: Session, company_id: int) -> int:
    """The company's newest sprint id (0 without one), kept at an approval."""
    return int(
        session.exec(
            select(func.max(Sprint.id)).where(Sprint.company_id == company_id)
        ).one()
        or 0
    )


def approve_specification(
    session: Session, spec: Specification, actor: str
) -> list[int]:
    """Approve the draft *spec*; the ids it superseded."""
    _blocked(spec_check.check(spec_planning.spec_content(spec)))
    superseded = []
    if spec.supersedes_id is not None:
        previous = session.get(Specification, spec.supersedes_id)
        if previous is not None and previous.status == "approved":
            previous.status = "superseded"
            previous.updated_at = _now()
            session.add(previous)
            superseded.append(previous.id)
    spec.status = "approved"
    spec.approved_by = actor
    spec.approved_at = _now()
    spec.updated_at = spec.approved_at
    session.add(spec)
    return superseded


def approve_architecture(
    session: Session, arch: Architecture, spec: Specification, actor: str
) -> list[int]:
    """Approve the draft *arch*; the ids it superseded."""
    _blocked(
        arch_check.check(
            arch_author.content_of(arch), arch_author.requirement_ids(spec)
        )
    )
    superseded = []
    for previous in session.exec(
        select(Architecture).where(
            Architecture.specification_id == spec.id,
            Architecture.status == "approved",
        )
    ).all():
        previous.status = "superseded"
        previous.updated_at = _now()
        session.add(previous)
        superseded.append(previous.id)
    arch.status = "approved"
    arch.approved_by = actor
    arch.approved_at = _now()
    arch.updated_at = arch.approved_at
    session.add(arch)
    return superseded


def approve_pack(
    session: Session, pack: DocumentationPack, lint: dict[str, Any], actor: str
) -> list[int]:
    """Approve the draft *pack* (issue #530); the ids it superseded.

    The same rule as a specification's: the deterministic check, here the
    pack's drift lint (``project_docs.pack.lint``), must not block, so a pack
    whose records changed since it was compiled is compiled again first.
    """
    _blocked(lint)
    superseded = []
    for previous in session.exec(
        select(DocumentationPack).where(
            DocumentationPack.company_id == pack.company_id,
            DocumentationPack.status == "approved",
        )
    ).all():
        previous.status = "superseded"
        previous.updated_at = _now()
        session.add(previous)
        superseded.append(previous.id)
    pack.status = "approved"
    pack.approved_by = actor
    pack.approved_at = _now()
    pack.updated_at = pack.approved_at
    session.add(pack)
    return superseded


def _restore(session: Session, model: Any, ids: list[int]) -> None:
    for old_id in ids:
        old = session.get(model, old_id)
        if old is not None and old.status == "superseded":
            old.status = "approved"
            old.updated_at = _now()
            session.add(old)


def unapprove(
    session: Session,
    doc: Specification | Architecture,
    approved_by: str,
    superseded: list[int],
    sprints_before: int,
) -> None:
    """Back to draft, with what it superseded approved again; ``ApprovalRefused``
    (409) once something was planned from it or someone decided it since."""
    if doc.status != "approved" or doc.approved_by != approved_by:
        raise ApprovalRefused(
            409, f"The document is {doc.status} now, no longer as the agent left it."
        )
    if isinstance(doc, Specification):
        designed = session.exec(
            select(Architecture.id).where(
                Architecture.specification_id == doc.id,
                Architecture.status == "approved",
            )
        ).first()
        if designed is not None:
            raise ApprovalRefused(
                409, f"Architecture {designed} was approved from it: revise it instead."
            )
        planned = session.exec(
            select(Sprint.id).where(Sprint.specification_id == doc.id)
        ).first()
    else:
        planned = session.exec(
            select(Sprint.id).where(
                Sprint.specification_id == doc.specification_id,
                Sprint.id > sprints_before,
            )
        ).first()
    if planned is not None:
        raise ApprovalRefused(
            409,
            f"Sprint {planned} was planned from it: revise it to change it instead.",
        )
    doc.status = "draft"
    doc.approved_by = None
    doc.approved_at = None
    doc.updated_at = _now()
    session.add(doc)
    _restore(session, type(doc), superseded)
