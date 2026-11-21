"""When a console producer's CLI may run again: the per-company cooldown after
a quota stop or a missing binary (``AssistantRuntimeState``).

A console producer (``console_producer``, a company's explicit choice of a
terminal coding agent) that hits its provider's quota, or whose CLI is not in
the sandbox, cools down for that company so the next tasks do not start a run
that cannot work; the deadline is persisted so a restart does not retry a dead
CLI. Moved here from ``console_assistants`` when the console assistants, the
CLIs that advised tasks, were removed (issue #750): the cooldowns were always
the producers' too.

The quota messages and the reset time are read from the Claude CLI's output
(the other CLIs' are ``console_clis.QUOTA_PATTERNS``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from config import settings
from database import engine
from models import AssistantRuntimeState
from sqlmodel import Session, select

# How long a quota stop cools a CLI down when its message names no reset time.
CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES = max(
    1, int(settings.CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES)
)
# How long a CLI missing from the sandbox cools down.
ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES = settings.ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES
CLAUDE_QUOTA_PATTERNS = (
    "usage limit",
    "rate limit",
    "quota",
    "too many requests",
)
ASSISTANT_UNAVAILABLE_PATTERNS = (
    "not found",
    "cannot find github copilot cli",
    "no such file or directory",
    "command not found",
)
CLAUDE_RESET_TIME_PATTERNS = (
    re.compile(
        r"(?:available|retry|resets?)\s+(?:at|after)\s+(?P<time>\d{1,2}:\d{2}\s?(?:am|pm)?)",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<time>\d{1,2}:\d{2}\s?(?:am|pm))", re.IGNORECASE),
)


def normalize_utc(value: datetime | None) -> datetime | None:
    """Normalize datetimes loaded from the DB to timezone-aware UTC values."""
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_claude_reset_time(text: str) -> datetime | None:
    """Best-effort parse for reset times embedded in Claude CLI quota messages."""
    now = datetime.now().astimezone()
    for pattern in CLAUDE_RESET_TIME_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        raw_time = (match.group("time") or "").strip().upper().replace(" ", "")
        for fmt in ("%I:%M%p", "%H:%M"):
            try:
                parsed = datetime.strptime(raw_time, fmt).replace(tzinfo=now.tzinfo)
            except ValueError:
                continue
            candidate = now.replace(
                hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(UTC)
    return None


def _get_runtime_state(
    company_id: int, assistant_key: str
) -> AssistantRuntimeState | None:
    with Session(engine) as session:
        return session.exec(
            select(AssistantRuntimeState).where(
                AssistantRuntimeState.company_id == company_id,
                AssistantRuntimeState.assistant_key == assistant_key,
            )
        ).first()


def set_runtime_state(
    company_id: int,
    assistant_key: str,
    *,
    cooldown_until: datetime | None,
    last_error: str | None = None,
) -> datetime | None:
    """Persist a cooldown so it survives backend restarts."""
    normalized_cooldown = normalize_utc(cooldown_until)
    with Session(engine) as session:
        state = session.exec(
            select(AssistantRuntimeState).where(
                AssistantRuntimeState.company_id == company_id,
                AssistantRuntimeState.assistant_key == assistant_key,
            )
        ).first()
        if not state:
            state = AssistantRuntimeState(
                company_id=company_id, assistant_key=assistant_key
            )
        state.cooldown_until = normalized_cooldown
        state.last_error = (last_error or "")[:1000] or None
        state.updated_at = datetime.now(UTC)
        session.add(state)
        session.commit()
    return normalized_cooldown


def cooldown_until(company_id: int, assistant_key: str) -> datetime | None:
    """The persisted cooldown deadline, cleared once it has passed."""
    now = datetime.now(UTC)
    state = _get_runtime_state(company_id, assistant_key)
    deadline = normalize_utc(state.cooldown_until) if state else None
    if deadline and deadline <= now:
        set_runtime_state(
            company_id, assistant_key, cooldown_until=None, last_error=None
        )
        return None
    return deadline
