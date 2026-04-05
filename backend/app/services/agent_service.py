import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from sqlmodel import Session
from models import AuditLog

class AgentService:
    def __init__(self, nats_client=None):
        self.nats_client = nats_client

    async def generate_package(self, company_id: int, story: str, constraints: str, interaction_id: str = None):
        """Orchestrates package generation by publishing to NATS."""
        if not self.nats_client:
            raise RuntimeError("NATS client not initialized")
            
        interaction_id = interaction_id or str(uuid4())
        await self.nats_client.publish("workspace.generate", {
            "interaction_id": interaction_id,
            "company_id": company_id,
            "story": story,
            "constraints": constraints,
        })
        return interaction_id

    def audit_interaction(self, session: Session, interaction_id: str, agent_id: int, action: str, details: str, result: str, decision_signature: str, cost_tokens: int):
        """Logs an interaction to the filesystem and the database."""
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
        
        # Filesystem audit
        with open(audit_log_path / "audit.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Database audit
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
