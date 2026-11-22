"""Who asked for a piece of work, read from records (issue #543;
``docs/archive/design/SEGREGATION_OF_DUTIES.md``).

The segregation-of-duties rule (``services/platform/sod.py``) compares the
person approving with these; each is an observation the backend wrote, never
what the approver or an agent says:

- a sprint's tasks, runs and integration: the account that started the sprint
  (``sprints.started_by_user_id``, from the session at ``POST /v1/start_sprint``;
  empty for a sprint the platform opened, which is unattributed);
- a run: its task's sprint, else the sprint its subject names;
- a specification or an architecture: the person whose email is in
  ``drafted_by`` (the last person who wrote or revised the draft; an agent's
  draft names no person).
"""

from __future__ import annotations

from models import AgentRun, Architecture, BacklogItem, Specification, Sprint
from sqlmodel import Session

from services.platform.sod import Requester, person_of_email, person_of_user_id

STARTED = "started the sprint"
DRAFTED_SPEC = "drafted the specification"
DRAFTED_ARCH = "drafted the architecture"


def of_sprint(session: Session, sprint: Sprint | None) -> list[Requester]:
    if sprint is None or sprint.started_by_user_id is None:
        return []
    return [Requester(person_of_user_id(session, sprint.started_by_user_id), STARTED)]


def sprint_of_run(session: Session, run: AgentRun) -> Sprint | None:
    """The sprint a run's work belongs to, in the run's own company."""
    subject = run.subject or {}
    sprint_id = subject.get("sprint_id")
    item_id = subject.get("backlog_item_id")
    if isinstance(item_id, int):
        item = session.get(BacklogItem, item_id)
        if item is not None:
            sprint_id = item.sprint_id
    if not isinstance(sprint_id, int):
        return None
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or sprint.company_id != run.company_id:
        return None
    return sprint


def of_run(session: Session, run: AgentRun) -> list[Requester]:
    return of_sprint(session, sprint_of_run(session, run))


def of_specification(session: Session, spec: Specification) -> list[Requester]:
    person = person_of_email(session, spec.drafted_by)
    return [Requester(person, DRAFTED_SPEC)] if person.known() else []


def of_architecture(session: Session, arch: Architecture) -> list[Requester]:
    person = person_of_email(session, arch.drafted_by)
    return [Requester(person, DRAFTED_ARCH)] if person.known() else []
