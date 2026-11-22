"""A company's Slack app or Teams webhooks for approvals in chat (``docs/archive/design/CHAT_APPROVALS.md``).

One connection per company and provider, written by an Owner
(``routers/chat_approvals.py``). The credential that posts (Slack's bot token,
or the Teams incoming webhook URL, itself a bearer credential) and the key
that verifies inbound requests (Slack's signing secret, or the Teams outgoing
webhook's key) are encrypted with the Fernet key and never returned:
``describe`` says only whether each is set. The inbound URL carries an opaque
``public_id``, not a secret: it only selects which key verifies the request.

The Teams URL must be https, on a Microsoft webhook host, and resolve to
public addresses (``webhooks.check_url``, again before every post); Slack is
always called at ``https://slack.com/api/``.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import crypto
from models import ChatConnection
from sqlmodel import Session, select

from services.integrations import chat_client, webhooks

PROVIDERS = ("slack", "teams")
TIMEOUT_SECONDS = 10.0
MAX_DETAIL_CHARS = 300
# Where Teams incoming webhooks and Workflows triggers live.
TEAMS_HOST_SUFFIXES = (
    ".webhook.office.com",
    ".logic.azure.com",
    ".powerplatform.com",
    ".powerautomate.com",
)
_SLACK_TEAM = re.compile(r"^[TE][A-Z0-9]{2,20}$")
_SLACK_CHANNEL = re.compile(r"^[CGD][A-Z0-9]{2,20}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class InvalidConnection(ValueError):
    """The connection asked for cannot be saved or used; the message says why."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise InvalidConnection(f"provider: one of {', '.join(PROVIDERS)}")
    return provider


def get(session: Session, company_id: int, provider: str) -> ChatConnection | None:
    return session.exec(
        select(ChatConnection).where(
            ChatConnection.company_id == company_id,
            ChatConnection.provider == provider,
        )
    ).first()


def by_public_id(
    session: Session, provider: str, public_id: str
) -> ChatConnection | None:
    """The enabled connection an inbound URL names, or ``None``."""
    if not public_id or len(public_id) > 64:
        return None
    return session.exec(
        select(ChatConnection).where(
            ChatConnection.public_id == public_id,
            ChatConnection.provider == provider,
            ChatConnection.enabled.is_(True),  # type: ignore[union-attr]
        )
    ).first()


def enabled_for(session: Session, company_id: int) -> list[ChatConnection]:
    return list(
        session.exec(
            select(ChatConnection).where(
                ChatConnection.company_id == company_id,
                ChatConnection.enabled.is_(True),  # type: ignore[union-attr]
            )
        ).all()
    )


def inbound_url(row: ChatConnection) -> str:
    from config import settings

    base = (settings.PUBLIC_API_URL or "http://localhost:8000").rstrip("/")
    tail = "interactions" if row.provider == "slack" else "messages"
    return f"{base}/v1/integrations/{row.provider}/{row.public_id}/{tail}"


def describe(row: ChatConnection | None) -> dict[str, Any] | None:
    """What the API shows: everything but the credentials, which are a yes or no."""
    if row is None:
        return None
    return {
        "id": row.id,
        "company_id": row.company_id,
        "provider": row.provider,
        "workspace_id": row.workspace_id,
        "channel": row.channel,
        "enabled": row.enabled,
        "has_post_credential": bool(row.post_credential),
        "has_signing_secret": bool(row.signing_secret),
        "inbound_url": inbound_url(row),
        "tested_at": row.tested_at,
        "test_ok": row.test_ok,
        "test_detail": row.test_detail,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "updated_at": row.updated_at,
    }


def check_teams_url(url: str, *, resolve: bool = True) -> str:
    """*url* as a Teams incoming webhook, or ``InvalidConnection`` with the reason."""
    try:
        url = webhooks.check_url(url, resolve=False)
    except webhooks.InvalidWebhook as exc:
        raise InvalidConnection(str(exc).replace("url:", "post_credential:")) from exc
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith(TEAMS_HOST_SUFFIXES):
        raise InvalidConnection(
            "post_credential: not a Teams incoming webhook or Workflows URL"
        )
    if resolve:
        try:
            webhooks.check_url(url)
        except webhooks.InvalidWebhook as exc:
            raise InvalidConnection(
                str(exc).replace("url:", "post_credential:")
            ) from exc
    return url


def _check_fields(
    provider: str,
    workspace_id: str,
    channel: str | None,
    post_credential: str | None,
    signing_secret: str | None,
) -> tuple[str, str | None]:
    workspace_id = (workspace_id or "").strip()
    channel = (channel or "").strip() or None
    if provider == "slack":
        if not _SLACK_TEAM.match(workspace_id):
            raise InvalidConnection("workspace_id: a Slack team id such as T0123ABCD")
        if channel is None or not _SLACK_CHANNEL.match(channel):
            raise InvalidConnection("channel: a Slack channel id such as C0123ABCD")
        if post_credential is not None and not post_credential.startswith("xoxb-"):
            raise InvalidConnection("post_credential: a Slack bot token (xoxb-...)")
        if signing_secret is not None and len(signing_secret) < 16:
            raise InvalidConnection("signing_secret: Slack's signing secret")
    else:
        if not _GUID.match(workspace_id):
            raise InvalidConnection("workspace_id: the Microsoft Entra tenant id")
        channel = None
        if post_credential is not None:
            check_teams_url(post_credential)
        if signing_secret is not None:
            try:
                key = base64.b64decode(signing_secret, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise InvalidConnection(
                    "signing_secret: the outgoing webhook's security token (base64)"
                ) from exc
            if len(key) < 16:
                raise InvalidConnection("signing_secret: too short")
    return workspace_id, channel


def save(
    session: Session,
    company_id: int,
    provider: str,
    *,
    workspace_id: str,
    channel: str | None,
    post_credential: str | None,
    signing_secret: str | None,
    enabled: bool,
    actor: str,
) -> tuple[ChatConnection, list[str]]:
    """Create or change the connection; an omitted credential keeps the stored one.

    Returns the row and the names of the fields that changed (for the audit
    row; never a value).
    """
    check_provider(provider)
    if not crypto.is_encryption_enabled():
        raise InvalidConnection(
            "FERNET_SECRET_KEY is not set, so the credentials cannot be stored"
        )
    post_credential = (post_credential or "").strip() or None
    signing_secret = (signing_secret or "").strip() or None
    workspace_id, channel = _check_fields(
        provider, workspace_id, channel, post_credential, signing_secret
    )
    row = get(session, company_id, provider)
    if row is None:
        if post_credential is None or signing_secret is None:
            raise InvalidConnection(
                "post_credential and signing_secret: both required for a new connection"
            )
        row = ChatConnection(
            company_id=company_id,
            provider=provider,
            public_id=secrets.token_urlsafe(18),
            workspace_id=workspace_id,
            created_by=actor,
        )
    changed = [
        name
        for name, new in (
            ("workspace_id", workspace_id),
            ("channel", channel),
            ("enabled", enabled),
        )
        if getattr(row, name) != new or row.id is None
    ]
    row.workspace_id, row.channel, row.enabled = workspace_id, channel, enabled
    if post_credential is not None:
        row.post_credential = crypto.encrypt_value(post_credential)
        changed.append("post_credential")
    if signing_secret is not None:
        row.signing_secret = crypto.encrypt_value(signing_secret)
        changed.append("signing_secret")
    if changed:
        # A change of where or with what it posts makes the last test moot.
        row.tested_at = row.test_ok = row.test_detail = None
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, changed


def secret_of(row: ChatConnection) -> str | None:
    """The inbound verification key, decrypted; ``None`` when unusable."""
    if not row.signing_secret:
        return None
    try:
        return crypto.decrypt_value(row.signing_secret)
    except Exception:  # noqa: BLE001 - An undecryptable key verifies nothing: fail closed.
        return None


def credential_of(row: ChatConnection) -> str:
    """The posting credential, decrypted, or ``InvalidConnection``."""
    if not row.post_credential:
        raise InvalidConnection("no posting credential configured")
    try:
        return crypto.decrypt_value(row.post_credential)
    except Exception as exc:
        raise InvalidConnection("the posting credential cannot be decrypted") from exc


def record(row: ChatConnection, status: str, error: str | None) -> None:
    row.last_status = status
    row.last_error = (error or "")[:MAX_DETAIL_CHARS] or None


async def test(session: Session, row: ChatConnection) -> dict[str, Any]:
    """Call the platform with the stored credential; record and return what was observed."""
    observed: dict[str, Any] = {}
    try:
        credential = credential_of(row)
        if row.provider == "slack":
            answer = await chat_client.slack_call(
                "auth.test", credential, {}, TIMEOUT_SECONDS
            )
            observed = {
                "team_id": answer.get("team_id"),
                "bot_user_id": answer.get("user_id"),
            }
            if answer.get("team_id") != row.workspace_id:
                raise InvalidConnection(
                    "the bot token belongs to another Slack workspace"
                )
            await chat_client.slack_call(
                "chat.postMessage",
                credential,
                {"channel": row.channel, "text": "La Fabrique is connected."},
                TIMEOUT_SECONDS,
            )
        else:
            url = check_teams_url(credential)
            status = await chat_client.teams_post(
                url, {"text": "La Fabrique is connected."}, TIMEOUT_SECONDS
            )
            observed = {"status": status}
            if not 200 <= status < 300:
                raise InvalidConnection(f"Teams answered HTTP {status}")
        ok, detail = True, "connected"
    except (InvalidConnection, chat_client.ChatPostError) as exc:
        ok, detail = False, str(exc)[:MAX_DETAIL_CHARS]
    row.tested_at, row.test_ok, row.test_detail = _now(), ok, detail
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"ok": ok, "detail": detail, "observed": observed}
