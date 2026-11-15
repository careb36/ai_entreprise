"""Typed relations between tasks, across sprints (issue #533).

One row per pair of tasks of one company: ``task_id`` is ``kind`` of
``related_task_id`` (``blocked_by``, ``duplicate`` or ``relates_to``,
``services/tasks/relations.KINDS``). The reverse name (``blocking`` for
``blocked_by``) is never stored: it is read from one fixed table when the
relation is shown from the other task. ``pair_low`` / ``pair_high`` are the two
task ids in order, so the unique constraint holds one row per pair whichever
way round it was written.

A relation an agent proposes stays ``proposed`` until a person or a
deterministic rule (carry-over) confirms it; only a confirmed ``blocked_by``
between two tasks of one sprint reaches dispatch, through the task's
``depends_on`` (``added_dependency`` says the confirmation added it there).
Every change is also a ``relation`` task event on both tasks, so their hash
chains cover it.

The design follows Plane's ``IssueRelation`` (``db/models/issue.py``,
``utils/issue_relation_mapper.py``; AGPL-3.0, design only, no code taken).
"""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TaskRelation(SQLModel, table=True):
    __tablename__ = "task_relations"
    __table_args__ = (UniqueConstraint("pair_low", "pair_high"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    task_id: int = Field(foreign_key="backlog_items.id", index=True)
    related_task_id: int = Field(foreign_key="backlog_items.id", index=True)
    pair_low: int
    pair_high: int
    kind: str
    # ``proposed`` or ``confirmed``.
    state: str = "proposed"
    # Who proposed it (an email, a role name or ``system``) and as what
    # (``person``, ``system`` or the agent's role).
    proposed_by: str
    proposed_role: str
    # A person's email, or ``rule:<name>`` for a deterministic rule.
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    added_dependency: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
