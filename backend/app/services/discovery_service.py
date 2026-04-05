import json
from sqlmodel import Session
from models import ProjectDiscovery

class DiscoveryService:
    def __init__(self, session: Session):
        self.session = session

    def discover_project_team(self, company_id: int, project_path: str):
        """Scans a project path to detect stack and suggest the ideal AI team."""
        
        # Placeholder for real analysis logic (to be expanded in later phases)
        detected_stack = {
            "language": "TypeScript",
            "framework": "React",
            "database": "Postgres",
            "complexity": "Medium",
        }
        
        suggested_team = [
            {"role": "PO", "seniority": "Senior", "specialty": "Product Management"},
            {"role": "Architect", "seniority": "Staff", "specialty": "Cloud/DB"},
            {"role": "Developer", "seniority": "Mid", "specialty": "Frontend/React"},
            {"role": "QA", "seniority": "Senior", "specialty": "Automation"},
        ]
        
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
