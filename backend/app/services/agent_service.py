"""
AgentService — orchestration layer for LangGraph workflow triggers and audit logging.

Responsibilities:
- Publish ``workspace.generate`` events to NATS so the LangGraph runtime can
  pick them up asynchronously (fire-and-forget pattern).
- Write dual-destination audit entries: filesystem JSONL + ``audit_log`` table.

This service is intentionally thin; it delegates heavy lifting to the NATS
event bus and the LangGraph agents.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from typing import Optional
from sqlmodel import Session
from models import AuditLog


class AgentService:
    """
    Service layer for agent workflow orchestration and audit trail management.

    Args:
        nats_client: An active ``NATSClient`` instance used for publishing
                     events.  May be ``None`` in unit-test contexts where NATS
                     is not available.
    """

    def __init__(self, nats_client=None):
        self.nats_client = nats_client

    async def generate_package(
        self,
        company_id: int,
        story: str,
        constraints: str,
        business_unit_id: Optional[int] = None,
        interaction_id: str = None,
        session: Session = None,
    ) -> str:
        """
        Trigger an AI package generation workflow by publishing to NATS.
        """
        if not self.nats_client:
            raise RuntimeError("NATS client not initialized")

        # Resolve Business Unit for Governance (Phase 4)
        if not business_unit_id:
            if not session:
                raise ValueError("Session required to resolve default Business Unit")
            
            from models.company import BusinessUnit
            from sqlmodel import select
            
            unit = session.exec(select(BusinessUnit).where(BusinessUnit.company_id == company_id)).first()
            if not unit:
                raise ValueError(f"No Business Unit found for company {company_id}. Governance requires a cost centre.")
            business_unit_id = unit.id

        interaction_id = interaction_id or str(uuid4())
        await self.nats_client.publish("workspace.generate", {
            "interaction_id": interaction_id,
            "company_id": company_id,
            "business_unit_id": business_unit_id,
            "story": story,
            "constraints": constraints,
        })
        return interaction_id

    def audit_interaction(
        self,
        session: Session,
        interaction_id: str,
        agent_id: int,
        action: str,
        details: str,
        result: str,
        decision_signature: str,
        cost_tokens: int,
    ):
        """
        Write a dual-destination audit entry for an agent interaction.

        Destinations:
        1. **Filesystem** — appends a JSON line to ``AUDIT_LOG_PATH/audit.jsonl``.
           This file acts as an immutable, append-only audit trail that persists
           even if the database is unavailable.
        2. **Database** — inserts an ``AuditLog`` row in the same SQLModel session.
           Provides queryable history for governance dashboards.

        Args:
            session:            Active SQLModel session; the caller is responsible
                                for committing.
            interaction_id:     UUID correlating all audit entries for a workflow run.
            agent_id:           Identifier of the agent that performed the action.
            action:             Short label (e.g. ``"generate_code"``).
            details:            Arbitrary context dict / string for the action.
            result:             Outcome dict / string returned by the action.
            decision_signature: Cryptographic hash for tamper detection.
            cost_tokens:        LLM tokens consumed; credited to the business unit.

        Returns:
            The persisted ``AuditLog`` ORM instance.
        """
        audit_log_path = Path(os.getenv("AUDIT_LOG_PATH", "/app/.factory/logs"))
        audit_log_path.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "interaction_id": interaction_id,
            "agent_id": agent_id,
            "action": action,
            "details": details,
            "result": result,
            "decision_signature": decision_signature,
            "cost_tokens": cost_tokens,
        }

        # Filesystem audit — append-only JSONL; one JSON object per line.
        with open(audit_log_path / "audit.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Database audit — queryable via the governance dashboard.
        log = AuditLog(
            interaction_id=interaction_id,
            agent_id=agent_id,
            action=action,
            details=details,
            result=result,
            decision_signature=decision_signature,
            cost_tokens=cost_tokens,
        )
        session.add(log)
        session.commit()
        return log
