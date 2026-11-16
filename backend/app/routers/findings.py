"""Findings router — the company's retrospective findings, read-only
(``docs/design/rsi-loop.md``, PR 9 of §8).

- ``GET /v1/findings``              the company's findings, newest first (Viewer)
- ``GET /v1/findings/{finding_id}`` one finding, with its specification text (Viewer)

The outcomes are observations the platform writes
(``services.learning.findings.record_outcome``); no route here writes them,
and none starts a cycle — the runner reads ``findings.may_start`` (REQ-20).
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Finding, User
from models.finding import FINDING_STATUSES
from services.learning import findings
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


def _serialize(finding: Finding, *, with_text: bool = False) -> dict:
    data = {
        "id": finding.id,
        "company_id": finding.company_id,
        "source": finding.source,
        "source_ref": finding.source_ref,
        "title": finding.title,
        "specification_sha256": finding.specification_sha256,
        "specification_path": finding.specification_path,
        "target_measure": finding.target_measure,
        "status": finding.status,
        "outcomes": finding.outcomes or [],
        "closed_by": finding.closed_by,
        "created_at": finding.created_at.isoformat() if finding.created_at else None,
        "updated_at": finding.updated_at.isoformat() if finding.updated_at else None,
    }
    if with_text:
        data["specification_text"] = finding.specification_text
    return data


@router.get("/findings")
async def list_findings(
    company_id: int,
    status: str | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's findings, newest first; *status* filters."""
    require_company_role(session, user, company_id, "Viewer")
    if status is not None and status not in FINDING_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status. Use one of: {', '.join(FINDING_STATUSES)}",
        )
    rows = findings.list_findings(session, company_id, status=status)
    return {"findings": [_serialize(row) for row in rows]}


@router.get("/findings/{finding_id}")
async def get_finding(
    finding_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One finding, with its specification text and every outcome."""
    finding = findings.get_finding(session, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    require_company_role(session, user, finding.company_id, "Viewer")
    return _serialize(finding, with_text=True)
