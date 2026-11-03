"""A person's review of a run, the way a pull request is reviewed.

``ReviewComment`` is a note on one line of a changed file (``path`` and
``line``, on the ``new`` or ``old`` side), or on the run as a whole (no
``path``). ``RunReview`` is a submitted review, ``comment``, ``approve`` or
``request_changes``, with what it did (``outcome``). Both are a person's
words: they decide what happens next and are never evidence.
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

REVIEW_EVENTS = ("comment", "approve", "request_changes")
COMMENT_SIDES = ("new", "old")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RunReview(SQLModel, table=True):
    __tablename__ = "run_reviews"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    agent_run_id: int = Field(foreign_key="agent_runs.id", index=True)
    event: str
    body: str | None = None
    # recorded | sent_back | accepted | approval_recorded
    outcome: str
    author_name: str
    author_user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=_utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")


class ReviewComment(SQLModel, table=True):
    __tablename__ = "review_comments"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    agent_run_id: int = Field(foreign_key="agent_runs.id", index=True)
    path: str | None = None
    line: int | None = None
    side: str = "new"
    body: str
    author_name: str
    author_user_id: int | None = Field(default=None, foreign_key="users.id")
    # The review that carried it; empty while it waits for one.
    review_id: int | None = Field(default=None, foreign_key="run_reviews.id")
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
