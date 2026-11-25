import hashlib
import json
from uuid import UUID

from models.agent_run import AgentRun
from schemas import AgentRunCreate
from sqlmodel import Session, select

from services.platform.agent_service import AgentService

MAX_ARTIFACT_SIZE_BYTES = 2 * 1024 * 1024


class ArtifactTooLargeError(ValueError):
    pass


def _correlation_id(payload: AgentRunCreate) -> UUID | None:
    """The async run's outbox correlation id (an extra field), or ``None``.

    The task worker passes the id the assignment carried; a value that is not
    a UUID is left out rather than refusing the run.
    """
    raw = getattr(payload, "correlation_id", None)
    if not raw:
        return None
    try:
        return raw if isinstance(raw, UUID) else UUID(str(raw))
    except ValueError:
        return None


def ingest_agent_run(
    session: Session, payload: AgentRunCreate
) -> tuple[AgentRun, bool]:
    artifact_size = len(payload.artifact.encode("utf-8"))
    if artifact_size > MAX_ARTIFACT_SIZE_BYTES:
        raise ArtifactTooLargeError("artifact exceeds the 2 MiB limit")

    artifact_digest = hashlib.sha256(payload.artifact.encode("utf-8")).hexdigest()
    latest = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == payload.company_id,
            AgentRun.external_ref == payload.external_ref,
        )
        .order_by(AgentRun.version.desc())
    ).first()

    if latest is not None:
        claims_match = json.dumps(latest.claims, sort_keys=True) == json.dumps(
            payload.claims, sort_keys=True
        )
        if latest.artifact_digest == artifact_digest and claims_match:
            return latest, False
        version = latest.version + 1
    else:
        version = 1

    run = AgentRun(
        company_id=payload.company_id,
        external_ref=payload.external_ref,
        version=version,
        producer=payload.producer,
        model=payload.model,
        agent_role=payload.agent_role,
        subject=payload.subject,
        claims=payload.claims,
        artifact_text=payload.artifact,
        artifact_digest=artifact_digest,
        artifact_size=artifact_size,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        correlation_id=_correlation_id(payload),
    )
    session.add(run)
    session.flush()

    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"agent-run:{payload.external_ref}",
        agent_id=payload.producer,
        action="agent_run_ingested",
        details={
            "company_id": str(payload.company_id),
            "producer": payload.producer,
            "external_ref": payload.external_ref,
            "version": version,
            "model": payload.model,
        },
        result={
            "agent_run_id": run.id,
            "artifact_digest": artifact_digest,
            "artifact_size": artifact_size,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    run.audit_log_id = audit_log.id
    session.add(run)
    session.commit()
    session.refresh(run)
    return run, True
