"""The intake queue: requests that reach a company, until a person decides (issue #532).

A request comes from a support ticket, the client's brief, an inbound call
with an API token or a chat command (``source``, set by the backend from the
way it arrived, never by the sender). It waits ``pending``; a Manager accepts
it (it becomes a task or a specification revision, and both links are kept),
declines it with a reason, marks it a duplicate of another item or a task, or
snoozes it until a date, when it comes back by itself.

Three parties write here and none overwrites another (``docs/POSITIONING.md``):
the sender's words (``title``, ``body``), the Support agent's suggestion
(``suggested_*``, a claim) and a person's decision (``decided_*``), as on
``BugReport``. Every status change is an audit row with its actor
(``services/planning/intake_queue.py``).
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IntakeItem(SQLModel, table=True):
    __tablename__ = "intake_items"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # ticket, brief, webhook, chat (``services/contracts/intake_rules.SOURCES``).
    source: str
    # What the source calls it: a ticket number, an API token's id, a chat
    # workspace. Provenance, set by the backend.
    source_ref: str | None = None
    # Who sent it, as the backend authenticated them.
    submitted_by: str | None = None
    title: str
    body: str = ""
    # pending, accepted, declined, duplicate, snoozed.
    status: str = Field(default="pending", index=True)
    snoozed_until: datetime | None = None
    # What it repeats: another intake item, or a task.
    duplicate_of_item_id: int | None = Field(
        default=None, foreign_key="intake_items.id"
    )
    duplicate_of_task_id: int | None = Field(
        default=None, foreign_key="backlog_items.id"
    )
    # What it became: a task, or a specification version.
    outcome_task_id: int | None = Field(default=None, foreign_key="backlog_items.id")
    outcome_specification_id: int | None = Field(
        default=None, foreign_key="specifications.id"
    )
    # The Support agent's suggestion: a claim, beside the decision.
    suggested_status: str | None = None
    suggested_category: str | None = None
    suggested_duplicate_of: str | None = None  # "intake:3" or "task:7"
    suggested_reason: str | None = None
    triaged_by: str | None = None
    triaged_at: datetime | None = None
    # A person's latest decision, apart from the suggestion.
    decided_status: str | None = None
    decided_category: str | None = None
    decision_reason: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
