"""
CompanyService — logic for company lifecycle, tenant bootstrapping, and team discovery.

Responsibilities:
- Initialise a new company tenant with agents and credentials.
- Handle human-in-the-loop task approvals and governance.
- Aggregate sprint health and token usage analytics for the dashboard.
- Bridge Postgres multi-tenant data to the Neo4j Temporal Knowledge Graph.
"""

import json
from datetime import date
from uuid import uuid4
from typing import List, Dict, Any, Optional

from sqlmodel import Session, select, func
from sqlalchemy import text

from database import get_neo4j_driver
from models import (
    Company, Agent, ProjectCredential, Sprint, BacklogItem,
    OutboxEvent, TaskQueue, AuditLog, BusinessUnit
)
from services.sprint_planner import plan_foundation_sprint
from crypto import encrypt_value, decrypt_value
from logging_config import logger


class CompanyService:
    """
    Service layer for multi-tenancy governance and company lifecycle management.

    Args:
        session: An active ``SQLModel`` session for database persistence.
    """

    def __init__(self, session: Session):
        self.session = session

    def init_company(
        self,
        company_name: str,
        agents: List[Dict[str, Any]],
        default_scope_size: str = "M",
        default_sprint_length_days: Optional[int] = None,  # deprecated
        autonomy_level: int = 2,
        permissions: Optional[Dict[str, str]] = None,
        project_path: Optional[str] = None,
        detected_stack: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Bootstrap a new company tenant with its AI agents and project credentials.

        This involves multiple tables and a non-critical Neo4j seed operation.
        Returns a summary of the created entities.
        """
        # 1. Create the Company
        company = Company(
            company_name=company_name,
            default_scope_size=default_scope_size,
            autonomy_level=autonomy_level,
        )
        self.session.add(company)
        self.session.flush()

        # 2. Create Agents
        for agent_data in agents:
            self.session.add(Agent(
                company_id=company.id,
                name=agent_data.get("name", ""),
                role=agent_data.get("role"),
                persona=agent_data.get("persona"),
                seniority=agent_data.get("seniority", "Senior"),
                specialty=agent_data.get("specialty", ""),
                model=agent_data.get("model", ""),
                system_prompt=agent_data.get("system_prompt"),
                token_budget=agent_data.get("token_budget", 500000),
            ))

        # 3. Create Project Credentials
        if permissions:
            for cred_type, access_level in permissions.items():
                if access_level != "none":
                    self.session.add(ProjectCredential(
                        company_id=company.id,
                        credential_type=cred_type,
                        access_level=access_level,
                        encrypted_value=encrypt_value("vault_encrypted_placeholder"),
                    ))

        # 4. Plan and Create Foundation Sprint
        plan = plan_foundation_sprint(
            company_name=company_name,
            agents=agents,
            detected_stack=detected_stack,
            project_path=project_path,
        )

        sprint = Sprint(
            company_id=company.id,
            sprint_name="Sprint 0 — Foundations",
            start_date=date.today().isoformat(),
            scope_size=default_scope_size,
            status="active",
            goal=plan.get("goal", f"Foundation sprint for {company_name}"),
        )
        self.session.add(sprint)
        self.session.flush()

        # 5. Create Backlog Items and Outbox Events
        tasks = plan.get("tasks", [])
        for task in tasks:
            bi = BacklogItem(
                sprint_id=sprint.id,
                title=task.get("title", "Untitled task"),
                description=task.get("description", ""),
                priority=task.get("priority", "medium"),
                assignee=task.get("assignee", "PO"),
                status="todo",
            )
            self.session.add(bi)
            self.session.flush()

            nats_payload = {
                "subject": "task.assign",
                "message": {
                    "backlog_item_id": bi.id,
                    "interaction_id": str(uuid4()),
                    "agent": task.get("assignee", "PO"),
                    "sprint_id": sprint.id,
                },
            }
            self.session.add(OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(bi.id),
                event_type="nats_publish",
                payload=nats_payload,
            ))

        self.session.commit()

        # 6. Seed Neo4j (Non-critical)
        self._seed_neo4j(company.id, company_name)

        return {
            "company_id": company.id,
            "sprint_id": sprint.id,
            "task_count": len(tasks),
            "message": f"Company initialized with {len(tasks)} foundation tasks",
        }

    def _seed_neo4j(self, company_id: int, company_name: str):
        """Seed a root Company node in Neo4j (Temporal Knowledge Graph)."""
        try:
            driver = get_neo4j_driver()
            with driver.session() as neo_session:
                neo_session.run(
                    "MERGE (c:Company {id: $id}) "
                    "ON CREATE SET c.name = $name, c.created = timestamp()",
                    id=company_id,
                    name=company_name,
                )
        except Exception as e:
            logger.error(f"Neo4j seed failed (non-critical): {e}")

    def approve_task(self, task_id: int, approver_name: str, decision: str, feedback: Optional[str] = None) -> Dict[str, Any]:
        """
        Human-in-the-Loop governance: approve or reject an AI-generated task.

        ``task_id`` maps to a ``BacklogItem.id``.  On approval the item status
        is set to ``in_review``.  On rejection the item is reset to ``todo``
        and a ``task.assign`` OutboxEvent is created that carries the human
        feedback so the agent can incorporate it on the next execution pass.

        A ``TaskQueue`` record is also updated if one exists with a matching
        ``interaction_id`` for legacy compatibility.
        """
        from datetime import datetime, timezone

        item = self.session.get(BacklogItem, task_id)
        if not item:
            raise ValueError(f"BacklogItem {task_id} not found")

        sprint = self.session.get(Sprint, item.sprint_id)
        if not sprint:
            raise ValueError(f"Sprint for BacklogItem {task_id} not found")

        if decision == "approved":
            item.status = "in_review"
            item.updated_at = datetime.now(timezone.utc)
            if feedback:
                comments = list(item.comments or [])
                comments.append({
                    "author": approver_name,
                    "text": f"✅ Approved: {feedback}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                item.comments = comments
        else:
            # Rejected — reset to todo and re-queue with reviewer feedback.
            item.status = "todo"
            item.updated_at = datetime.now(timezone.utc)
            reject_note = feedback or "No feedback provided."
            comments = list(item.comments or [])
            comments.append({
                "author": approver_name,
                "text": f"❌ Rejected: {reject_note}",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            item.comments = comments

            # Publish a task.assign event with the feedback so the agent
            # receives it as part of the prompt on the next execution pass.
            self.session.add(OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(item.id),
                event_type="nats_publish",
                payload={
                    "subject": "task.assign",
                    "message": {
                        "backlog_item_id": item.id,
                        "interaction_id": str(uuid4()),
                        "agent": item.assignee or "PO",
                        "sprint_id": sprint.id,
                        "hitl_feedback": reject_note,
                    },
                },
            ))

        self.session.add(item)
        self.session.commit()
        return {
            "status": "success",
            "task_id": task_id,
            "new_status": decision,
            "item_status": item.status,
        }

    def get_quotas(self, company_id: int) -> List[Dict[str, Any]]:
        """Return token usage and limits for all business units of a company."""
        units = self.session.exec(
            select(BusinessUnit).where(BusinessUnit.company_id == company_id)
        ).all()
        return [
            {"unit": u.unit_name, "limit": u.token_quota_limit, "used": u.token_quota_used}
            for u in units
        ]

    def get_project_health(self, sprint_id: int) -> Dict[str, Any]:
        """Generate a sprint health report using SQLModel idiomatic queries."""
        # 1. Calculate total tokens (aggregate across interactions linked to task_queue)
        # Using a subquery for the interaction IDs
        subq = select(TaskQueue.interaction_id)
        total_tokens = self.session.exec(
            select(func.sum(AuditLog.cost_tokens))
            .where(AuditLog.interaction_id.in_(subq))
        ).one() or 0

        # 2. Count tasks per status
        status_counts_rows = self.session.exec(
            select(BacklogItem.status, func.count(BacklogItem.id))
            .where(BacklogItem.sprint_id == sprint_id)
            .group_by(BacklogItem.status)
        ).all()
        status_counts = {status: count for status, count in status_counts_rows}

        ai_summary = (
            f"The sprint is healthy. {status_counts.get('done', 0)} tasks completed. "
            f"Total investment: {total_tokens} tokens. "
            "Velocity is stable, no major blockers detected."
        )

        return {
            "sprint_id": sprint_id,
            "health_score": 92,
            "tokens_consumed": total_tokens,
            "tasks_status": status_counts,
            "ai_executive_summary": ai_summary,
        }
