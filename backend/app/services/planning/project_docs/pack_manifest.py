"""The signed manifest of a documentation pack (issue #694, phase 4).

``docs/design/PROJECT_DOCUMENTATION.md``, "Phase 4: the signed manifest". A
manifest lists every file a pack writes to ``docs/project/`` (``pack.files_of``)
with the sha256 and size the backend computed over those bytes, the pack's
inputs digest and its compile time. Nothing in it comes from the party being
recorded: the backend hashes the content it holds, never a hash a producer,
the daemon or the browser reports.

It is signed with the deployment's existing key, ``AUDIT_SIGNING_KEY``,
through ``audit_signing.sign_audit_payload``: ``hmac-sha256:<hex>`` with a key,
``unsigned:<sha256>`` without one. No new key scheme. Because the factory holds
that key, the signature alone does not stop the factory rewriting a pack, so
the manifest's digest also enters the company's audit chain when the pack is
compiled (``record``): the chain is the record that external anchors
(``services/platform/audit_anchors.py``) cover, and a pack rewritten in the
database afterwards gives a manifest whose digest no longer matches the
chained one (``audit_record``).

The manifest is derived from the stored pack, so it needs no table: the
chained digest is what is kept.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import audit_signing
from audit_signing import SIGNED_PREFIX, UNSIGNED_PREFIX
from models import AuditLog, DocumentationPack
from sqlmodel import Session, select

from services.planning.project_docs.pack import files_of

MANIFEST_TYPE = "la-fabrique.documentation-pack-manifest"
MANIFEST_VERSION = 1
AUDIT_ACTION = "documentation_pack_compiled"


def _same(left: str, right: str) -> bool:
    """A constant-time comparison that also takes text outside ASCII."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _interaction(pack: DocumentationPack) -> str:
    return f"documentation-pack:{pack.id}"


def compiled_at(moment: datetime | None) -> str:
    """The compile time in UTC to the second; a naive moment is read as UTC."""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(pack: DocumentationPack) -> dict[str, Any]:
    """The manifest of *pack*: every file with the sha256 computed here, sorted by path."""
    files = []
    for item in files_of(pack):
        data = str(item["content"]).encode("utf-8")
        files.append(
            {
                "path": item["path"],
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    files.sort(key=lambda f: f["path"])
    return {
        "type": MANIFEST_TYPE,
        "version": MANIFEST_VERSION,
        "company_id": pack.company_id,
        "pack_id": pack.id,
        "pack_version": pack.version,
        "language": pack.language,
        "inputs_sha256": pack.inputs_sha256,
        "compiled_at": compiled_at(pack.created_at),
        "files": files,
    }


EXPORT_MANIFEST_TYPE = "la-fabrique.documentation-pack-export-manifest"
EXPORT_DIRECTORY = "export"


def build_export(
    pack: DocumentationPack,
    *,
    fmt: str,
    file_name: str,
    data: bytes,
    renderer: str,
    conformance: list[str],
) -> dict[str, Any]:
    """The manifest of one export of *pack*: the pack's files and the export's own.

    The export's bytes are hashed here, by the backend that rendered them,
    and listed as one more file (``export/<file name>``), so ``verify`` checks
    a downloaded DOCX or PDF beside the Markdown. ``pack_manifest_sha256`` is
    the digest of the pack's own manifest, the one the audit chain holds.
    """
    built = build(pack)
    files = [
        *built["files"],
        {
            "path": f"{EXPORT_DIRECTORY}/{file_name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
    ]
    files.sort(key=lambda f: f["path"])
    return {
        **built,
        "type": EXPORT_MANIFEST_TYPE,
        "version": MANIFEST_VERSION,
        "pack_manifest_sha256": digest(built),
        "export": {
            "format": fmt,
            "path": f"{EXPORT_DIRECTORY}/{file_name}",
            "renderer": renderer,
            # What the file declares; the design says which of it is verified.
            "conformance": list(conformance),
        },
        "files": files,
    }


def canonical(manifest: Mapping[str, Any]) -> bytes:
    """The bytes that are hashed and signed. Part of the format: changing it needs a new version."""
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(manifest)).hexdigest()


def sign(manifest: Mapping[str, Any]) -> str:
    """``hmac-sha256:<hex>`` with the audit signing key, ``unsigned:<sha256>`` without one."""
    return audit_signing.sign_audit_payload(canonical(manifest))


def _signature_status(manifest: Mapping[str, Any], signature: str) -> tuple[str, str]:
    payload = canonical(manifest)
    if isinstance(signature, str) and signature.startswith(SIGNED_PREFIX):
        if not audit_signing.is_signing_enabled():
            return "unverifiable", "hmac-sha256"
        expected = audit_signing.sign_audit_payload(payload)
        valid = _same(signature, expected)
        return ("valid" if valid else "invalid"), "hmac-sha256"
    if isinstance(signature, str) and signature.startswith(UNSIGNED_PREFIX):
        expected = UNSIGNED_PREFIX + hashlib.sha256(payload).hexdigest()
        valid = _same(signature, expected)
        return ("unsigned" if valid else "invalid"), "unsigned"
    return "invalid", "unknown"


def verify(
    manifest: Mapping[str, Any], signature: str, files: Mapping[str, bytes]
) -> dict[str, Any]:
    """Check *files* (path to bytes as found) against *manifest* and its *signature*.

    ``ok`` only when the signature is valid or an intact ``unsigned`` digest
    and every listed file is present with the listed sha256, and no other file
    is. A keyed signature without the key here is ``unverifiable``, not valid.
    """
    status, mode = _signature_status(manifest, signature)
    listed = {
        str(f.get("path")): str(f.get("sha256"))
        for f in manifest.get("files") or []
        if isinstance(f, Mapping)
    }
    mismatched, missing = [], []
    for path, expected in sorted(listed.items()):
        if path not in files:
            missing.append(path)
        elif not _same(hashlib.sha256(files[path]).hexdigest(), expected):
            mismatched.append(path)
    unexpected = sorted(set(files) - set(listed))
    ok = (
        status in ("valid", "unsigned")
        and not mismatched
        and not missing
        and not unexpected
    )
    return {
        "ok": ok,
        "signature": status,
        "mode": mode,
        "mismatched": mismatched,
        "missing": missing,
        "unexpected": unexpected,
    }


# --- the audit chain -----------------------------------------------------------


def record(session: Session, pack: DocumentationPack, actor: str) -> AuditLog:
    """Enter the manifest's digest in the company's audit chain. *pack* must have an id.

    ``audit_interaction`` commits the session, the pack with it.
    """
    from services.platform.agent_service import AgentService

    built = build(pack)
    return AgentService().audit_interaction(
        session=session,
        company_id=pack.company_id,
        interaction_id=_interaction(pack),
        agent_id=f"human:{actor}",
        action=AUDIT_ACTION,
        details={
            "pack_id": pack.id,
            "version": pack.version,
            "inputs_sha256": pack.inputs_sha256,
            "manifest_sha256": digest(built),
            "files": len(built["files"]),
        },
        result={"manifest_version": MANIFEST_VERSION},
        decision_signature=None,
        cost_tokens=0,
    )


def audit_record(
    session: Session, pack: DocumentationPack, manifest_sha256: str
) -> dict[str, Any]:
    """The chained digest recorded at compile time and whether *manifest_sha256* matches it."""
    row = session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == pack.company_id,
            AuditLog.interaction_id == _interaction(pack),
            AuditLog.action == AUDIT_ACTION,
        )
        .order_by(AuditLog.id)
    ).first()
    details = row.details if row is not None else None
    recorded = details.get("manifest_sha256") if isinstance(details, dict) else None
    if row is None or not isinstance(recorded, str):
        return {"recorded": False, "matches": None}
    return {
        "recorded": True,
        "matches": _same(recorded, manifest_sha256),
        "audit_id": row.id,
        "chain_seq": row.chain_seq,
        "chain_hash": row.chain_hash,
        "manifest_sha256": recorded,
    }
