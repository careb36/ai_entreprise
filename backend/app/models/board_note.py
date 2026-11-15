"""The sprint board: short, typed notes the tasks of one sprint leave each other (issue #439).

One row per note, company-scoped and kept with its sprint. The note itself
(kind, text, links, author, the note it supersedes) is never edited after it
is written: what changes is its ``status`` (``active``, ``superseded`` when a
later note replaces it, ``marked_wrong`` when a person says it is wrong),
with who changed it, when and why. Each write is also a task event
(``board_note``) with its author, so the record says who claimed what.

A note is a claim, never evidence (``docs/POSITIONING.md``). There is no
field here for what was observed: the board reads that from the Verifier's
records when it shows a note (``services/evidence/touched.py``).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class SprintBoardNote(SQLModel, table=True):
    __tablename__ = "sprint_board_notes"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    # decision, interface_change, schema_change, gotcha, question
    # (``services/contracts/board_notes.KINDS``).
    kind: str
    text: str
    # The role that wrote it ("person" for a person), its name or email, and
    # the task whose delivery carried it (none for a person's note).
    author_role: str
    author_name: str | None = None
    author_task_id: int | None = Field(
        default=None, foreign_key="backlog_items.id", index=True
    )
    # The run whose deliverable carried the note: the claim's source.
    run_id: int | None = Field(default=None, foreign_key="agent_runs.id")
    # {"files": [...], "symbols": [...], "requirements": [...], "modules": [...]}
    links: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    supersedes_id: int | None = Field(default=None, foreign_key="sprint_board_notes.id")
    status: str = "active"
    status_by: str | None = None
    status_at: datetime | None = None
    status_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
