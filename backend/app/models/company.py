from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Company(SQLModel, table=True):
    __tablename__ = "companies"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str
    default_sprint_length_days: int = 14
    autonomy_level: int = 2


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    role: Optional[str] = None
    persona: Optional[str] = None
    seniority: str = "Senior"
    specialty: str = ""


class BusinessUnit(SQLModel, table=True):
    __tablename__ = "business_units"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    unit_name: str
    token_quota_limit: int = 0
    token_quota_used: int = 0


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    full_name: str
    password_hash: str


class CompanyMember(SQLModel, table=True):
    __tablename__ = "company_members"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    user_id: int = Field(foreign_key="users.id")
    human_role: str  # 'Owner', 'Manager', 'Viewer'


class ProjectCredential(SQLModel, table=True):
    __tablename__ = "project_credentials"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    credential_type: str
    access_level: str
    encrypted_value: str


class ProjectDiscovery(SQLModel, table=True):
    __tablename__ = "project_discovery"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    project_path: str
    detected_stack: Optional[str] = None   # JSON string
    suggested_team: Optional[str] = None   # JSON string
    status: str = "completed"
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
