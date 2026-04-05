"""
DiscoveryService — automated project stack detection and AI team composition.

Analyses a project path (file system structure, configuration files, etc.) to
determine the technology stack in use, then recommends the ideal composition of
AI agents for that project.

The current implementation returns a deterministic placeholder result.
Future phases will replace this with real file-system introspection, parsing
``package.json``, ``pom.xml``, ``requirements.txt``, Docker files, etc.
"""

import json
from typing import Optional
from sqlmodel import Session
from models import ProjectDiscovery


class DiscoveryService:
    """
    Scans a project directory and persists the detected stack and team suggestion.

    Args:
        session: Active SQLModel session used for persisting the discovery result.
    """

    def __init__(self, session: Session):
        self.session = session

    def discover_project_team(self, company_id: Optional[int], project_path: str):
        """
        Scan a project path and recommend an AI engineering team.

        Steps:
        1. Analyse the project to detect the technology stack.
        2. Map the detected stack to a recommended team of AI agents.
        3. Persist a ``ProjectDiscovery`` row ONLY if company_id is provided.

        Args:
            company_id:   (Optional) Tenant owning this project.  Pass ``None``
                          to run discovery without persisting the result.
            project_path: Filesystem path to the project root to analyse.

        Returns:
            Tuple of ``(discovery_orm_row_or_none, detected_stack_dict, suggested_team_list)``.
        """
        # --- Stack detection (stub — replace with real file introspection) ---
        # Future enhancement: derive company name from folder name if no ID provided.
        folder_name = project_path.split("/")[-1] or "New Project"
        
        detected_stack = {
            "name": folder_name,
            "language": "TypeScript",
            "framework": "React",
            "database": "Postgres",
            "complexity": "Medium",
        }

        # Map detected stack complexity → optimal agent team composition.
        suggested_team = [
            {"role": "PO", "seniority": "Senior", "specialty": "Product Management"},
            {"role": "Architect", "seniority": "Staff", "specialty": "Cloud/DB"},
            {"role": "Developer", "seniority": "Mid", "specialty": "Frontend/React"},
            {"role": "QA", "seniority": "Senior", "specialty": "Automation"},
        ]

        discovery = None
        if company_id:
            discovery = ProjectDiscovery(
                company_id=company_id,
                project_path=project_path,
                detected_stack=json.dumps(detected_stack),
                suggested_team=json.dumps(suggested_team),
                status="completed",
            )

            self.session.add(discovery)
            self.session.commit()
            self.session.refresh(discovery)

        return discovery, detected_stack, suggested_team
