"""
Sprint board router — the notes a sprint's tasks leave each other (issue #439).

Endpoints:
- ``GET  /v1/sprints/{sprint_id}/board_notes``  — the sprint's notes, newest
  first, each with what the Verifier's records observed about the files it
  names, and the board's numbers (Viewer).
- ``POST /v1/sprints/{sprint_id}/board_notes``  — a person adds a note (Manager).
- ``POST /v1/board_notes/{note_id}/mark_wrong`` — a person marks a note wrong
  (Manager).

A note is a claim: its fields are what its author said. ``observed`` is the
platform's own reading of the evidence, kept apart. Both writes are audited
under ``sprint:<id>:board`` and recorded as ``board_note`` task events.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Sprint, SprintBoardNote, User
from pydantic import BaseModel, Field
from services.contracts import board_notes
from services.loop.worker import sprint_notes
from services.platform.agent_service import AgentService
from services.tasks import sprint_board
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Agents"])


def _links() -> Any:
    return Field(default_factory=list, max_length=board_notes.MAX_LINKS_PER_KIND)


class BoardNoteCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=board_notes.TEXT_LIMIT)
    files: list[str] = _links()
    symbols: list[str] = _links()
    requirements: list[str] = _links()
    modules: list[str] = _links()
    supersedes_id: int | None = None


class MarkWrongRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=board_notes.REASON_LIMIT)


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _sprint(session: Session, sprint_id: int) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return sprint


def _audit(
    session: Session, sprint: Sprint, actor: str, action: str, result: dict[str, Any]
) -> None:
    """One audit row for a board write; commits the session."""
    AgentService().audit_interaction(
        session=session,
        company_id=sprint.company_id,
        interaction_id=f"sprint:{sprint.id}:board",
        agent_id=f"human:{actor}",
        action=action,
        details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _with_observed(
    session: Session, sprint: Sprint, notes: list[SprintBoardNote]
) -> list[dict[str, Any]]:
    return sprint_notes.with_observations(
        session, sprint.company_id, [sprint_board.as_dict(n) for n in notes]
    )


@router.get("/sprints/{sprint_id}/board_notes")
async def list_board_notes(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The sprint's notes, newest first, every status; and the board's numbers."""
    sprint = _sprint(session, sprint_id)
    require_company_role(session, user, sprint.company_id, "Viewer")
    notes = sprint_board.notes_of(session, sprint.id)
    return {
        "notes": _with_observed(session, sprint, notes),
        "metrics": sprint_board.sprint_metrics(session, sprint.id),
        "kinds": list(board_notes.KINDS),
    }


@router.post("/sprints/{sprint_id}/board_notes", status_code=201)
async def add_board_note(
    sprint_id: int,
    req: BoardNoteCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person's note on the sprint's board; audited as ``board_note_added``."""
    sprint = _sprint(session, sprint_id)
    require_company_role(session, user, sprint.company_id, "Manager")
    if sprint.archived_at is not None:
        raise HTTPException(status_code=409, detail="The sprint is archived")
    actor = _actor(user)
    try:
        note = sprint_board.add(
            session,
            sprint,
            kind=req.kind,
            text=req.text,
            author_role=sprint_board.PERSON,
            author_name=actor,
            links={
                "files": req.files,
                "symbols": req.symbols,
                "requirements": req.requirements,
                "modules": req.modules,
            },
            supersedes_id=req.supersedes_id,
        )
    except sprint_board.BoardNoteError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    _audit(
        session,
        sprint,
        actor,
        "board_note_added",
        {"note_id": note.id, "kind": note.kind, "supersedes_id": note.supersedes_id},
    )
    session.refresh(note)
    return _with_observed(session, sprint, [note])[0]


@router.post("/board_notes/{note_id}/mark_wrong")
async def mark_board_note_wrong(
    note_id: int,
    req: MarkWrongRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person says the note is wrong; audited as ``board_note_marked_wrong``."""
    note = session.get(SprintBoardNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    require_company_role(session, user, note.company_id, "Manager")
    sprint = _sprint(session, note.sprint_id)
    actor = _actor(user)
    previous = note.status
    try:
        sprint_board.mark_wrong(session, note, actor, req.reason)
    except sprint_board.BoardNoteError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    _audit(
        session,
        sprint,
        actor,
        "board_note_marked_wrong",
        {"note_id": note.id, "previous_status": previous, "reason": note.status_reason},
    )
    session.refresh(note)
    return _with_observed(session, sprint, [note])[0]
