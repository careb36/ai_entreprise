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
  ``X-Log-Verified`` is ``true`` only when the hashes matched.
- The same route serves one byte range: a ``Range: bytes=a-b`` header (or
  ``bytes=a-``, ``bytes=-n``) or ``?offset=&limit=``. The answer is a 206
  with ``Content-Range: bytes a-b/<recorded size>``, and a 416 with
  ``Content-Range: bytes */<recorded size>`` for a range past the end. A
  piece cannot be checked against the whole log's sha256, so it says so:
  ``X-Log-Integrity: unverified``, ``X-Log-Verified: false`` and
  ``X-Log-Unverified-Reason: partial``, with the recorded hash and no
  observed one. Only the full read verifies.

No hash is accepted from the client or the producer (rule 6 of
``docs/POSITIONING.md``): the comparison is between two values the backend
computed, at record time and now.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
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


def _refusal(exc: Exception, entry: dict) -> HTTPException:
    if isinstance(exc, run_log_reader.LogMissing):
        status, code = run_log_reader.MISSING, 410
    elif isinstance(exc, run_log_reader.LogChanged):
        status, code = run_log_reader.UNVERIFIED, 409
    else:
        status, code = run_log_reader.UNAVAILABLE, 503
    return HTTPException(
        status_code=code, detail=_integrity_detail(status, entry, str(exc))
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
    offset: int | None = Query(None, ge=0),
    limit: int | None = Query(None, ge=1),
    range_header: str | None = Header(None, alias="Range"),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One full log, streamed, with its sha256 computed over the bytes served;
    or one byte range of it, served as unverified."""
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
    ranged = offset is not None or limit is not None
    if ranged and range_header:
        raise HTTPException(
            status_code=400,
            detail="Give a Range header or offset and limit, not both",
        )
    if ranged or range_header:
        piece = await _piece(nats_client, entry, range_header, offset, limit)
        if piece is not None:
            return _partial_response(piece, entry)
    try:
        read = await run_log_reader.read(nats_client, entry["object"])
    except (run_log_reader.LogMissing, run_log_reader.LogUnavailable) as exc:
        raise _refusal(exc, entry) from exc
    status = run_log_reader.integrity(entry["recorded_sha256"], read.sha256)
    headers = {
        **_common_headers(entry),
        "X-Log-Integrity": status,
        "X-Log-Verified": "true" if status == run_log_reader.MATCHED else "false",
        "X-Log-Sha256-Observed": read.sha256,
        "X-Log-Size": str(read.size),
        "Content-Length": str(read.size),
    }
    if status != run_log_reader.MATCHED:
        headers["X-Log-Unverified-Reason"] = status
    if download:
        stream = str(entry.get("stream") or "log")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stream)[:32]
        headers["Content-Disposition"] = (
            f'attachment; filename="run-{run_id}-{log_id}-{safe}.log"'
        )
    return StreamingResponse(
        read.chunks(), media_type="text/plain; charset=utf-8", headers=headers
    )


def _common_headers(entry: dict) -> dict:
    return {
        "X-Log-Sha256-Recorded": entry["recorded_sha256"],
        "X-Log-Observed-By": "backend",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


async def _piece(nats_client, entry: dict, header, offset, limit):
    """The range asked for, read; ``None`` when the answer is the full read."""
    total = entry.get("recorded_size")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise HTTPException(
            status_code=409,
            detail=_integrity_detail(
                run_log_reader.UNVERIFIED,
                entry,
                "no size was recorded, so a range cannot be placed; read the whole log",
            ),
        )
    try:
        asked = run_log_reader.parse_range(header, offset, limit, total)
    except run_log_reader.RangeNotSatisfiable as exc:
        raise HTTPException(
            status_code=416,
            detail=_integrity_detail(run_log_reader.UNVERIFIED, entry, str(exc)),
            headers={"Content-Range": f"bytes */{total}"},
        ) from exc
    if asked is None:
        return None
    try:
        return await run_log_reader.read_range(
            nats_client, entry["object"], total, *asked
        )
    except (
        run_log_reader.LogMissing,
        run_log_reader.LogUnavailable,
        run_log_reader.LogChanged,
    ) as exc:
        raise _refusal(exc, entry) from exc


def _partial_response(piece, entry: dict) -> StreamingResponse:
    """A piece of the log: never checked against the whole log's hash."""
    headers = {
        **_common_headers(entry),
        "X-Log-Integrity": run_log_reader.UNVERIFIED,
        "X-Log-Verified": "false",
        "X-Log-Unverified-Reason": run_log_reader.PARTIAL,
        "X-Log-Size": str(piece.total),
        "Content-Range": f"bytes {piece.start}-{piece.end}/{piece.total}",
        "Content-Length": str(piece.end - piece.start + 1),
    }
    return StreamingResponse(
        piece.chunks(),
        status_code=206,
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )
