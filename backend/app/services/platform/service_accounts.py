"""The service accounts inventory (issue #543; ``docs/design/SEGREGATION_OF_DUTIES.md``).

Every credential that acts for the company without a person at the keyboard,
with its owner, scope and age, for the Owner's access review: the API
tokens, the outbound webhook, the chat connections, the SSO client, the
model provider connections and the GitHub webhook bindings; and, for the
deployment, how the backend authenticates to NATS and whether the LLM
proxy's internal token is set.

Names, scopes and dates only, never a value: no token, secret, key, password
or full URL (a webhook shows its host). The daemon's own NATS credential is
on the computer that runs it; the backend sees only its own, so the inventory
names the method and user, never the secret.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import nats_auth
from config import settings
from models import (
    ApiToken,
    ChatConnection,
    CompanyWebhook,
    GitHubRepoBinding,
    ProviderConnection,
    SsoConnection,
)
from sqlmodel import Session, select

from services.platform import api_tokens


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def age_days(created: datetime | None, now: datetime) -> int | None:
    """Pure: whole days since *created*; ``None`` when unknown."""
    created = _naive(created)
    return None if created is None else max(0, (now - created).days)


def _host(url: str | None) -> str | None:
    try:
        return urlsplit(url or "").hostname or None
    except ValueError:
        return None


def _row(kind, name, owner, scope, created, now, status, **extra) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "owner": owner,
        "scope": scope,
        "created_at": _naive(created).isoformat() if created else None,
        "age_days": age_days(created, now),
        "status": status,
        **extra,
    }


def _token_status(token: ApiToken) -> str:
    if token.revoked_at is not None:
        return "revoked"
    return "expired" if api_tokens.expired(token) else "active"


def company_accounts(session: Session, company_id: int, now: datetime) -> list[dict]:
    out = []
    for t in api_tokens.list_tokens(session, company_id):
        out.append(
            _row(
                "api_token",
                t.name,
                t.created_by,
                f"{t.role}; {', '.join(t.scopes or []) or 'all scopes'}",
                t.created_at,
                now,
                _token_status(t),
                id=t.id,
                last_used_at=_naive(t.last_used_at).isoformat()
                if t.last_used_at
                else None,
                expires_at=_naive(t.expires_at).isoformat() if t.expires_at else None,
            )
        )
    for w in session.exec(
        select(CompanyWebhook).where(CompanyWebhook.company_id == company_id)
    ).all():
        out.append(
            _row(
                "webhook",
                _host(w.url) or "webhook",
                None,
                ", ".join(w.events or []) or "all events",
                w.created_at,
                now,
                "active" if w.enabled else "disabled",
            )
        )
    for c in session.exec(
        select(ChatConnection).where(ChatConnection.company_id == company_id)
    ).all():
        out.append(
            _row(
                "chat_connection",
                c.provider,
                c.created_by,
                "post approvals, take decisions",
                c.created_at,
                now,
                "active" if c.enabled else "disabled",
            )
        )
    for s in session.exec(
        select(SsoConnection).where(SsoConnection.company_id == company_id)
    ).all():
        out.append(
            _row(
                "sso_client",
                _host(s.issuer) or s.preset,
                s.created_by,
                f"sign-in; joins as {s.default_role}"
                if s.auto_join
                else "sign-in; invited members only",
                s.created_at,
                now,
                "active" if s.enabled else "disabled",
                has_secret=bool(s.client_secret),
            )
        )
    for p in session.exec(
        select(ProviderConnection).where(ProviderConnection.company_id == company_id)
    ).all():
        out.append(
            _row(
                "model_provider",
                p.name,
                p.created_by,
                p.provider,
                p.created_at,
                now,
                "active" if p.enabled else "disabled",
                has_secret=bool(p.api_key),
            )
        )
    for g in session.exec(
        select(GitHubRepoBinding).where(GitHubRepoBinding.company_id == company_id)
    ).all():
        out.append(
            _row(
                "github_webhook",
                f"{g.owner}/{g.repo}",
                None,
                f"inbound webhook; {g.inclusion_rule}",
                g.created_at,
                now,
                "active",
            )
        )
    return out


def deployment_accounts(env: dict[str, str] | None = None) -> list[dict]:
    """The deployment's service credentials: presence and names, never values."""
    env = dict(os.environ) if env is None else env
    try:
        method = nats_auth.auth_method(env)
    except nats_auth.NATSConfigError:
        method = "misconfigured"
    return [
        {
            "kind": "nats_backend",
            "name": (env.get("NATS_USER") or "").strip() or None,
            "scope": "the backend's subjects on the bus",
            "method": method,
            "tls": (env.get("NATS_TLS") or "").strip().lower()
            in {"1", "true", "yes", "on"},
            "client_certificate": bool((env.get("NATS_TLS_CERT") or "").strip()),
        },
        {
            "kind": "nats_daemon",
            "name": None,
            "scope": "the daemon's subjects; its credential stays on its computer",
            "method": "not observed here",
        },
        {
            "kind": "llm_proxy_token",
            "name": "LLM_PROXY_INTERNAL_TOKEN",
            "scope": "backend to LLM proxy",
            "present": bool(settings.LLM_PROXY_INTERNAL_TOKEN),
        },
    ]


def inventory(session: Session, company_id: int, now: datetime | None = None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return {
        "company_id": company_id,
        "generated_at": now.isoformat(),
        "company": company_accounts(session, company_id, now),
        "deployment": deployment_accounts(),
    }
