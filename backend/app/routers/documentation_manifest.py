"""Documentation manifest router — the signed manifest of a documentation pack.

Endpoint (issue #694, phase 4 of ``docs/archive/design/PROJECT_DOCUMENTATION.md``):
- ``GET /v1/documentation/{pack_id}/manifest`` — download the manifest (Viewer).

The manifest is built from the stored pack by
``services/planning/project_docs/pack_manifest.py``: every file with the sha256 the
backend computed, the inputs digest and the compile time, signed with the
audit signing key when one is configured. ``audit`` says whether the digest
entered the audit chain at compile time and whether the pack still matches
it; it sits beside the manifest, outside what is signed.
"""

import json

from auth_policy import current_user_if_required
from database import get_session
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from models import User
from services.planning.project_docs import pack_manifest as manifests
from sqlmodel import Session

from routers.documentation import _load

router = APIRouter(tags=["Core"])


@router.get("/v1/documentation/{pack_id}/manifest")
async def get_manifest(
    pack_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The pack's manifest, its digest and signature, and the audit chain's record of it."""
    pack, _ = _load(session, user, pack_id, "Viewer")
    built = manifests.build(pack)
    manifest_sha256 = manifests.digest(built)
    body = {
        "manifest": built,
        "manifest_sha256": manifest_sha256,
        "signature": manifests.sign(built),
        "audit": manifests.audit_record(session, pack, manifest_sha256),
    }
    return Response(
        json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="documentation-pack-{pack.id}-manifest.json"'
            )
        },
    )
