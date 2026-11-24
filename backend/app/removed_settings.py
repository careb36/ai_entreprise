"""Settings that no longer do anything (``config.Settings`` keeps them declared
so an old ``.env`` still loads), and the startup warning that names those an
environment still sets (``lifecycle``)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Settings

# Settings that no longer do anything, kept declared so an old .env still loads.
REMOVED_SETTINGS: dict[str, str] = {
    name: "the console assistants were removed (issue #750); the team's Architect gives the second opinion"
    for name in (
        "CONSOLE_ASSISTANTS_ENABLED",
        "CONSOLE_ASSISTANT_TIMEOUT_SECONDS",
        "CONSOLE_ASSISTANT_STRATEGY",
        "CONSOLE_ASSISTANT_ALLOWED_ROLES",
        "COPILOT_CLI_COMMAND_TEMPLATE",
        "CLAUDE_CLI_COMMAND_TEMPLATE",
        "CLAUDE_CLI_ALLOWED_TOOLS",
        "CLAUDE_CLI_APPEND_SYSTEM_PROMPT",
        "CLAUDE_CLI_HIGH_VALUE_ROLES",
    )
}


def removed_settings_in_use(current: "Settings") -> list[str]:
    """The removed settings this environment or .env still sets, one warning line each."""
    return [
        f"{name} is set but no longer does anything: {why}. Remove it from the environment or .env."
        for name, why in REMOVED_SETTINGS.items()
        if name in current.model_fields_set
    ]
