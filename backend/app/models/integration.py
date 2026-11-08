"""A sprint's task branches joined into one branch, and what that showed.

``SprintIntegration`` is one attempt at integrating a closed sprint (issue
#314, ``services/integration.py``): the workspace daemon merged the done
tasks' branches into ``fabrique/sprint-<id>`` in dependency order and the
project's test command ran on the result. Every field below the request is
an observation: what git and the daemon answered, never what an agent said.
``publication`` is the daemon's answer when a Manager published the branch
as one pull or merge request for the sprint.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
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
    created_at: datetime = Field(default_factory=_utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")
