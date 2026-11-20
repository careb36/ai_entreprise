"""
Personal notifications router — a person's own notices (issue #683).

Every route answers for the signed-in person only: their own rows, in the
companies they are still a member of. Another person's notice answers 404.
An API token is refused (403): a notice belongs to a person, not a token.

Endpoints:
- ``GET  /v1/notifications?company_id=&state=unread|snoozed|all&limit=`` —
  the person's notices, newest first, with the unread count.
- ``GET  /v1/notifications/count?company_id=`` — the unread count (the badge).
- ``POST /v1/notifications/{notification_id}/read`` — mark read (``read:
  false`` marks it unread again).
- ``POST /v1/notifications/read`` — mark every notice read (of one company
  with ``company_id``).
- ``POST /v1/notifications/{notification_id}/snooze`` — hide it until
  ``until`` (at most 30 days; 24 hours for a security notice); ``until:
  null`` brings it back.
- ``GET  /v1/notifications/preferences`` — each kind's preference.
- ``PUT  /v1/notifications/preferences/{kind}`` — in the app and by email; a
  security notice cannot leave the app (409).

The rules are in ``services/platform/notifications.py``.
"""

from datetime import datetime

from auth_policy import current_user_if_required
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import User
from schemas import STRICT, RequestModel
from services.platform import mail, notifications
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Notifications"])


class ReadBody(RequestModel):
    model_config = STRICT

    read: bool = True


class ReadAllBody(RequestModel):
    model_config = STRICT

    company_id: int | None = None


class SnoozeBody(RequestModel):
    model_config = STRICT

    until: datetime | None = None


class PreferenceBody(RequestModel):
    model_config = STRICT

    in_app: bool = True
    email: bool = False


def _person(user: User | None) -> User:
    """A signed-in person, not an API token (even without AUTH_REQUIRED)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="Notifications belong to a person, not a token"
        )
    return user


def _refused(exc: notifications.NotificationRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


@router.get("/notifications")
def list_notifications(
    company_id: int | None = None,
    state: str = Query(default="unread", max_length=10),
    limit: int = Query(default=50, ge=1, le=notifications.LIST_LIMIT),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    try:
        return notifications.list_mine(session, person.id, company_id, state, limit)
    except notifications.NotificationRefused as exc:
        raise _refused(exc) from exc


@router.get("/notifications/count")
def count_notifications(
    company_id: int | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    return {"unread": notifications.unread_count(session, person.id, company_id)}


@router.post("/notifications/read")
def read_all(
    body: ReadAllBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    return {"read": notifications.mark_all_read(session, person.id, body.company_id)}


@router.post("/notifications/{notification_id}/read")
def read_one(
    notification_id: int,
    body: ReadBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    try:
        return notifications.mark_read(session, person.id, notification_id, body.read)
    except notifications.NotificationRefused as exc:
        raise _refused(exc) from exc


@router.post("/notifications/{notification_id}/snooze")
def snooze_one(
    notification_id: int,
    body: SnoozeBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    try:
        return notifications.snooze(session, person.id, notification_id, body.until)
    except notifications.NotificationRefused as exc:
        raise _refused(exc) from exc


@router.get("/notifications/preferences")
def get_preferences(
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    return {
        "preferences": list(notifications.preferences(session, person.id).values()),
        "email_available": mail.enabled(),
    }


@router.put("/notifications/preferences/{kind}")
def put_preference(
    kind: str,
    body: PreferenceBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    try:
        return notifications.set_preference(
            session, person.id, kind, body.in_app, body.email
        )
    except notifications.NotificationRefused as exc:
        raise _refused(exc) from exc
