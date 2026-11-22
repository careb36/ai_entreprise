"""A person's statement for one section of a project's documentation pack (issue #530, phase 2).

Some sections of a pack are decisions no record holds and no agent may take:
the charter's business justification, expected benefits, budget envelope and
sponsor, the risk register's risk appetite, and the check results a person
must judge (within budget, outside stakeholders). The catalogue is
``services/planning/project_docs/statements.SECTIONS``. An Owner or a Manager
writes them here (``PUT /v1/companies/{id}/documentation/person-sections/{section}``).

Rows are append-only: every write is a new row, the newest one per section is
the current statement, and a withdrawal is a row with ``text`` None. A
statement is a **claim** by its author, never an observation: it is kept in
its own table, apart from every compiled field, and a pack quotes it as the
person's statement with the author's company role and the time it was
recorded. ``statement_sha256`` is computed by the backend over the stored
fields, never taken from the request. The author's address stays in the
database and the audit; the pack written to the repository shows only the
role (rule 6 of ``docs/archive/design/PROJECT_DOCUMENTATION.md``).
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class PersonStatement(SQLModel, table=True):
    __tablename__ = "person_statements"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # The catalogue key, ``<document>.<section>`` (``charter.budget``).
    section: str
    # What the person wrote; None records a withdrawal.
    text: str | None = None
    # The author's effective company role when it was recorded (Owner, Manager).
    author_role: str
    # Who recorded it (their address, or "local demo" without sign-in).
    recorded_by: str
    recorded_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    # The current statement this row replaces, if any.
    supersedes_id: int | None = None
    statement_sha256: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
