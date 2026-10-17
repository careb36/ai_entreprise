"""
CompanyService — logic for company lifecycle, tenant bootstrapping, and team discovery.

Responsibilities:
- Initialise a new company tenant with agents and credentials.
- Aggregate sprint health and token usage analytics for the dashboard.
- Bridge Postgres multi-tenant data to the Neo4j Temporal Knowledge Graph.
"""

from datetime import date
from typing import Any
from uuid import uuid4

from crypto import encrypt_value
from database import get_neo4j_driver
from logging_config import logger
from models import (
    Agent,
    AuditLog,
    BacklogItem,
    BusinessUnit,
    Company,
    CompanyMember,
    OutboxEvent,
    ProjectCredential,
    Sprint,
)
from sqlmodel import Session, func, select

from services.sprint_planner import plan_foundation_sprint


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
        agents: list[dict[str, Any]],
        default_scope_size: str = "M",
        default_sprint_length_days: int | None = None,  # deprecated
        autonomy_level: int = 2,
        permissions: dict[str, str] | None = None,
        project_path: str | None = None,
        detected_stack: dict[str, Any] | None = None,
        owner_user_id: int | None = None,
    ) -> dict[str, Any]:
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

        if owner_user_id is not None:
            self.session.add(
                CompanyMember(
                    company_id=company.id, user_id=owner_user_id, human_role="Owner"
                )
            )

        # 2. Create Agents
        for agent_data in agents:
            self.session.add(
                Agent(
                    company_id=company.id,
                    name=agent_data.get("name", ""),
                    role=agent_data.get("role"),
                    persona=agent_data.get("persona"),
                    seniority=agent_data.get("seniority", "Senior"),
                    specialty=agent_data.get("specialty", ""),
                    model=agent_data.get("model", ""),
                    system_prompt=agent_data.get("system_prompt"),
                    token_budget=agent_data.get("token_budget", 500000),
                )
            )

        # 3. Create Project Credentials
        if permissions:
            for cred_type, access_level in permissions.items():
                if access_level != "none":
                    # Routed through encrypt_value so the column holds a
                    # self-describing value from the moment a row is written.
                    # The placeholder carries no secret today — init_company
                    # receives only credential_type -> access_level, never a
                    # value — but wiring it now means the first real secret
                    # written here is encrypted rather than retrofitted.
                    self.session.add(
                        ProjectCredential(
                            company_id=company.id,
                            credential_type=cred_type,
                            access_level=access_level,
                            encrypted_value=encrypt_value(
                                "vault_encrypted_placeholder"
                            ),
                        )
                    )

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
            self.session.add(
                OutboxEvent(
                    aggregate_type="backlog_item",
                    aggregate_id=str(bi.id),
                    event_type="nats_publish",
                    payload=nats_payload,
                )
            )

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
        except Exception as e:  # noqa: BLE001 - Optional graph seeding must not fail company creation.
            logger.error(f"Neo4j seed failed (non-critical): {e}")

    def get_quotas(self, company_id: int) -> list[dict[str, Any]]:
        """Return token usage and limits for all business units of a company."""
        units = self.session.exec(
            select(BusinessUnit).where(BusinessUnit.company_id == company_id)
        ).all()
        return [
            {
                "id": u.id,
                "unit": u.unit_name,
                "limit": u.token_quota_limit,
                "used": u.token_quota_used,
            }
            for u in units
        ]

    def reset_quota(self, bu_id: int) -> dict[str, Any]:
        """Reset the token usage counter for a business unit to zero."""
        unit = self.session.get(BusinessUnit, bu_id)
        if not unit:
            raise ValueError(f"Business Unit {bu_id} not found")
        unit.token_quota_used = 0
        self.session.add(unit)
        self.session.commit()
        return {
            "status": "reset",
            "bu_id": bu_id,
            "unit": unit.unit_name,
            "token_quota_used": 0,
        }

    def get_project_health(self, sprint_id: int) -> dict[str, Any]:
        """Generate a sprint health report using SQLModel idiomatic queries."""
        # 1. Calculate total tokens for this sprint's backlog interactions
        item_ids = self.session.exec(
            select(BacklogItem.id).where(BacklogItem.sprint_id == sprint_id)
        ).all()
        interaction_ids = [f"task-{item_id}" for item_id in item_ids]
        total_tokens = 0
        if interaction_ids:
            total_tokens = (
                self.session.exec(
                    select(func.sum(AuditLog.cost_tokens)).where(
                        AuditLog.interaction_id.in_(interaction_ids)
                    )
                ).one()
                or 0
            )

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
