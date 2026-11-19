"""
Decisions from Slack and Teams — the public inbound endpoints (issue #512, phase 4).

- ``POST /v1/integrations/slack/{public_id}/interactions`` — Slack's
  interactivity Request URL (Block Kit buttons) and the ``/fabrique`` slash
  command's Request URL.
- ``POST /v1/integrations/teams/{public_id}/messages`` — a Teams outgoing
  webhook's callback URL (``@Fabrique approve CODE``, ``sendback CODE
  <reason>``, ``link CODE``).

A task blocked for a person's triage (no risk hold) is posted with Send back
only; it goes through the board's own ``services/loop/board_moves.send_back``
after the board's role check (Manager), with the reason in its note. Slack's
``/fabrique sendback CODE <reason>`` carries a reason for either point.

Both also take ``request <text>`` (issue #532): a linked account with at
least the Viewer role sends a request to the company's intake queue, source
``chat`` (``services/planning/intake_queue.py``); a Manager decides it in the web.

Each request is refused unless, in this order: the URL names an enabled
connection; the platform's signature holds over the raw body
(``services/integrations/chat_signatures.py``; Slack's timestamp, or the
Teams activity's, within five minutes); its interaction id was never seen
(``chat_interactions``); it comes from the connection's workspace or tenant;
the chat user is linked to a La Fabrique account in the company; that account
has the role the web asks for (``require_company_role(..., "Manager")``, as
``POST /v1/agent_runs/{id}/reviews``); and the message still matches the
record (``chat_approvals.claim``). Then the web's own function decides
(``services/loop/run_review.submit_review``, with ``via``), an audit row names
the person and the platform, and the chat message is updated through the
outbox. Nothing in the payload but the platform's user id is read as
identity; no role is ever read from it. Refusals change nothing and say why
in chat (an ephemeral message in Slack; a thread reply in Teams).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

from auth_policy import require_company_role
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from logging_config import logger
from models import (
    BacklogItem,
    ChatApprovalMessage,
    ChatConnection,
    ChatInteraction,
    Sprint,
    User,
)
from security import limiter
from services.integrations import chat_approvals, chat_links, chat_messages
from services.integrations import chat_connections as conns
from services.integrations.chat_signatures import (
    SignatureRefused,
    check_activity_time,
    verify_slack,
    verify_teams,
)
from services.loop import board_moves
from services.loop.run_review import ReviewRefused, submit_review
from services.planning import intake_queue
from services.platform.agent_service import AgentService
from services.tasks.board import SEND_BACK_MARKER
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

router = APIRouter(prefix="/v1/integrations", tags=["Chat approvals"])

MAX_BODY_BYTES = 64 * 1024
# Twelve times the signature window (chat_signatures.MAX_SKEW_SECONDS): an
# older request is refused by its timestamp, so its key need not be kept.
REPLAY_KEEP = timedelta(hours=1)
NAMES = {"slack": "Slack", "teams": "Teams"}
LINK_FIRST = (
    "Your chat account is not linked to La Fabrique. In La Fabrique, open "
    "Chat approvals, ask for a link code and send it here."
)
NEEDS_ROLE = "Deciding needs the Manager role in this company in La Fabrique."
ONLY_SEND_BACK = (
    "This task is blocked for a person's triage: there is no delivery to "
    "approve. Send it back to work with a reason, or decide in La Fabrique."
)


def _rate_limit() -> str:
    return settings.CHAT_RATE_LIMIT


class Refusal(Exception):
    """A decision that cannot be taken; the message is shown in chat."""


async def _body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large")
    return body


def _connection(session: Session, provider: str, public_id: str) -> ChatConnection:
    row = conns.by_public_id(session, provider, public_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


def _signature_refused(provider: str, row: ChatConnection, exc: Exception):
    logger.warning(
        "Chat %s: refused an inbound request for connection %s: %s",
        provider,
        row.id,
        exc,
    )
    return HTTPException(status_code=401, detail="Invalid signature")


def _first_time(session: Session, row: ChatConnection, key: str) -> bool:
    """Store *key* once; ``False`` when it was seen before (a replay).

    Keys older than ``REPLAY_KEEP`` are dropped first, so the store stays
    bounded.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - REPLAY_KEEP
    session.exec(delete(ChatInteraction).where(ChatInteraction.received_at < cutoff))
    session.add(
        ChatInteraction(
            provider=row.provider, interaction_key=key[:200], connection_id=row.id
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def _audit(
    session: Session,
    row: ChatConnection,
    agent_id: str,
    action: str,
    details: dict[str, Any],
    result: dict[str, Any],
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"chat-approval:{row.company_id}",
        agent_id=agent_id,
        action=action,
        details={"company_id": row.company_id, "via": row.provider, **details},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _refuse(
    session: Session,
    row: ChatConnection,
    chat_user: str,
    message: ChatApprovalMessage | None,
    reason: str,
) -> Refusal:
    _audit(
        session,
        row,
        f"{row.provider}:{row.workspace_id}:{chat_user}",
        "chat_approval_refused",
        {"approval_id": message.id if message else None, "chat_user": chat_user},
        {"reason": reason},
    )
    session.commit()
    return Refusal(reason)


def decide(
    session: Session,
    row: ChatConnection,
    *,
    chat_user: str,
    message: ChatApprovalMessage | None,
    decision: str,
    reason: str | None,
    head: str | None = None,
    run_version: int | None = None,
) -> str:
    """Take a person's decision from chat exactly as the web would, or raise ``Refusal``."""
    name = NAMES[row.provider]
    identity = chat_links.identity_of(
        session, row.company_id, row.provider, row.workspace_id, chat_user
    )
    user = session.get(User, identity.user_id) if identity else None
    if user is None:
        raise _refuse(session, row, chat_user, message, LINK_FIRST)
    try:
        require_company_role(session, user, row.company_id, "Manager")
    except HTTPException as exc:
        raise _refuse(session, row, chat_user, message, NEEDS_ROLE) from exc
    if (
        decision == "approve"
        and message is not None
        and chat_approvals.kind_of(session, message) == chat_approvals.TRIAGE
    ):
        raise _refuse(session, row, chat_user, message, ONLY_SEND_BACK)
    try:
        run, item, kind = chat_approvals.claim(
            session, row, message, head=head, run_version=run_version
        )
    except chat_approvals.StaleApproval as exc:
        raise _refuse(session, row, chat_user, message, str(exc)) from exc
    if message is None:  # claim refuses a missing message; this keeps the type
        raise Refusal("This approval is not known here.")
    if kind == chat_approvals.TRIAGE:
        return _send_back(session, row, chat_user, message, user, item, reason)
    author = user.full_name or user.email
    event = "approve" if decision == "approve" else "request_changes"
    body = (reason or "").strip()[:2000] or None
    if event == "request_changes" and body is None:
        body = (
            f"Sent back from {name} without a written reason; see the run's "
            "comments in La Fabrique."
        )
    try:
        review = submit_review(
            session,
            run,
            event=event,
            body=body,
            author_name=author,
            author_user_id=user.id,
            via=row.provider,
        )
    except ReviewRefused as refused:
        session.rollback()
        chat_approvals.release(session, message)
        raise _refuse(session, row, chat_user, message, refused.detail) from refused
    verb = "Approved" if event == "approve" else "Sent back to work"
    outcome = f"{verb} by {author} via {name}."
    chat_approvals.finish(
        session, message, decision=event, user_id=user.id, outcome_text=outcome
    )
    _audit(
        session,
        row,
        f"human:{author}",
        f"chat_approval_{review.outcome}",
        {
            "approval_id": message.id,
            "agent_run_id": run.id,
            "run_version": message.run_version,
            "review_id": review.id,
            "user_id": user.id,
            "chat_user": chat_user,
        },
        {"decision": event, "outcome": review.outcome},
    )
    session.commit()
    return outcome


def _send_back(
    session: Session,
    row: ChatConnection,
    chat_user: str,
    message: ChatApprovalMessage,
    user: User,
    item: BacklogItem,
    reason: str | None,
) -> str:
    """Send a task blocked for triage back to work, as the board's button does.

    The note starts with ``SEND_BACK_MARKER`` (the rework budgets restart)
    and carries the person's reason; ``board_moves.send_back`` writes the
    board's ``sent_back`` event and queues the task. The message was claimed.
    """
    name = NAMES[row.provider]
    author = user.full_name or user.email
    body = (reason or "").strip()[:2000]
    note = f"{SEND_BACK_MARKER} by a person via {name}" + (f": {body}" if body else ".")
    try:
        event, started = board_moves.send_back(
            session, item, session.get(Sprint, item.sprint_id), user, note
        )
    except board_moves.BoardMoveError as refused:
        session.rollback()
        chat_approvals.release(session, message)
        raise _refuse(session, row, chat_user, message, refused.detail) from refused
    outcome = f"Sent back to work by {author} via {name}."
    chat_approvals.finish(
        session, message, decision="send_back", user_id=user.id, outcome_text=outcome
    )
    _audit(
        session,
        row,
        f"human:{author}",
        "chat_approval_sent_back",
        {
            "approval_id": message.id,
            "backlog_item_id": item.id,
            "task_event_id": event.id if event else None,
            "user_id": user.id,
            "chat_user": chat_user,
        },
        {"decision": "send_back", "queued": bool(started.get("queued"))},
    )
    session.commit()
    return outcome


def _link(
    session: Session,
    row: ChatConnection,
    chat_user: str,
    display: str | None,
    code: str,
) -> str:
    try:
        identity = chat_links.claim(
            session,
            company_id=row.company_id,
            provider=row.provider,
            workspace_id=row.workspace_id,
            external_user_id=chat_user,
            display_name=display,
            code=code,
        )
    except chat_links.LinkRefused as exc:
        session.commit()  # the code is spent even when the link is refused
        return str(exc)
    user = session.get(User, identity.user_id)
    _audit(
        session,
        row,
        f"human:{user.email if user else identity.user_id}",
        "chat_account_linked",
        {"user_id": identity.user_id, "chat_user": chat_user},
        {"linked": True},
    )
    session.commit()
    return f"Linked: approvals you decide here are recorded as {user.full_name if user else 'you'}."


def _request(session: Session, row: ChatConnection, chat_user: str, text: str) -> str:
    """A request for the company's intake queue (issue #532), from a linked
    chat account with at least the Viewer role; the source is ``chat``."""
    text = text.strip()
    if not text:
        return "Usage: request <what you need>."
    identity = chat_links.identity_of(
        session, row.company_id, row.provider, row.workspace_id, chat_user
    )
    user = session.get(User, identity.user_id) if identity else None
    if user is None:
        return LINK_FIRST
    try:
        require_company_role(session, user, row.company_id, "Viewer")
    except HTTPException:
        return "Sending a request needs a role in this company in La Fabrique."
    title, _, rest = text.partition("\n")
    item = intake_queue.receive(
        session,
        row.company_id,
        "chat",
        title,
        text if rest else "",
        submitted_by=user.email,
        source_ref=f"{row.provider}:{row.workspace_id}",
    )
    session.commit()
    return f"Request #{item.id} is in the intake queue; a Manager decides it."


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def _slack_reply(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


@router.post("/slack/{public_id}/interactions")
@limiter.limit(_rate_limit)
async def slack_interactions(
    request: Request,
    public_id: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    body = await _body(request)
    row = _connection(session, "slack", public_id)
    try:
        verify_slack(
            conns.secret_of(row),
            request.headers.get("X-Slack-Request-Timestamp"),
            request.headers.get("X-Slack-Signature"),
            body,
        )
    except SignatureRefused as exc:
        raise _signature_refused("slack", row, exc) from exc
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    if "payload" in form:
        try:
            payload = json.loads(form["payload"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Malformed payload")
        return _slack_action(session, row, payload)
    return _slack_command(session, row, form)


def _slack_command(
    session: Session, row: ChatConnection, form: dict[str, str]
) -> Response:
    if not form.get("trigger_id"):
        raise HTTPException(status_code=400, detail="Not a Slack command")
    if not _first_time(session, row, f"command:{form['trigger_id']}"):
        return Response(status_code=200)
    if form.get("team_id") != row.workspace_id:
        return _slack_reply(
            "This Slack workspace is not the one connected to La Fabrique."
        )
    words = (form.get("text") or "").split()
    if len(words) == 2 and words[0].lower() == "link":
        return _slack_reply(
            _link(
                session, row, form.get("user_id") or "", form.get("user_name"), words[1]
            )
        )
    if words and words[0].lower() == "request":
        raw = (form.get("text") or "").strip()
        return _slack_reply(
            _request(session, row, form.get("user_id") or "", raw[len("request") :])
        )
    if len(words) >= 2 and words[0].lower() == "sendback":
        try:
            return _slack_reply(
                decide(
                    session,
                    row,
                    chat_user=form.get("user_id") or "",
                    message=chat_approvals.message_for(
                        session, row, short_code=words[1]
                    ),
                    decision="send_back",
                    reason=" ".join(words[2:]),
                )
            )
        except Refusal as refusal:
            return _slack_reply(str(refusal))
    return _slack_reply(
        "Usage: /fabrique link CODE (ask for the code in La Fabrique), "
        "/fabrique sendback CODE <reason>, or /fabrique request <what you need>."
    )


def _slack_action(
    session: Session, row: ChatConnection, payload: dict[str, Any]
) -> Response:
    trigger = str(payload.get("trigger_id") or "")
    if payload.get("type") != "block_actions" or not trigger:
        return Response(status_code=200)  # not an interaction this app sends
    if not _first_time(session, row, f"action:{trigger}"):
        return Response(status_code=200)
    team = (payload.get("team") or {}).get("id")
    user = str((payload.get("user") or {}).get("id") or "")
    channel = str((payload.get("channel") or {}).get("id") or row.channel or "")
    actions = payload.get("actions") or []
    action = actions[0] if actions and isinstance(actions[0], dict) else {}
    parsed = chat_messages.parse_button_value(action.get("value"))
    if team != row.workspace_id:
        text = "This Slack workspace is not the one connected to La Fabrique."
    elif (
        action.get("action_id") not in (chat_messages.APPROVE, chat_messages.SEND_BACK)
        or parsed is None
    ):
        text = "This button is not one La Fabrique sent."
    else:
        message_id, head, version = parsed
        message = chat_approvals.message_for(session, row, message_id=message_id)
        try:
            decide(
                session,
                row,
                chat_user=user,
                message=message,
                decision="approve"
                if action["action_id"] == chat_messages.APPROVE
                else "send_back",
                reason=None,
                head=head,
                run_version=version,
            )
            return Response(status_code=200)
        except Refusal as refusal:
            text = str(refusal)
    chat_approvals.queue(
        session, chat_approvals.EPHEMERAL, row.id, channel=channel, user=user, text=text
    )
    session.commit()
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


_TAGS = re.compile(r"<at>.*?</at>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def _teams_reply(text: str) -> JSONResponse:
    return JSONResponse({"type": "message", "text": text})


@router.post("/teams/{public_id}/messages")
@limiter.limit(_rate_limit)
async def teams_messages(
    request: Request,
    public_id: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    body = await _body(request)
    row = _connection(session, "teams", public_id)
    try:
        verify_teams(conns.secret_of(row), request.headers.get("Authorization"), body)
        activity = json.loads(body)
        if not isinstance(activity, dict):
            raise SignatureRefused("activity is not an object")
        check_activity_time(activity.get("timestamp"))
    except (SignatureRefused, ValueError) as exc:
        raise _signature_refused("teams", row, exc) from exc
    if activity.get("type") != "message" or not activity.get("id"):
        return _teams_reply("")
    if not _first_time(session, row, f"activity:{activity['id']}"):
        return _teams_reply("Already handled.")
    tenant = ((activity.get("channelData") or {}).get("tenant") or {}).get("id")
    if tenant != row.workspace_id:
        return _teams_reply(
            "This Teams tenant is not the one connected to La Fabrique."
        )
    sender = activity.get("from") or {}
    chat_user = str(sender.get("aadObjectId") or "")
    if not chat_user:
        return _teams_reply("Teams did not say who sent this message.")
    words = _TAGS.sub(" ", str(activity.get("text") or "")).split()
    command = words[0].lower() if words else ""
    if command == "link" and len(words) == 2:
        return _teams_reply(
            _link(session, row, chat_user, sender.get("name"), words[1])
        )
    if command in ("approve", "sendback") and len(words) >= 2:
        message = chat_approvals.message_for(session, row, short_code=words[1])
        try:
            return _teams_reply(
                decide(
                    session,
                    row,
                    chat_user=chat_user,
                    message=message,
                    decision="approve" if command == "approve" else "send_back",
                    reason=" ".join(words[2:]) if command == "sendback" else None,
                )
            )
        except Refusal as refusal:
            return _teams_reply(str(refusal))
    if command == "request":
        return _teams_reply(_request(session, row, chat_user, " ".join(words[1:])))
    return _teams_reply(
        "Usage: approve CODE, sendback CODE <reason>, link CODE (from La Fabrique), "
        "or request <what you need>."
    )
