"""A run's full logs, read back and checked against the recorded sha256 (#315).

The install, the test run and the application check keep each stream whole
in the object store, and the backend recorded the size and sha256 it computed
when it read each object back (``services/workspace/run_logs.py``). These
routes let a person of the run's company (Viewer) read a log again:

- ``GET /v1/agent_runs/{run_id}/logs`` lists the full logs the run's
  verifications recorded, with the recorded size and sha256. Nothing is read.
- ``GET /v1/agent_runs/{run_id}/logs/{log_id}`` reads the object again,
  computes its sha256 over the bytes it is about to serve and streams them
  (``text/plain``; ``download=true`` as an attachment). The headers say what
  was observed: ``X-Log-Integrity`` is ``matched`` or ``mismatched`` against
  the recorded hash, with ``X-Log-Sha256-Recorded``, ``X-Log-Sha256-Observed``,
  ``X-Log-Size`` and ``X-Log-Observed-By: backend``. A log the store no
  longer holds is a 410 whose detail says ``missing``; a store that cannot be
  read is a 503 whose detail says ``unavailable`` (unknown, never absent).

No hash is accepted from the client or the producer (rule 6 of
``docs/POSITIONING.md``): the comparison is between two values the backend
computed, at record time and now.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from models import User
from models.agent_run import AgentRun, Verification
from services.workspace import run_log_reader
from sqlmodel import Session, select

router = APIRouter(prefix="/v1/agent_runs", tags=["Run logs"])


def _verifications(
    session: Session, run_id: int, company_id: int
) -> list[Verification]:
    run = session.exec(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.company_id == company_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return list(
        session.exec(
            select(Verification)
            .where(
                Verification.agent_run_id == run_id,
                Verification.company_id == company_id,
            )
            .order_by(Verification.observed_at, Verification.id)
        ).all()
    )


def _integrity_detail(status: str, entry: dict, reason: str) -> dict:
    return {
        "message": reason,
        "integrity": {
            "status": status,
            "observed_by": "backend",
            "recorded_sha256": entry.get("recorded_sha256"),
            "reason": reason,
        },
    }


@router.get("/{run_id}/logs")
def list_run_logs(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The full logs the run's verifications recorded, oldest first."""
    require_company_role(session, user, company_id, "Viewer")
    verifications = _verifications(session, run_id, company_id)
    return {"logs": run_log_reader.recorded_logs(verifications)}


@router.get("/{run_id}/logs/{log_id}")
async def read_run_log(
    run_id: int,
    log_id: str,
    request: Request,
    company_id: int = Query(...),
    download: bool = Query(False),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One full log, streamed, with its sha256 computed over the bytes served."""
    require_company_role(session, user, company_id, "Viewer")
    parsed = run_log_reader.parse_id(log_id)
    verifications = _verifications(session, run_id, company_id)
    verification = next(
        (v for v in verifications if parsed and v.id == parsed[0]), None
    )
    logs = (
        run_log_reader.logs_of(verification.id, verification.evidence)
        if verification
        else []
    )
    if parsed is None or parsed[1] >= len(logs):
        raise HTTPException(status_code=404, detail="Run log not found")
    entry = logs[parsed[1]]
    if not entry["openable"]:
        raise HTTPException(
            status_code=409,
            detail=_integrity_detail(
                run_log_reader.UNAVAILABLE, entry, entry["reason"]
            ),
        )
    nats_client = getattr(request.app.state, "nats_client", None)
    try:
        read = await run_log_reader.read(nats_client, entry["object"])
    except run_log_reader.LogMissing as exc:
        raise HTTPException(
            status_code=410,
            detail=_integrity_detail(run_log_reader.MISSING, entry, str(exc)),
        ) from exc
    except run_log_reader.LogUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=_integrity_detail(run_log_reader.UNAVAILABLE, entry, str(exc)),
        ) from exc
    headers = {
        "X-Log-Integrity": run_log_reader.integrity(
            entry["recorded_sha256"], read.sha256
        ),
        "X-Log-Sha256-Recorded": entry["recorded_sha256"],
        "X-Log-Sha256-Observed": read.sha256,
        "X-Log-Size": str(read.size),
        "X-Log-Observed-By": "backend",
        "Content-Length": str(read.size),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        stream = str(entry.get("stream") or "log")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stream)[:32]
        headers["Content-Disposition"] = (
            f'attachment; filename="run-{run_id}-{log_id}-{safe}.log"'
        )
    return StreamingResponse(
        read.chunks(), media_type="text/plain; charset=utf-8", headers=headers
    )
