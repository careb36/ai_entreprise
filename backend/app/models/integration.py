"""A sprint's task branches joined into one branch, and what that showed.

``SprintIntegration`` is one attempt at integrating a closed sprint (issue
#314, ``services/release/integration.py``): the workspace daemon merged the done
tasks' branches into ``fabrique/sprint-<id>`` in dependency order and the
project's test command ran on the result. Every field below the request is
an observation: what git and the daemon answered, never what an agent said.
``publication`` is the daemon's answer when a Manager published the branch
as one pull or merge request for the sprint.

``OutboxEvent`` is the transactional outbox row (``services/integrations/
outbox_service.py``): the domain change and its NATS delivery write in one
database transaction, then a background loop drains pending rows. Its
``correlation_id`` is generated once at enqueue time and never regenerated
on retry or redelivery; it is distinct from ``Nats-Msg-Id`` which stays for
dedup only (Harden async task-worker observability and failure replay).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel

# untested says nothing about the tests; incomplete: a done task's branch was
# missing or refused; running: a closed sprint's integration kept before the
# daemon is asked (a restart leaves it to be retried); pending: the daemon
# could not be reached when the sprint closed (retried); error: the daemon
# refused the integration.
INTEGRATION_STATUSES = (
    "tests_passed",
    "tests_failed",
    "untested",
    "conflict",
    "incomplete",
    "running",
    "pending",
    "error",
)
WAITS_FOR_A_PERSON = ("conflict", "tests_failed", "incomplete", "pending", "error")
# What may be published as the sprint's request.
PUBLISHABLE = ("tests_passed", "tests_failed", "untested")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OutboxEvent(SQLModel, table=True):
    """One transactional outbox row, drained into NATS by ``process_outbox``.

    ``correlation_id`` is generated exactly once, at enqueue time, and never
    regenerated on retry or redelivery. It flows from here through JetStream
    consume, daemon call, verification and ``AgentRun`` so every async run is
    traceable end-to-end in logs and spans. It is distinct from ``Nats-Msg-Id``,
    which keeps its dedup role.
    """

    __tablename__ = "outbox_events"

    id: int | None = Field(default=None, primary_key=True)
    event_type: str
    payload: dict | None = Field(default=None, sa_column=Column(JSON))
    correlation_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(UUID(as_uuid=True), index=True),
    )
    status: str = Field(default="pending", index=True)
    retry_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)
    processed_at: datetime | None = None

    def ensure_correlation_id(self) -> uuid.UUID:
        """Return the stable id, generating it exactly once.

        Generation happens only for rows that predate a migration or an
        enqueue path not using ``new_correlation_id``; a caller that already
        assigned one gets it back unchanged so a retry can never rotate it.
        """
        if self.correlation_id is None:
            self.correlation_id = uuid.uuid4()
        return self.correlation_id


class SprintIntegration(SQLModel, table=True):
    __tablename__ = "sprint_integrations"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    # sprint_closed (the worker, when the sprint closed) or manual (a Manager).
    trigger: str
    requested_by: str
    status: str
    branch: str
    base: str
    repository: str | None = None
    worktree: str | None = None
    # Where the branch stood before this attempt, and after it.
    start_commit: str | None = None
    commit: str | None = None
    # [{task_id, branch, commit}] in the order merged.
    merged: list | None = Field(default=None, sa_column=Column(JSON))
    # {task_id, branch, files, hunks, message}: the merge git could not make.
    conflict: dict | None = Field(default=None, sa_column=Column(JSON))
    # The daemon's test run (workspace_daemon.record_test_run), in a worktree
    # detached at tested_commit, so a later integration cannot move it.
    test_run: dict | None = Field(default=None, sa_column=Column(JSON))
    tested_commit: str | None = None
    # Per done task, at integration time: {task_id, title, branch, state,
    # source_commit, merge_commit, run_id, verification_id}. The request's
    # description is built from these, never from a run recorded later.
    tasks: list | None = Field(default=None, sa_column=Column(JSON))
    # already_merged, missing, refused, not_attempted, left_out (tasks not done).
    details: dict | None = Field(default=None, sa_column=Column(JSON))
    publication: dict | None = Field(default=None, sa_column=Column(JSON))
    # The company's forge policy this integration ran under (issue #727,
    # services/contracts/forge_policy.py); None for one recorded before 0053.
    forge_policy: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")
