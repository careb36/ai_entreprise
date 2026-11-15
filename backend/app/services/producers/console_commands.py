"""What the console assistants are asked, and how their commands are written.

The assistants' labels and keys, the prompt they get for a task and why each
is consulted, the reset time read from a Claude CLI quota message, and the
shell command rendered from a template with the prompt safely quoted and the
host's binary fallbacks. Pure: no database, no daemon. ``console_assistants``
re-exports every name.
"""

import re
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any

from config import settings

CLAUDE_CLI_ALLOWED_TOOLS = settings.CLAUDE_CLI_ALLOWED_TOOLS
CLAUDE_CLI_APPEND_SYSTEM_PROMPT = settings.CLAUDE_CLI_APPEND_SYSTEM_PROMPT
CLAUDE_LABEL = "Claude CLI"
COPILOT_LABEL = "GitHub Copilot CLI"
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


def build_prompt(item: dict[str, Any], agent: dict[str, Any], sprint_name: str) -> str:
    """Build a concise prompt for external console assistants."""
    return (
        f"Repository task for {agent.get('role', 'Developer')} in sprint '{sprint_name}'.\n"
        f"Task: {item.get('title', '')}\n"
        f"Description: {item.get('description', '')}\n"
        f"Priority: {item.get('priority', 'medium')}\n\n"
        "Provide concise actionable advice for implementing this task in the current repository. "
        "Prefer incremental changes, mention relevant existing code or GitHub/MCP-backed ideas when useful, and avoid suggesting destructive rewrites."
    )


def assistant_purpose(item: dict[str, Any], agent: dict[str, Any], label: str) -> str:
    """Describe why an external assistant is being consulted for the current task."""
    role = (agent.get("role") or "Developer").strip()
    priority = (item.get("priority") or "medium").strip().lower()
    task_text = f"{item.get('title', '')}\n{item.get('description', '')}".lower()

    if label == CLAUDE_LABEL:
        if role in {"Architect", "PO", "ScrumMaster"}:
            return "planning and architecture advice"
        if role == "QA" or any(
            token in task_text for token in ("validate", "review", "security", "risk")
        ):
            return "review and risk analysis advice"
        return "high-value implementation guidance"

    if priority in {"high", "critical"}:
        return "high-priority implementation guidance"
    return "incremental implementation guidance"


def assistant_key_for_label(label: str) -> str:
    if label == CLAUDE_LABEL:
        return "claude"
    if label == COPILOT_LABEL:
        return "copilot"
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "assistant"


def parse_claude_reset_time(text: str) -> datetime | None:
    """Best-effort parse for reset times embedded in Claude CLI quota messages."""
    now = datetime.now().astimezone()
    for pattern in CLAUDE_RESET_TIME_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        raw_time = (match.group("time") or "").strip().upper().replace(" ", "")
        formats = ["%I:%M%p", "%H:%M"]
        for fmt in formats:
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


def render_command(template: str, prompt: str, workspace_root: str) -> str | None:
    """Render a shell command template with a safely quoted prompt."""
    normalized = (template or "").strip()
    if not normalized:
        return None
    quoted_values = {
        "prompt": shlex.quote(prompt),
        "workspace_root": shlex.quote(workspace_root),
        "allowed_tools": shlex.quote(CLAUDE_CLI_ALLOWED_TOOLS),
        "system_prompt": shlex.quote(CLAUDE_CLI_APPEND_SYSTEM_PROMPT),
    }
    if any(
        token in normalized
        for token in (
            "{prompt}",
            "{workspace_root}",
            "{allowed_tools}",
            "{system_prompt}",
        )
    ):
        return normalized.format(**quoted_values)
    return f"{normalized} {quoted_values['prompt']}"


def replace_command_binary(command: str, original: str, replacement: str) -> str:
    """Swap the first command token while preserving the rest of the shell command."""
    if command == original:
        return replacement
    prefix = f"{original} "
    if command.startswith(prefix):
        return f"{replacement}{command[len(original) :]}"
    return command


def wrap_command_with_fallback(label: str, command: str) -> str:
    """Add host-specific binary fallback paths for known assistants."""
    if label == COPILOT_LABEL:
        fallback = '"$HOME/Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot"'
        fallback_command = replace_command_binary(command, "copilot", fallback)
        return (
            f"if [ -x {fallback} ]; then {fallback_command}; "
            f"elif command -v copilot >/dev/null 2>&1; then {command}; "
            f"else {command}; fi"
        )

    if label == CLAUDE_LABEL:
        fallback = '"$HOME/.local/bin/claude"'
        fallback_command = replace_command_binary(command, "claude", fallback)
        return (
            f"if command -v claude >/dev/null 2>&1; then {command}; "
            f"elif [ -x {fallback} ]; then {fallback_command}; "
            f"else {command}; fi"
        )

    return command
