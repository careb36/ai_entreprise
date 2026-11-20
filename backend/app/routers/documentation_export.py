"""Documentation export router — a stored pack as DOCX or PDF, and the export's manifest.

Endpoints (issue #694, phase 4 of ``docs/design/PROJECT_DOCUMENTATION.md``):
- ``GET /v1/documentation/exports``                              — which formats this deployment can produce (any signed-in caller).
- ``GET /v1/documentation/{pack_id}/export/{fmt}``          — download the pack as ``docx`` or ``pdf`` (Viewer).
- ``GET /v1/documentation/{pack_id}/export/{fmt}/manifest`` — the signed manifest of that export (Viewer).

The export is rendered from the stored pack only (``project_docs/export.py``):
no field of the request reaches the document. It runs in a thread, since
laying out a PDF takes seconds. The download carries the sha256 the backend
computed over the bytes it sends (``X-Content-SHA256``); the export's manifest
lists the same digest beside the pack's files, signed like the pack's own
manifest, with the audit chain's record of the pack. A renderer whose library
is missing answers 503 with its reason; nothing else stops working.
"""

import asyncio
import json

from auth_policy import current_user_if_required
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from models import User
from services.planning.project_docs import export as exports
from services.planning.project_docs import pack_manifest as manifests
from sqlmodel import Session

from routers.documentation import _load

router = APIRouter(tags=["Core"])

# One render at a time per event loop: a burst of downloads waits here, on the
# loop, instead of taking every worker thread while a PDF holds its lock.
_RENDERS: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _renders() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _RENDERS:
        _RENDERS.clear()
        _RENDERS[loop] = asyncio.Semaphore(1)
    return _RENDERS[loop]


async def _render(pack, fmt: str) -> exports.Export:
    # Checked after the role, like a document key: an outsider learns nothing.
    if fmt not in exports.FORMATS:
        raise HTTPException(status_code=404, detail="No such export format")
    try:
        async with _renders():
            return await asyncio.to_thread(exports.export, pack, fmt)
    except exports.ExportUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{fmt.upper()} export is unavailable here: {exc.reason}",
        ) from exc


@router.get("/v1/documentation/exports")
async def export_formats(
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Which export formats this deployment can produce, and why one cannot."""
    return {"formats": exports.availability()}


@router.get("/v1/documentation/{pack_id}/export/{fmt}")
async def download_export(
    pack_id: int,
    fmt: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The stored pack as one DOCX or PDF file, with the sha256 of its bytes."""
    pack, _ = _load(session, user, pack_id, "Viewer")
    rendered = await _render(pack, fmt)
    return Response(
        rendered.data,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{rendered.file_name}"',
            "X-Content-SHA256": rendered.sha256,
        },
    )


@router.get("/v1/documentation/{pack_id}/export/{fmt}/manifest")
async def export_manifest(
    pack_id: int,
    fmt: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The export's manifest: the pack's files and the export's sha256, signed."""
    pack, _ = _load(session, user, pack_id, "Viewer")
    rendered = await _render(pack, fmt)
    built = manifests.build_export(
        pack,
        fmt=fmt,
        file_name=rendered.file_name,
        data=rendered.data,
        renderer=rendered.renderer,
        conformance=rendered.conformance,
    )
    body = {
        "manifest": built,
        "manifest_sha256": manifests.digest(built),
        "signature": manifests.sign(built),
        "audit": manifests.audit_record(session, pack, built["pack_manifest_sha256"]),
    }
    return Response(
        json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{rendered.file_name}.manifest.json"'
            )
        },
    )
