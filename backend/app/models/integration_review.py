"""A person's review of a sprint's integration, the way a pull request is reviewed.

The integration (``SprintIntegration``, ``fabrique/sprint-<id>``) is the one
request of a sprint a person reads (issue #314, F7 of the self-hosting
retrospective). ``IntegrationReviewComment`` is a note on a line of a file
the integration changes, or on the integration as a whole (no ``path``);
``IntegrationReview`` is a submitted review, ``comment``, ``approve`` or
``request_changes``.

The words (``body``, the verdict) are the person's: claims, never evidence.
What they were said about is observed and kept apart: the integration's
``commit``, the ``merge_base`` and, for a comment on a line, the sha256 of the
file on that side, all from the workspace daemon's diff (``workspace.git.diff``),
and ``diff_sha256``, which the backend computes from the daemon's per-file
hashes. Nothing here is taken from the client but the words and where they go.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

# What a submitted review did: comment → recorded, approve → approved,
# request_changes → changes_requested.
INTEGRATION_REVIEW_OUTCOMES = ("recorded", "approved", "changes_requested")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IntegrationReview(SQLModel, table=True):
    __tablename__ = "integration_reviews"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    integration_id: int = Field(foreign_key="sprint_integrations.id", index=True)
    event: str
    body: str | None = None
    outcome: str
    # Observed: what the daemon's diff was of when the review was submitted.
    commit: str
    merge_base: str | None = None
    diff_sha256: str
    files: int = 0
    # The integration's pull or merge request, when it was published by then.
    published_url: str | None = None
    # The segregation-of-duties outcome of an approval (services/platform/sod.py).
    sod: str | None = None
    # The protected paths a signed-in Owner named in an approval (migration
    # 0042; services/release/protected_merge.py): their decision, recorded
    # beside the diff_sha256 it was made on.
    protected_paths_ack: list | None = Field(default=None, sa_column=Column(JSON))
    author_name: str
    author_user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=_utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")


class IntegrationReviewComment(SQLModel, table=True):
    __tablename__ = "integration_review_comments"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    integration_id: int = Field(foreign_key="sprint_integrations.id", index=True)
    path: str | None = None
    line: int | None = None
    side: str = "new"
    body: str
    # Observed: the commit and, on a line, the file's sha256 on that side.
    commit: str
    file_sha256: str | None = None
    author_name: str
    author_user_id: int | None = Field(default=None, foreign_key="users.id")
    # The review that carried it; empty while it waits for one.
    review_id: int | None = Field(default=None, foreign_key="integration_reviews.id")
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
